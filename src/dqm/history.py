"""
Historical run log, backed by SQLite (a single file, no server required).
Every pipeline run appends one row per dataset here. This is what powers the
"quality score by day" trend chart and lets a stakeholder ask "has this been
getting worse?" without re-running anything.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    score REAL NOT NULL,
    critical_fail INTEGER NOT NULL,
    issue_count INTEGER NOT NULL,
    category_issue_counts TEXT NOT NULL,   -- JSON blob
    fixes_applied TEXT NOT NULL,           -- JSON blob
    report_path TEXT
);
"""


@dataclass
class RunRecord:
    run_timestamp: str
    dataset_name: str
    source_file: str
    row_count: int
    score: float
    critical_fail: bool
    issue_count: int
    category_issue_counts: dict
    fixes_applied: list
    report_path: str | None = None


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def record_run(db_path: Path, record: RunRecord) -> int:
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO runs
               (run_timestamp, dataset_name, source_file, row_count, score,
                critical_fail, issue_count, category_issue_counts, fixes_applied, report_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.run_timestamp,
                record.dataset_name,
                record.source_file,
                record.row_count,
                record.score,
                int(record.critical_fail),
                record.issue_count,
                json.dumps(record.category_issue_counts),
                json.dumps(record.fixes_applied),
                record.report_path,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_history(db_path: Path, dataset_name: str | None = None, limit: int = 90) -> list[dict]:
    """Returns the most recent `limit` runs, oldest first (good for plotting)."""
    if not db_path.exists():
        return []
    conn = get_connection(db_path)
    try:
        if dataset_name:
            rows = conn.execute(
                """SELECT * FROM runs WHERE dataset_name = ?
                   ORDER BY run_timestamp DESC LIMIT ?""",
                (dataset_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY run_timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM runs LIMIT 0").description]
        records = [dict(zip(cols, row)) for row in rows]
        records.reverse()  
        for r in records:
            r["category_issue_counts"] = json.loads(r["category_issue_counts"])
            r["fixes_applied"] = json.loads(r["fixes_applied"])
            r["critical_fail"] = bool(r["critical_fail"])
        return records
    finally:
        conn.close()