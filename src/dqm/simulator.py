"""
Simulates a recurring data pipeline: a stable customers dimension table plus a
new orders export "arriving" each day. Issues (nulls, dupes, outliers, schema
drift, orphan keys, stale dates, format breaks) are injected on purpose so the
validation engine downstream has real things to catch.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

COUNTRIES = ["US", "CA", "UK", "DE", "FR", "AU", "IN", "BR", "MX", "JP"]
STATUSES_CUST = ["active", "inactive", "churned", "pending"]
STATUSES_ORDER = ["placed", "shipped", "delivered", "cancelled", "refunded"]


SEVERITY_PRESETS = {
    "clean": dict(null_rate=0.005, dup_row_rate=0.0, dup_key_rate=0.0,
                  orphan_rate=0.0, outlier_rate=0.005, schema_drift=None,
                  stale=False, format_break_rate=0.005),
    "minor": dict(null_rate=0.02, dup_row_rate=0.005, dup_key_rate=0.002,
                  orphan_rate=0.005, outlier_rate=0.01, schema_drift=None,
                  stale=False, format_break_rate=0.02),
    "major": dict(null_rate=0.06, dup_row_rate=0.02, dup_key_rate=0.015,
                  orphan_rate=0.02, outlier_rate=0.03, schema_drift="extra_column",
                  stale=False, format_break_rate=0.06),
    "critical": dict(null_rate=0.15, dup_row_rate=0.05, dup_key_rate=0.04,
                      orphan_rate=0.06, outlier_rate=0.05, schema_drift="missing_column",
                      stale=True, format_break_rate=0.15),
}


def _rand_email(name: str, i: int) -> str:
    return f"{name.lower()}{i}@example.com"


def generate_customers(path: Path, n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Writes (or overwrites) the stable customers reference table."""
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    first_names = ["alex", "sam", "jordan", "taylor", "morgan", "casey", "riley",
                   "jamie", "drew", "avery", "quinn", "reese", "skyler", "rowan"]
    rows = []
    start = datetime(2022, 1, 1)
    for i in range(1, n + 1):
        name = rng.choice(first_names)
        signup = start + timedelta(days=int(np_rng.integers(0, 900)))
        rows.append({
            "customer_id": f"CUST{i:05d}",
            "email": _rand_email(name, i),
            "signup_date": signup.strftime("%Y-%m-%d"),
            "country": rng.choice(COUNTRIES),
            "status": rng.choices(STATUSES_CUST, weights=[70, 15, 10, 5])[0],
        })
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def generate_orders_day(
    customers_df: pd.DataFrame,
    order_date: datetime,
    day_offset_for_ids: int,
    n: int = 300,
    severity: str = "clean",
    seed: int | None = None,
) -> pd.DataFrame:
    """Generates one day's orders export with issues injected per `severity`."""
    cfg = SEVERITY_PRESETS[severity]
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    valid_customer_ids = customers_df["customer_id"].tolist()
    rows = []
    base_order_num = day_offset_for_ids * n

    for i in range(n):
        order_id = f"ORD{base_order_num + i:06d}"
        cust_id = rng.choice(valid_customer_ids)

       
        if rng.random() < cfg["orphan_rate"]:
            cust_id = f"CUST{rng.randint(90000, 99999):05d}" 

        amount = round(np_rng.gamma(shape=3.0, scale=25.0), 2)
       
        if rng.random() < cfg["outlier_rate"]:
            amount = round(amount * rng.uniform(15, 60), 2)

        quantity = int(np_rng.integers(1, 6))
        if rng.random() < cfg["outlier_rate"] * 0.5:
            quantity = int(rng.choice([500, 900, -3]))

        status = rng.choices(STATUSES_ORDER, weights=[30, 25, 30, 10, 5])[0]

        rows.append({
            "order_id": order_id,
            "customer_id": cust_id,
            "order_date": order_date.strftime("%Y-%m-%d"),
            "amount": amount,
            "quantity": quantity,
            "status": status,
            "discount_code": rng.choice(["", "", "", "SAVE10", "WELCOME5"]),
        })

    df = pd.DataFrame(rows)

    
    fmt_mask = np_rng.random(len(df)) < cfg["format_break_rate"]
    idxs = df.index[fmt_mask]
    for idx in idxs:
        choice = rng.choice(["case", "whitespace", "date_format", "bad_status", "neg_amount"])
        if choice == "case":
            df.loc[idx, "status"] = df.loc[idx, "status"].upper()
        elif choice == "whitespace":
            df.loc[idx, "customer_id"] = f"  {df.loc[idx, 'customer_id']}  "
        elif choice == "date_format":
            d = datetime.strptime(df.loc[idx, "order_date"], "%Y-%m-%d")
            df.loc[idx, "order_date"] = d.strftime("%m/%d/%Y")
        elif choice == "bad_status":
            df.loc[idx, "status"] = rng.choice(["proccessed", "unknown", "N/A"])
        elif choice == "neg_amount":
            df.loc[idx, "amount"] = -abs(df.loc[idx, "amount"])

    
    for col in ["customer_id", "amount", "quantity", "status", "discount_code"]:
        mask = np_rng.random(len(df)) < cfg["null_rate"]
        df.loc[mask, col] = np.nan

    
    if cfg["dup_row_rate"] > 0:
        n_dupes = int(len(df) * cfg["dup_row_rate"])
        if n_dupes > 0:
            dupes = df.sample(n=n_dupes, random_state=seed, replace=True)
            df = pd.concat([df, dupes], ignore_index=True)

    
    if cfg["dup_key_rate"] > 0:
        n_dk = int(len(df) * cfg["dup_key_rate"])
        if n_dk > 0:
            sample = df.sample(n=n_dk, random_state=(seed or 0) + 1, replace=True).copy()
            sample["amount"] = (sample["amount"].fillna(0).astype(float) * 1.1).round(2)
            df = pd.concat([df, sample], ignore_index=True)

   
    if cfg["schema_drift"] == "extra_column":
        df["referral_source"] = rng.choice(["ad", "organic", "referral"])
    elif cfg["schema_drift"] == "missing_column":
        df = df.drop(columns=["discount_code"])

   
    if cfg["stale"]:
        df["order_date"] = (order_date - timedelta(days=10)).strftime("%Y-%m-%d")

    return df.reset_index(drop=True)


def write_orders_file(df: pd.DataFrame, out_dir: Path, order_date: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"orders_{order_date.strftime('%Y-%m-%d')}.csv"
    df.to_csv(path, index=False)
    return path


def simulate_one_day(
    incoming_dir: Path,
    reference_dir: Path,
    date: datetime | None = None,
    severity: str = "clean",
    n: int = 300,
    seed: int | None = None,
) -> Path:
    """Ensures customers.csv exists, then drops one orders_<date>.csv file."""
    customers_path = reference_dir / "customers.csv"
    if customers_path.exists():
        customers_df = pd.read_csv(customers_path, dtype=str)
    else:
        customers_df = generate_customers(customers_path)

    date = date or datetime.now()
    day_offset = int(date.strftime("%j"))  # day-of-year, just for unique order id ranges
    orders_df = generate_orders_day(
        customers_df, date, day_offset_for_ids=day_offset, n=n, severity=severity, seed=seed
    )
    return write_orders_file(orders_df, incoming_dir, date)