"""
The validation engine. Runs a configurable battery of checks against a
DataFrame and returns a flat list of Issue objects. This module never mutates
data and never decides what's "safe to fix" - that's remediation.py's job.
Keeping those concerns separate is what makes it possible to prove, in a
report, exactly what was checked and exactly what was (and wasn't) touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass
class Issue:
    category: str           
    severity: str            
    column: str | None
    message: str
    row_count: int = 0        
    sample_rows: list = field(default_factory=list)  


def check_schema(df: pd.DataFrame, schema_cfg: dict) -> list[Issue]:
    issues = []
    expected_cols = set(schema_cfg["columns"].keys())
    actual_cols = set(df.columns)

    missing = expected_cols - actual_cols
    for col in sorted(missing):
        required = schema_cfg["columns"][col].get("required", True)
        issues.append(Issue(
            category="schema",
            severity="critical" if required else "warning",
            column=col,
            message=f"Expected column '{col}' is missing from the file.",
        ))

    if not schema_cfg.get("allow_new_columns", True):
        unexpected = actual_cols - expected_cols
        for col in sorted(unexpected):
            issues.append(Issue(
                category="schema",
                severity="warning",
                column=col,
                message=f"Unexpected column '{col}' found (schema drift - not in expected schema).",
            ))

   
    for col, spec in schema_cfg["columns"].items():
        if col not in df.columns:
            continue
        dtype = spec.get("dtype")
        non_null = df[col].dropna()
        if len(non_null) == 0:
            continue
        if dtype == "int":
           
            bad = non_null[~non_null.astype(str).str.match(r"^-?\d+(\.0+)?$")]
        elif dtype == "float":
            bad = non_null[~non_null.astype(str).str.match(r"^-?\d+(\.\d+)?$")]
        elif dtype == "date":
            parsed = pd.to_datetime(non_null, errors="coerce", format="%Y-%m-%d")
            bad = non_null[parsed.isna()]
        else:
            bad = pd.Series(dtype=str)
        if len(bad) > 0:
            issues.append(Issue(
                category="schema",
                severity="warning",
                column=col,
                message=f"{len(bad)} value(s) in '{col}' don't match expected type '{dtype}'.",
                row_count=len(bad),
                sample_rows=bad.head(5).tolist(),
            ))
    return issues


def check_completeness(df: pd.DataFrame, completeness_cfg: dict) -> list[Issue]:
    issues = []
    if not completeness_cfg or len(df) == 0:
        return issues
    default_rate = completeness_cfg.get("max_null_rate", {}).get("default", 0.05)
    overrides = completeness_cfg.get("max_null_rate", {}).get("overrides", {})

    for col in df.columns:
        threshold = overrides.get(col, default_rate)
        null_count = df[col].isna().sum()
        null_rate = null_count / len(df)
        if null_rate > threshold:
            issues.append(Issue(
                category="completeness",
                severity="critical" if threshold == 0.0 else "warning",
                column=col,
                message=(f"'{col}' is {null_rate:.1%} null "
                         f"(threshold {threshold:.1%}) - {null_count} of {len(df)} rows."),
                row_count=int(null_count),
            ))
    return issues


def check_uniqueness(df: pd.DataFrame, uniqueness_cfg: dict) -> list[Issue]:
    issues = []
    if not uniqueness_cfg:
        return issues

    exact_dupes = df[df.duplicated(keep=False)]
    if len(exact_dupes) > 0:
        n_groups = df.duplicated(keep="first").sum()
        issues.append(Issue(
            category="uniqueness",
            severity="warning",
            column=None,
            message=f"{n_groups} exact duplicate row(s) found (safe to auto-remove).",
            row_count=int(n_groups),
            sample_rows=exact_dupes.head(5).index.tolist(),
        ))

    for col in uniqueness_cfg.get("unique_columns", []):
        if col not in df.columns:
            continue
        non_null = df[df[col].notna()]
        dup_keys = non_null[non_null.duplicated(subset=[col], keep=False)]
        if len(dup_keys) > 0:
            n_dup_groups = non_null.duplicated(subset=[col], keep="first").sum()
            issues.append(Issue(
                category="uniqueness",
                severity="critical",
                column=col,
                message=(f"{n_dup_groups} duplicate key(s) in '{col}' - same key, "
                         f"possibly different row content. NOT auto-fixed (ambiguous "
                         f"which row is correct)."),
                row_count=int(n_dup_groups),
                sample_rows=dup_keys[col].head(5).tolist(),
            ))
    return issues


def check_format_and_range(df: pd.DataFrame, format_cfg: dict, range_cfg: dict) -> list[Issue]:
    issues = []
    format_cfg = format_cfg or {}
    range_cfg = range_cfg or {}

    for col, rule in format_cfg.items():
        if col not in df.columns:
            continue
        non_null = df[col].dropna().astype(str).str.strip()
        if len(non_null) == 0:
            continue
        if "pattern" in rule:
            bad_mask = ~non_null.str.match(rule["pattern"])
            bad = non_null[bad_mask]
            if len(bad) > 0:
                issues.append(Issue(
                    category="format", severity="warning", column=col,
                    message=f"{len(bad)} value(s) in '{col}' don't match required format.",
                    row_count=len(bad), sample_rows=bad.head(5).tolist(),
                ))
        if "allowed_values" in rule:
            allowed = set(rule["allowed_values"])
            
            raw_non_null = df[col].dropna().astype(str)
            bad_mask = ~raw_non_null.isin(allowed)
            bad = raw_non_null[bad_mask]
            if len(bad) > 0:
                issues.append(Issue(
                    category="format", severity="warning", column=col,
                    message=(f"{len(bad)} value(s) in '{col}' aren't in the allowed list "
                             f"{sorted(allowed)}. Examples: {bad.unique()[:5].tolist()}"),
                    row_count=len(bad), sample_rows=bad.head(5).tolist(),
                ))

    for col, rule in range_cfg.items():
        if col not in df.columns:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce")
        mask = pd.Series(False, index=df.index)
        if rule.get("min") is not None:
            mask |= numeric < rule["min"]
        if rule.get("max") is not None:
            mask |= numeric > rule["max"]
        bad = df.loc[mask.fillna(False), col]
        if len(bad) > 0:
            issues.append(Issue(
                category="format", severity="warning", column=col,
                message=(f"{len(bad)} value(s) in '{col}' are outside allowed range "
                         f"[{rule.get('min')}, {rule.get('max')}]."),
                row_count=len(bad), sample_rows=bad.head(5).tolist(),
            ))
    return issues


def check_outliers(df: pd.DataFrame, outlier_cfg: dict) -> list[Issue]:
    issues = []
    if not outlier_cfg or not outlier_cfg.get("columns"):
        return issues
    method = outlier_cfg.get("method", "zscore")

    for col in outlier_cfg["columns"]:
        if col not in df.columns:
            continue
        numeric = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(numeric) < 10:
            continue  

        if method == "zscore":
            mean, std = numeric.mean(), numeric.std()
            if std == 0 or np.isnan(std):
                continue
            z = (numeric - mean) / std
            threshold = outlier_cfg.get("zscore_threshold", 3.0)
            flagged = numeric[z.abs() > threshold]
        else:  
            q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
            iqr = q3 - q1
            mult = outlier_cfg.get("iqr_multiplier", 1.5)
            lower, upper = q1 - mult * iqr, q3 + mult * iqr
            flagged = numeric[(numeric < lower) | (numeric > upper)]

        if len(flagged) > 0:
            issues.append(Issue(
                category="outlier", severity="warning", column=col,
                message=(f"{len(flagged)} statistical outlier(s) in '{col}' "
                         f"(method={method}). Flagged for review, not auto-fixed."),
                row_count=len(flagged), sample_rows=flagged.head(5).tolist(),
            ))
    return issues


def check_referential(df: pd.DataFrame, foreign_keys: list[dict], reference_dfs: dict[str, pd.DataFrame]) -> list[Issue]:
    issues = []
    for fk in foreign_keys or []:
        col = fk["column"]
        ref_dataset = fk["references_dataset"]
        ref_col = fk["references_column"]
        if col not in df.columns or ref_dataset not in reference_dfs:
            continue
        ref_values = set(reference_dfs[ref_dataset][ref_col].dropna().astype(str).str.strip())
        actual = df[col].dropna().astype(str).str.strip()
        orphans = actual[~actual.isin(ref_values)]
        if len(orphans) > 0:
            issues.append(Issue(
                category="referential", severity="critical", column=col,
                message=(f"{len(orphans)} row(s) have '{col}' values that don't exist "
                         f"in {ref_dataset}.{ref_col} (orphaned foreign key)."),
                row_count=len(orphans), sample_rows=orphans.head(5).tolist(),
            ))
    return issues


def check_freshness(df: pd.DataFrame, freshness_cfg: dict, run_date: datetime) -> list[Issue]:
    issues = []
    if not freshness_cfg or not freshness_cfg.get("enabled"):
        return issues
    date_col = freshness_cfg["date_column"]
    if date_col not in df.columns:
        return issues
    parsed = pd.to_datetime(df[date_col], errors="coerce")
    if parsed.isna().all():
        return issues
    newest = parsed.max()
    age_days = (run_date - newest.to_pydatetime()).days
    max_age = freshness_cfg.get("max_age_days", 2)
    if age_days > max_age:
        issues.append(Issue(
            category="freshness", severity="critical", column=date_col,
            message=(f"Newest date in file is {newest.date()}, which is {age_days} day(s) "
                     f"old (max allowed: {max_age}). This looks like a stale or reprocessed file."),
        ))
    return issues


def run_all_checks(
    df: pd.DataFrame,
    dataset_cfg: dict,
    reference_dfs: dict[str, pd.DataFrame] | None = None,
    run_date: datetime | None = None,
) -> list[Issue]:
    """Runs the full battery of checks for one dataset and returns all issues found."""
    reference_dfs = reference_dfs or {}
    run_date = run_date or datetime.now()

    issues = []
    issues += check_schema(df, dataset_cfg["schema"])
    issues += check_completeness(df, dataset_cfg.get("completeness", {}))
    issues += check_uniqueness(df, dataset_cfg.get("uniqueness", {}))
    issues += check_format_and_range(df, dataset_cfg.get("format_rules", {}), dataset_cfg.get("range_rules", {}))
    issues += check_outliers(df, dataset_cfg.get("outlier_columns", {}))
    issues += check_referential(df, dataset_cfg.get("foreign_keys", []), reference_dfs)
    issues += check_freshness(df, dataset_cfg.get("freshness", {}), run_date)
    return issues