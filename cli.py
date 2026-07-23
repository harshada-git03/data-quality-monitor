"""
Command-line entrypoint for the Data Quality Monitor.

Usage:
    python cli.py simulate --severity clean
    python cli.py simulate --severity major --date 2026-07-22
    python cli.py run
    python cli.py status
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dqm import history
from dqm.pipeline import run_pipeline
from dqm.simulator import simulate_one_day

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "rules.yaml"
INCOMING_DIR = ROOT / "data" / "incoming"
ARCHIVE_DIR = ROOT / "data" / "archive"
REFERENCE_DIR = ROOT / "data" / "reference"
REPORTS_DIR = ROOT / "reports" / "html"
HISTORY_DB = ROOT / "reports" / "history.db"


def cmd_simulate(args: argparse.Namespace) -> None:
    date = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    path = simulate_one_day(
        INCOMING_DIR, REFERENCE_DIR, date=date, severity=args.severity, n=args.rows, seed=args.seed
    )
    print(f"Simulated a new daily export: {path}")


def cmd_run(args: argparse.Namespace) -> None:
    run_timestamp = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now()
    results = run_pipeline(
        CONFIG_PATH, INCOMING_DIR, ARCHIVE_DIR, REFERENCE_DIR, REPORTS_DIR, HISTORY_DB,
        run_timestamp=run_timestamp, dry_run=args.dry_run,
    )
    if not results:
        print("No new files found in data/incoming/. Nothing to process.")
        return

    exit_code = 0
    for r in results:
        status = "CRITICAL" if r.score_result.critical_fail else ("PASS" if r.score_result.score >= 80 else "NEEDS REVIEW")
        print(f"[{status:12}] {r.dataset_name:10} score={r.score_result.score:6}/100  "
              f"issues={len(r.issues):3}  rows={r.row_count:5}  report={r.report_path}")
        if r.alert_result.reason:
            channels = []
            if r.alert_result.slack_sent:
                channels.append("Slack")
            if r.alert_result.email_sent:
                channels.append("email")
            sent_str = f"sent via {', '.join(channels)}" if channels else "not sent (no channels configured)"
            print(f"    ALERT: {r.alert_result.reason} ({sent_str})")
        if r.score_result.critical_fail:
            exit_code = 1

    sys.exit(exit_code)  # non-zero exit lets CI mark the run as failed on critical issues


def cmd_status(args: argparse.Namespace) -> None:
    hist = history.get_history(HISTORY_DB, dataset_name=args.dataset, limit=args.limit)
    if not hist:
        print("No run history yet. Run `python cli.py simulate` then `python cli.py run` first.")
        return
    print(f"{'Date':20} {'Dataset':10} {'Score':>7}  {'Critical':>8}  {'Issues':>7}  Source")
    for h in hist:
        print(f"{h['run_timestamp']:20} {h['dataset_name']:10} {h['score']:7.1f}  "
              f"{str(h['critical_fail']):>8}  {h['issue_count']:7}  {h['source_file']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated Data Quality Monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sim = sub.add_parser("simulate", help="Generate a fake daily orders export with injected issues.")
    p_sim.add_argument("--severity", choices=["clean", "minor", "major", "critical"], default="clean")
    p_sim.add_argument("--date", help="YYYY-MM-DD, defaults to today.")
    p_sim.add_argument("--rows", type=int, default=300)
    p_sim.add_argument("--seed", type=int, default=None)
    p_sim.set_defaults(func=cmd_simulate)

    p_run = sub.add_parser("run", help="Process every new file in data/incoming/.")
    p_run.add_argument("--date", help="Stamp this run with a specific date (YYYY-MM-DD), defaults to now.")
    p_run.add_argument("--dry-run", action="store_true", help="Don't archive processed files.")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="Show recent run history.")
    p_status.add_argument("--dataset", default="orders")
    p_status.add_argument("--limit", type=int, default=30)
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()