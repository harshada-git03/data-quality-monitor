
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class RemediationResult:
    df: pd.DataFrame
    fixes_applied: list[str] = field(default_factory=list)
    rows_removed: int = 0


def _normalize_dates(series: pd.Series) -> pd.Series:
    """Parses whatever date-ish string is there and rewrites it as YYYY-MM-DD.
    Leaves unparseable values untouched (those get caught by validation, not
    silently blanked here)."""
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    normalized = parsed.dt.strftime("%Y-%m-%d")
    return normalized.where(parsed.notna(), series)


def auto_remediate(
    df: pd.DataFrame,
    dataset_cfg: dict,
) -> RemediationResult:
    df = df.copy()
    fixes = []

    
    str_cols = df.select_dtypes(include="object").columns
    trimmed_any = False
    for col in str_cols:
        before = df[col]
        after = before.where(before.isna(), before.astype(str).str.strip())
        if not before.equals(after):
            n_changed = (before != after).sum()
            df[col] = after
            fixes.append(f"Trimmed whitespace in '{col}' ({n_changed} value(s)).")
            trimmed_any = True

    
    format_cfg = dataset_cfg.get("format_rules", {}) or {}
    for col, rule in format_cfg.items():
        if col not in df.columns or "allowed_values" not in rule:
            continue
        allowed = set(rule["allowed_values"])
        before = df[col]
        candidate = before.where(before.isna(), before.astype(str).str.lower())
        would_fix_mask = before.notna() & (~before.isin(allowed)) & (candidate.isin(allowed))
        if would_fix_mask.any():
            df.loc[would_fix_mask, col] = candidate[would_fix_mask]
            fixes.append(
                f"Normalized casing in '{col}' for {int(would_fix_mask.sum())} value(s) "
                f"(e.g. 'PLACED' -> 'placed')."
            )

    
    schema_cols = dataset_cfg.get("schema", {}).get("columns", {})
    for col, spec in schema_cols.items():
        if col not in df.columns or spec.get("dtype") != "date":
            continue
        before = df[col]
        after = _normalize_dates(before)
        if not before.equals(after):
            n_changed = (before.fillna("") != after.fillna("")).sum()
            if n_changed > 0:
                df[col] = after
                fixes.append(f"Unified date format in '{col}' for {n_changed} value(s).")

    
    before_len = len(df)
    df = df.drop_duplicates(keep="first").reset_index(drop=True)
    rows_removed = before_len - len(df)
    if rows_removed > 0:
        fixes.append(f"Removed {rows_removed} exact duplicate row(s).")

    if not trimmed_any and len(fixes) == 0:
        fixes.append("No safe auto-fixes were needed.")

    return RemediationResult(df=df, fixes_applied=fixes, rows_removed=rows_removed)