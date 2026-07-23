"""
Generates a self-contained HTML data quality report for one run: a pass/fail
summary, the quality score, issues broken down by category with sample rows,
which fixes were auto-applied, and a trend chart of score-over-time pulled
from history.py. The chart is rendered as a base64 PNG and embedded directly
in the HTML so the report is a single file - nothing to attach, nothing to
break if it's moved or emailed.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .scoring import ScoreResult
from .validation import Issue

CATEGORY_LABELS = {
    "schema": "Schema",
    "completeness": "Completeness",
    "uniqueness": "Uniqueness",
    "format": "Format / Range",
    "outlier": "Outliers",
    "referential": "Referential Integrity",
    "freshness": "Freshness",
}


def _score_color(score: float) -> str:
    if score >= 90:
        return "#1a7f37"
    if score >= 70:
        return "#9a6700"
    return "#cf222e"


def render_trend_chart(history: list[dict], dataset_name: str) -> str:
    """Returns a base64-encoded PNG data URI of score-over-time, or empty
    string if there's not enough history yet to plot."""
    if len(history) < 2:
        return ""

    dates = [datetime.fromisoformat(h["run_timestamp"]) for h in history]
    scores = [h["score"] for h in history]

    fig, ax = plt.subplots(figsize=(7, 2.6), dpi=130)
    ax.plot(dates, scores, marker="o", linewidth=2, color="#2f5fd6", markersize=5)
    ax.axhline(80, color="#cf222e", linestyle="--", linewidth=1, alpha=0.6)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Score")
    ax.set_title(f"Data Quality Score - {dataset_name} (last {len(history)} runs)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _issue_rows_html(issues: list[Issue]) -> str:
    if not issues:
        return "<p class='muted'>No issues found. This run is clean.</p>"

    rows = []
    for issue in sorted(issues, key=lambda i: (i.severity != "critical", i.category)):
        badge_class = "badge-critical" if issue.severity == "critical" else "badge-warning"
        sample = ""
        if issue.sample_rows:
            sample_str = ", ".join(str(s) for s in issue.sample_rows[:5])
            sample = f"<div class='sample'>Examples: {sample_str}</div>"
        rows.append(f"""
        <tr>
            <td><span class="badge {badge_class}">{issue.severity}</span></td>
            <td>{CATEGORY_LABELS.get(issue.category, issue.category)}</td>
            <td>{issue.column or '-'}</td>
            <td>{issue.row_count or '-'}</td>
            <td>{issue.message}{sample}</td>
        </tr>""")
    return f"""
    <table class="issues">
        <thead><tr><th>Severity</th><th>Category</th><th>Column</th><th>Rows</th><th>Details</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>"""


def _fixes_html(fixes_applied: list[str], rows_removed: int) -> str:
    items = "".join(f"<li>{f}</li>" for f in fixes_applied)
    return f"""
    <ul class="fixes">{items}</ul>
    <p class="muted">{rows_removed} row(s) removed as exact duplicates.
    Everything else above was left untouched and reported instead of guessed at -
    see the README for why.</p>"""


def render_report(
    dataset_name: str,
    source_file: str,
    run_timestamp: datetime,
    row_count: int,
    issues: list[Issue],
    score_result: ScoreResult,
    fixes_applied: list[str],
    rows_removed: int,
    history: list[dict],
    out_path: Path,
) -> Path:
    score = score_result.score
    status_label = "CRITICAL" if score_result.critical_fail else ("PASS" if score >= 80 else "NEEDS REVIEW")
    status_class = "status-critical" if score_result.critical_fail else ("status-pass" if score >= 80 else "status-warn")

    category_summary_rows = "".join(
        f"<tr><td>{CATEGORY_LABELS.get(cat, cat)}</td><td>{count}</td>"
        f"<td>-{score_result.category_deductions.get(cat, 0):.1f}</td></tr>"
        for cat, count in sorted(score_result.category_issue_counts.items())
    ) or "<tr><td colspan='3' class='muted'>No issues in any category.</td></tr>"

    chart_data_uri = render_trend_chart(history, dataset_name)
    chart_html = (
        f'<img src="{chart_data_uri}" alt="Quality score trend" class="trend-chart"/>'
        if chart_data_uri else
        "<p class='muted'>Not enough history yet to show a trend (need at least 2 runs).</p>"
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Data Quality Report - {dataset_name} - {run_timestamp.strftime('%Y-%m-%d')}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
          background: #f6f8fa; color: #1f2328; margin: 0; padding: 32px; }}
  .container {{ max-width: 980px; margin: 0 auto; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 10px;
           padding: 24px 28px; margin-bottom: 20px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
  .subtitle {{ color: #59636e; font-size: 13px; margin-bottom: 8px; }}
  .top-row {{ display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }}
  .score-circle {{ font-size: 42px; font-weight: 700; color: {_score_color(score)}; }}
  .score-label {{ font-size: 12px; color: #59636e; text-transform: uppercase; letter-spacing: .04em; }}
  .status {{ display: inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 600;
             font-size: 12px; letter-spacing: .03em; }}
  .status-pass {{ background: #dafbe1; color: #1a7f37; }}
  .status-warn {{ background: #fff8c5; color: #9a6700; }}
  .status-critical {{ background: #ffebe9; color: #cf222e; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; }}
  th {{ color: #59636e; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
  .badge {{ padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
  .badge-critical {{ background: #ffebe9; color: #cf222e; }}
  .badge-warning {{ background: #fff8c5; color: #9a6700; }}
  .sample {{ font-size: 11.5px; color: #59636e; margin-top: 3px; font-family: ui-monospace, monospace; }}
  .muted {{ color: #59636e; font-size: 13px; }}
  .fixes li {{ margin-bottom: 4px; font-size: 13.5px; }}
  .trend-chart {{ width: 100%; max-width: 760px; }}
  h2 {{ font-size: 15px; margin: 0 0 12px 0; color: #1f2328; }}
  footer {{ color: #8b949e; font-size: 12px; text-align: center; margin-top: 28px; }}
</style>
</head>
<body>
<div class="container">

  <div class="card">
    <h1>Data Quality Report</h1>
    <div class="subtitle">Dataset: <strong>{dataset_name}</strong> &middot; Source: {source_file}
      &middot; Run at {run_timestamp.strftime('%Y-%m-%d %H:%M')} &middot; {row_count} rows</div>
    <div class="top-row">
      <div>
        <div class="score-label">Quality Score</div>
        <div class="score-circle">{score:.1f}<span style="font-size:20px;color:#8b949e;">/100</span></div>
      </div>
      <div><span class="status {status_class}">{status_label}</span></div>
    </div>
  </div>

  <div class="card">
    <h2>Score Trend</h2>
    {chart_html}
  </div>

  <div class="card">
    <h2>Issue Summary by Category</h2>
    <table>
      <thead><tr><th>Category</th><th>Issue Count</th><th>Score Impact</th></tr></thead>
      <tbody>{category_summary_rows}</tbody>
    </table>
  </div>

  <div class="card">
    <h2>Auto-Remediation Applied</h2>
    {_fixes_html(fixes_applied, rows_removed)}
  </div>

  <div class="card">
    <h2>All Flagged Issues ({len(issues)})</h2>
    {_issue_rows_html(issues)}
  </div>

  <footer>Generated automatically by Data Quality Monitor &middot; {run_timestamp.isoformat()}</footer>
</div>
</body>
</html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path