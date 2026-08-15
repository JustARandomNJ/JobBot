"""Application funnel summaries and conservative personal calibration."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

APPLIED = {"applied", "no response", "rejected", "recruiter screen", "technical interview", "final interview", "offer", "withdrawn"}
RESPONDED = {"recruiter screen", "technical interview", "final interview", "offer", "rejected"}
INTERVIEWED = {"technical interview", "final interview", "offer"}


def historical_conversion(rows: list[dict[str, Any]], role_family: str, *, minimum_sample: int = 5,
                          prior_strength: float = 8.0, neutral: float = 50.0) -> float | None:
    relevant = [row for row in rows if row.get("role_family") == role_family and row.get("status") in APPLIED]
    if len(relevant) < minimum_sample:
        return None
    successes = sum(row["status"] in INTERVIEWED for row in relevant)
    # Beta-style shrinkage toward the neutral baseline prevents a short run of
    # rejections from dominating the application priority.
    return round(100 * (successes + prior_strength * neutral / 100) / (len(relevant) + prior_strength), 1)


def _summary(rows: list[dict[str, Any]], minimum_sample: int = 5) -> dict[str, Any]:
    applications = [row for row in rows if row.get("status") in APPLIED]
    counts = Counter(row["status"] for row in applications)
    denominator = len(applications)
    result: dict[str, Any] = {
        "total": denominator, "pending": sum(counts[s] for s in ("applied", "no response")),
        "rejected": counts["rejected"], "interviews": sum(counts[s] for s in INTERVIEWED),
        "offers": counts["offer"], "withdrawn": counts["withdrawn"],
        "small_sample": denominator < minimum_sample,
    }
    for name, states in (("response_rate", RESPONDED), ("interview_rate", INTERVIEWED), ("offer_rate", {"offer"})):
        result[name] = None if denominator == 0 else round(100 * sum(counts[s] for s in states) / denominator, 1)
    return result


def summarize(rows: list[dict[str, Any]], minimum_sample: int = 5) -> dict[str, Any]:
    result = _summary(rows, minimum_sample)
    groups: dict[str, dict[str, Any]] = {}
    for field in ("fit_band", "priority_band", "role_family", "company", "source", "freshness_band",
                  "eligibility_status", "application_age_band", "skip_reason"):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get(field): buckets[str(row[field])].append(row)
        groups[field] = {key: _summary(value, minimum_sample) for key, value in sorted(buckets.items())}
    result["groups"] = groups
    return result
