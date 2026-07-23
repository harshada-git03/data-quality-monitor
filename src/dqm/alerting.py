"""
Alerting. Fires when the quality score drops below threshold, or a critical
check fails. Both channels are entirely env-var driven and no-op cleanly if
their env vars aren't set - this is what lets the pipeline run end-to-end in
a fresh clone or a CI job with zero configuration, while still being "real"
the moment someone drops in actual Slack/SMTP credentials.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from .scoring import ScoreResult


@dataclass
class AlertResult:
    slack_sent: bool
    email_sent: bool
    reason: str | None
    skipped_reasons: list[str]


def _should_alert(score_result: ScoreResult, alerting_cfg: dict) -> tuple[bool, str | None]:
    threshold = alerting_cfg.get("quality_score_threshold", 80)
    if score_result.critical_fail and alerting_cfg.get("always_alert_on_critical", True):
        return True, f"Critical check failed (score {score_result.score}/100)."
    if score_result.score < threshold:
        return True, f"Quality score {score_result.score} is below threshold {threshold}."
    return False, None


def _build_message(dataset_name: str, source_file: str, score_result: ScoreResult, reason: str, report_path: str) -> str:
    top_categories = sorted(
        score_result.category_issue_counts.items(), key=lambda kv: -kv[1]
    )[:3]
    top_str = ", ".join(f"{cat} ({n})" for cat, n in top_categories) or "none"
    return (
        f"Data Quality Alert - {dataset_name}\n"
        f"File: {source_file}\n"
        f"Reason: {reason}\n"
        f"Score: {score_result.score}/100\n"
        f"Top issue categories: {top_str}\n"
        f"Full report: {report_path}"
    )


def send_slack_alert(webhook_url: str, message: str) -> bool:
    try:
        resp = requests.post(webhook_url, json={"text": message}, timeout=10)
        return resp.status_code < 300
    except requests.RequestException:
        return False


def send_email_alert(smtp_cfg: dict, subject: str, message: str) -> bool:
    try:
        host = smtp_cfg["host"]
        port = int(smtp_cfg.get("port") or 587)
        user = smtp_cfg.get("user")
        password = smtp_cfg.get("password")
        from_addr = smtp_cfg["from_addr"]
        to_addr = smtp_cfg["to_addr"]

        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain"))

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception:
        return False


def maybe_alert(
    dataset_name: str,
    source_file: str,
    score_result: ScoreResult,
    alerting_cfg: dict,
    report_path: str,
) -> AlertResult:
    should_fire, reason = _should_alert(score_result, alerting_cfg)
    if not should_fire:
        return AlertResult(slack_sent=False, email_sent=False, reason=None,
                            skipped_reasons=["Score above threshold and no critical failure."])

    message = _build_message(dataset_name, source_file, score_result, reason, report_path)
    skipped = []
    slack_sent = False
    email_sent = False

    slack_env_var = alerting_cfg.get("slack", {}).get("enabled_env_var", "SLACK_WEBHOOK_URL")
    webhook_url = os.environ.get(slack_env_var)
    if webhook_url:
        slack_sent = send_slack_alert(webhook_url, message)
        if not slack_sent:
            skipped.append("Slack webhook call failed (see logs).")
    else:
        skipped.append(f"Slack skipped: {slack_env_var} not set.")

    email_cfg = alerting_cfg.get("email", {})
    smtp_host = os.environ.get(email_cfg.get("smtp_host_env_var", "DQM_SMTP_HOST"))
    if smtp_host:
        smtp_cfg = {
            "host": smtp_host,
            "port": os.environ.get(email_cfg.get("smtp_port_env_var", "DQM_SMTP_PORT")),
            "user": os.environ.get(email_cfg.get("smtp_user_env_var", "DQM_SMTP_USER")),
            "password": os.environ.get(email_cfg.get("smtp_pass_env_var", "DQM_SMTP_PASS")),
            "from_addr": os.environ.get(email_cfg.get("from_env_var", "DQM_ALERT_FROM"), "dqm@localhost"),
            "to_addr": os.environ.get(email_cfg.get("to_env_var", "DQM_ALERT_TO")),
        }
        if smtp_cfg["to_addr"]:
            email_sent = send_email_alert(smtp_cfg, f"[Data Quality Alert] {dataset_name}", message)
            if not email_sent:
                skipped.append("Email send failed (see logs).")
        else:
            skipped.append("Email skipped: no recipient address configured.")
    else:
        skipped.append("Email skipped: SMTP host not set.")

    return AlertResult(slack_sent=slack_sent, email_sent=email_sent, reason=reason, skipped_reasons=skipped)