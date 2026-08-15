"""Freshness and cautious posting-health signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_FRESHNESS_BUCKETS = [
    (1, 100.0, "<1 day"), (4, 90.0, "1-3 days"), (8, 78.0, "4-7 days"),
    (15, 62.0, "8-14 days"), (31, 42.0, "15-30 days"), (10**9, 20.0, "30+ days"),
]


def freshness_score(date_posted: datetime | str | None, config: dict[str, Any] | None = None,
                    now: datetime | None = None) -> tuple[float, str]:
    if not date_posted:
        return 50.0, "unknown"
    posted = datetime.fromisoformat(date_posted) if isinstance(date_posted, str) else date_posted
    posted = posted if posted.tzinfo else posted.replace(tzinfo=timezone.utc)
    age = max(0.0, ((now or datetime.now(timezone.utc)) - posted).total_seconds() / 86400)
    buckets = (config or {}).get("freshness_buckets", DEFAULT_FRESHNESS_BUCKETS)
    for maximum, score, label in buckets:
        if age < float(maximum):
            return float(score), str(label)
    return 20.0, "30+ days"


def repost_risk(*, age_days: float | None, reopened_count: int = 0, times_seen: int = 1,
                description_changes: int = 0, thresholds: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    cfg = thresholds or {}
    moderate_days = float(cfg.get("moderate_age_days", 45))
    high_days = float(cfg.get("high_age_days", 90))
    evidence: list[str] = []
    points = 0
    if age_days is not None and age_days >= high_days:
        points += 2; evidence.append(f"Posting has been observed for about {int(age_days)} days.")
    elif age_days is not None and age_days >= moderate_days:
        points += 1; evidence.append(f"Posting has remained listed for about {int(age_days)} days.")
    if reopened_count >= 2:
        points += 2; evidence.append("The posting has disappeared and returned multiple times.")
    elif reopened_count == 1:
        points += 1; evidence.append("The posting disappeared and later returned.")
    if times_seen >= int(cfg.get("many_observations", 10)) and description_changes == 0:
        points += 1; evidence.append("It has been repeatedly observed with nearly identical content.")
    return ("high" if points >= 3 else "moderate" if points >= 1 else "low"), evidence
