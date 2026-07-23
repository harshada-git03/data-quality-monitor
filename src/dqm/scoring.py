"""
Turns a list of validation Issues into one number stakeholders can track over
time, plus a boolean signal for "this needs attention right now regardless of
the number". The score is a communication tool, not the ground truth - the
full issue list in the report is the ground truth. Two design choices worth
calling out:

  1. Per-category caps: without a cap, a single column with a big null spike
     could drag the score to zero even if everything else about the file is
     fine. Caps keep one bad category from drowning out the overall signal.
  2. `critical_fail` is separate from the score: a file can score 78/100
     (fine on paper) but still have a broken foreign key, which should always
     be surfaced loudly rather than quietly averaged away.
"""
from __future__ import annotations

from dataclasses import dataclass

from .validation import Issue


@dataclass
class ScoreResult:
    score: float
    critical_fail: bool
    category_deductions: dict[str, float]
    category_issue_counts: dict[str, int]


def _deduction_for_issue(issue: Issue, weight: float) -> float:
    """Most categories cost `weight` per issue found. completeness scales by
    how far over threshold the column is, using the row_count as a rough
    proxy for magnitude so a barely-over-threshold column costs less than a
    column that's half null."""
    if issue.category == "completeness":
        return weight * max(1, issue.row_count / 10)
    if issue.category in ("format", "outlier", "referential"):
        return weight * max(1, issue.row_count)
    return weight  # schema, uniqueness, freshness: flat cost per issue found


def score_run(issues: list[Issue], scoring_cfg: dict) -> ScoreResult:
    base = scoring_cfg.get("base_score", 100)
    weights = scoring_cfg.get("category_weights", {})
    caps = scoring_cfg.get("category_caps", {})
    critical_categories = set(scoring_cfg.get("critical_categories", []))

    raw_deductions: dict[str, float] = {}
    issue_counts: dict[str, int] = {}
    critical_fail = False

    for issue in issues:
        cat = issue.category
        weight = weights.get(cat, 1.0)
        raw_deductions[cat] = raw_deductions.get(cat, 0.0) + _deduction_for_issue(issue, weight)
        issue_counts[cat] = issue_counts.get(cat, 0) + 1
        if issue.severity == "critical" and cat in critical_categories:
            critical_fail = True

    capped_deductions = {
        cat: min(amount, caps.get(cat, amount))
        for cat, amount in raw_deductions.items()
    }

    total_deduction = sum(capped_deductions.values())
    score = max(0.0, min(base, base - total_deduction))

    return ScoreResult(
        score=round(score, 1),
        critical_fail=critical_fail,
        category_deductions=capped_deductions,
        category_issue_counts=issue_counts,
    )