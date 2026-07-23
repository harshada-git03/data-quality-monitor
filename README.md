# Automated Data Quality Monitor

Before any analyst trusts a dataset for reporting, someone has to catch nulls, duplicates,
schema drift, and outliers. This project automates that first step: a recurring pipeline
watches for new data, runs a full battery of quality checks against it, fixes what's safe to
fix, and generates a report — with an alert firing automatically if something's wrong —
before bad data ever reaches a dashboard.

It's built around a simulated e-commerce pipeline (a stable `customers` table plus a new
`orders` export "arriving" daily) so the whole system is demonstrable end-to-end without
needing a real data source, but every piece is generic enough to point at a real folder,
API, or database with only a config change.

---

## What it actually does, end to end

1. **A new file "arrives"** in `data/incoming/` (simulated here; in production this would be
   an API pull, an S3 sync, an SFTP drop, or a folder a real pipeline writes to).
2. **Ingestion** loads it regardless of format (CSV, Excel, or JSON) into a plain DataFrame.
3. **Remediation** applies only the fixes that are unambiguous and lossless — trimming
   whitespace, normalizing casing, unifying date formats, dropping exact duplicate rows.
4. **Validation** runs the full check battery against what's left: schema, completeness,
   uniqueness, format/range, statistical outliers, referential integrity, freshness.
5. **Scoring** turns every issue found into a single 0–100 number, with per-category caps
   so no single problem can drown out the overall signal, and a separate `critical_fail`
   flag for issues serious enough to demand attention regardless of the score.
6. **Reporting** renders a single self-contained HTML file: score, status, a history trend
   chart, an issue breakdown by category, exactly what was auto-fixed, and every remaining
   issue with sample offending values.
7. **History** logs the run to a local SQLite database, so the trend chart and `status`
   command work without re-running anything.
8. **Alerting** fires a Slack message and/or email if the score drops below threshold or a
   critical check fails — and does nothing (cleanly, with a clear log line) if no channel is
   configured.
9. **Scheduling**: a GitHub Actions workflow runs this daily and uploads the report as a
   downloadable artifact — no server, no cron box, fully visible in the repo's Actions tab.

---

## Why it's built this way (the decisions that matter)

### Auto-fix only what's unambiguous — and say so explicitly

This is the single most important design choice in the project, and it's on purpose:

**Auto-fixed, because there's exactly one reasonable interpretation of the data:**
- Whitespace trimmed (`"  CUST00123  "` → `"CUST00123"`)
- Casing normalized against a known allowed-values list (`"PLACED"` → `"placed"`)
- Date formats unified (`"07/22/2026"` → `"2026-07-22"`)
- Exact duplicate rows removed (every column identical — no ambiguity about which copy is real)

**Never auto-fixed — flagged instead, every time:**
- Duplicate *keys* with different row content (which row is correct? that's not a
  question code should answer silently)
- Orphaned foreign keys, out-of-range values, invalid enum values, nulls, statistical outliers

Silently "fixing" ambiguous data — dropping rows that fail a range check, filling nulls
with a column mean, guessing which duplicate order is the real one — is how bad numbers
end up on a dashboard with nobody the wiser. A pipeline's job is to make problems visible
and reversible, not to make decisions an analyst should be making consciously. If you're
reading this in an interview context: this restraint is deliberate, not a missing feature.

### The score has per-category caps, and `critical_fail` is separate from it

Without a cap, one column with a bad null spike could drag the whole score to zero even if
everything else about the file is fine — a misleading signal. Capping each category's
maximum deduction keeps one bad category from drowning out everything else.

But some things should never be quietly averaged away. A single row with a foreign key
that points at a customer who doesn't exist is a real, standalone problem — even if the
overall score still looks like a 78. That's why `critical_fail` is tracked independently
of the numeric score and drives alerting on its own.

One consequence worth knowing: **even "minor" severity data can trip `critical_fail`.**
`customer_id` and `amount` have a zero-tolerance null threshold, and referential integrity
checks are always critical severity — so a single orphaned foreign key or one missing
required field is enough to trip the flag, regardless of overall file volume. This is
intentional: a single broken foreign key deserves to be surfaced loudly, not diluted into
a background statistic.

### Alerting is entirely environment-variable driven, with a clean no-op

`alerting.py` checks for `SLACK_WEBHOOK_URL` / `DQM_SMTP_HOST` etc. at runtime. If they're
unset, it doesn't fail, doesn't fake success — it logs exactly why it skipped each channel.
This means the project runs correctly in a fresh clone or CI job with zero configuration,
while becoming "real" the moment you drop in actual credentials as GitHub Actions secrets.

### Validation and remediation are separate concerns, by design

`validation.py` never mutates data. `remediation.py` never decides what counts as "safe."
Keeping these apart is what makes the report trustworthy: it can say precisely what was
checked, what was touched, and what was left alone, because those are three genuinely
different steps, not one function doing all three at once.

### A known nuance, worth being upfront about

Blank strings (`""`) and true nulls (`NaN`) aren't the same thing, but a naive completeness
check can conflate them — a column full of intentional empty strings (e.g., "no discount
code applied") can look identical to a column with real missing data. This project's
completeness check reads them as pandas would (empty string ≠ null), so a high "null rate"
on a column like `discount_code` is worth a second look before assuming it's actually broken
data versus just a common valid value. It's flagged as designed, but it's a good example of
why a quality report is a starting point for a human, not a final verdict.

---

## Project structure
dqm/
├── config/
│ └── rules.yaml # expected schema, thresholds, allowed values, scoring weights
├── data/
│ ├── incoming/ # new files "arrive" here
│ ├── archive/ # processed files are moved here (never reprocessed)
│ └── reference/ # stable dimension tables (customers.csv) - not archived
├── reports/
│ ├── html/ # one report per run
│ └── history.db # SQLite log of every run, powers the trend chart
├── src/dqm/
│ ├── simulator.py # generates fake daily exports with injectable issues
│ ├── ingestion.py # format-agnostic loader (CSV/Excel/JSON) + folder watcher
│ ├── validation.py # the check battery - never mutates data
│ ├── remediation.py # safe auto-fixes only - see philosophy above
│ ├── scoring.py # issues -> 0-100 score + critical_fail flag
│ ├── reporting.py # self-contained HTML report generator
│ ├── history.py # SQLite read/write for run history
│ ├── alerting.py # Slack + email, env-var driven, clean no-op
│ └── pipeline.py # orchestrates all of the above for one run
├── cli.py # entrypoint: simulate / run / status
├── .github/workflows/
│ └── dqm.yml # daily scheduled run + report artifact upload
└── requirements.txt