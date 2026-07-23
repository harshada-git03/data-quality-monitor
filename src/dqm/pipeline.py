"""
The pipeline orchestrator. This is the "zero manual intervention" part: given
an incoming folder and a config, it discovers new files, figures out which
dataset config each belongs to, remediates what's safe, validates what's
left, scores the result, writes an HTML report, logs it to history, and
fires an alert if needed - then archives the file so it's never reprocessed.

Reference datasets (like `customers`, which orders' foreign key points at)
are loaded separately from a stable reference/ folder rather than flowing
through the incoming/archive cycle, since they're dimension tables, not daily
drops.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from . import alerting, history, ingestion, remediation, reporting, scoring, validation


@dataclass
class DatasetRunResult:
    dataset_name: str
    source_file: str
    row_count: int
    issues: list[validation.Issue]
    score_result: scoring.ScoreResult
    fixes_applied: list[str]
    rows_removed: int
    report_path: Path
    alert_result: alerting.AlertResult


def load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_reference_datasets(reference_dir: Path, dataset_configs: dict) -> dict:
    """Loads dimension tables (e.g. customers) that other datasets' foreign
    keys point at. These live in reference/ and are NOT archived/consumed -
    they're read fresh every run."""
    reference_dfs = {}
    for name, cfg in dataset_configs.items():
        candidates = list(reference_dir.glob(cfg["file_glob"])) if reference_dir.exists() else []
        if candidates:
            reference_dfs[name] = ingestion.load_any(candidates[0])
    return reference_dfs


def run_pipeline(
    config_path: Path,
    incoming_dir: Path,
    archive_dir: Path,
    reference_dir: Path,
    reports_dir: Path,
    history_db_path: Path,
    run_timestamp: datetime | None = None,
    dry_run: bool = False,
) -> list[DatasetRunResult]:
    """Processes every new file sitting in incoming_dir. Returns one
    DatasetRunResult per file processed. `dry_run=True` skips archiving, so
    the same demo files can be reprocessed while testing."""
    cfg = load_config(config_path)
    run_timestamp = run_timestamp or datetime.now()
    dataset_configs = cfg["datasets"]
    scoring_cfg = cfg["scoring"]
    alerting_cfg = cfg["alerting"]

    reference_dfs = load_reference_datasets(reference_dir, dataset_configs)

    new_files = ingestion.find_new_files(incoming_dir, archive_dir)
    results: list[DatasetRunResult] = []

    for path in new_files:
        dataset_name = ingestion.match_dataset(path.name, dataset_configs)
        if dataset_name is None:
            continue  

        dataset_cfg = dataset_configs[dataset_name]

        raw_df = ingestion.load_any(path)
        remediated = remediation.auto_remediate(raw_df, dataset_cfg)

        issues = validation.run_all_checks(
            remediated.df, dataset_cfg, reference_dfs=reference_dfs, run_date=run_timestamp
        )
        score_result = scoring.score_run(issues, scoring_cfg)

        report_path = reports_dir / f"{dataset_name}_{run_timestamp.strftime('%Y-%m-%d_%H%M%S')}.html"
        hist = history.get_history(history_db_path, dataset_name=dataset_name)
        reporting.render_report(
            dataset_name=dataset_name,
            source_file=path.name,
            run_timestamp=run_timestamp,
            row_count=len(remediated.df),
            issues=issues,
            score_result=score_result,
            fixes_applied=remediated.fixes_applied,
            rows_removed=remediated.rows_removed,
            history=hist,
            out_path=report_path,
        )

        alert_result = alerting.maybe_alert(
            dataset_name=dataset_name,
            source_file=path.name,
            score_result=score_result,
            alerting_cfg=alerting_cfg,
            report_path=str(report_path),
        )

        history.record_run(
            history_db_path,
            history.RunRecord(
                run_timestamp=run_timestamp.isoformat(),
                dataset_name=dataset_name,
                source_file=path.name,
                row_count=len(remediated.df),
                score=score_result.score,
                critical_fail=score_result.critical_fail,
                issue_count=len(issues),
                category_issue_counts=score_result.category_issue_counts,
                fixes_applied=remediated.fixes_applied,
                report_path=str(report_path),
            ),
        )

        if not dry_run:
            ingestion.archive_file(path, archive_dir)

        results.append(DatasetRunResult(
            dataset_name=dataset_name,
            source_file=path.name,
            row_count=len(remediated.df),
            issues=issues,
            score_result=score_result,
            fixes_applied=remediated.fixes_applied,
            rows_removed=remediated.rows_removed,
            report_path=report_path,
            alert_result=alert_result,
        ))

    return results