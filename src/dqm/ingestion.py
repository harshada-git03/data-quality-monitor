"""
Ingestion layer. Deliberately format-agnostic: the validation engine downstream
only ever sees a pandas DataFrame, so adding a new source format later means
adding one function here, not touching anything else in the pipeline.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class IngestedFile:
    path: Path
    dataset_name: str
    df: pd.DataFrame


def load_any(path: Path) -> pd.DataFrame:
    """Loads a CSV, Excel (.xlsx/.xls), or JSON file into a DataFrame."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=True)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    if suffix == ".json":
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict) and "records" in data:
            data = data["records"]
        return pd.DataFrame(data).astype(str).replace("nan", pd.NA)
    raise ValueError(f"Unsupported file format: {suffix}")


def find_new_files(incoming_dir: Path, archive_dir: Path, pattern: str = "*") -> list[Path]:
    """Watch-folder logic: anything in incoming/ that isn't already archived."""
    archived_names = {p.name for p in archive_dir.glob("*")} if archive_dir.exists() else set()
    candidates = sorted(
        p for p in incoming_dir.glob(pattern)
        if p.is_file() and p.name not in archived_names
    )
    return candidates


def match_dataset(filename: str, dataset_configs: dict) -> str | None:
    """Given a filename and the `datasets` section of rules.yaml, find which
    dataset config's file_glob it matches."""
    for name, cfg in dataset_configs.items():
        if fnmatch.fnmatch(filename, cfg["file_glob"]):
            return name
    return None


def archive_file(path: Path, archive_dir: Path) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / path.name
    path.replace(dest)
    return dest