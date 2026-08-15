"""Company application saturation warnings."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


ACTIVE = {"applied", "recruiter screen", "technical interview", "final interview", "no response"}


def saturation(rows: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or {}
    families = Counter(row.get("role_family") or "other" for row in rows)
    active = sum(row.get("status") in ACTIVE for row in rows)
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(cfg.get("recent_window_days", 90)))
    recent = 0
    for row in rows:
        if row.get("applied_at"):
            when = datetime.fromisoformat(row["applied_at"])
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            recent += int(when >= cutoff)
    unrelated = len([name for name, count in families.items() if name != "other" and count])
    points = active + max(0, unrelated - int(cfg.get("coherent_family_count", 2))) * 2
    level = "high" if points >= int(cfg.get("high_threshold", 7)) else "moderate" if points >= int(cfg.get("moderate_threshold", 3)) else "low"
    warning = ""
    if unrelated > int(cfg.get("coherent_family_count", 2)):
        warning = "Applications span several unrelated role families at this company."
    elif active >= int(cfg.get("active_warning", 3)):
        warning = "Several applications are currently active at this company."
    return {"total": len(rows), "active": active, "recent": recent, "role_families": dict(families), "level": level, "warning": warning}
