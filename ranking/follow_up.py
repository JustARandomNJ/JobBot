"""Deterministic application follow-up decision support."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


DEFAULTS: dict[str, Any] = {
    "minimum_business_days": 5,
    "urgent_business_days": 8,
    "late_business_days": 12,
    "stale_business_days": 15,
    "max_unsolicited_follow_ups": 1,
    "allow_second_follow_up": False,
    "follow_up_spacing_business_days": 5,
    "holidays": [],
}

EXCLUDED_STATUSES = {"rejected", "skipped", "offer", "technical interview", "final interview", "withdrawn"}
AWAITING_STATUSES = {"applied", "no response"}
CONTACT_RANK = {
    "referral": 6, "recruiter": 5, "hiring manager": 4, "team member": 3,
    "general recruiting contact": 2, "unknown": 1,
}


def _date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def business_days_between(start: date | datetime | str, end: date | datetime | str,
                          holidays: Iterable[date | str] = ()) -> int:
    """Count weekdays after start through end; the application day is day zero."""
    first, last = _date(start), _date(end)
    if first is None or last is None or last <= first:
        return 0
    holiday_dates = {_date(day) for day in holidays}
    return sum(1 for offset in range(1, (last - first).days + 1)
               if (first + timedelta(days=offset)).weekday() < 5
               and first + timedelta(days=offset) not in holiday_dates)


def add_business_days(start: date | datetime | str, count: int,
                      holidays: Iterable[date | str] = ()) -> date:
    current = _date(start)
    if current is None:
        raise ValueError("A start date is required")
    holiday_dates = {_date(day) for day in holidays}
    remaining = max(0, count)
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holiday_dates:
            remaining -= 1
    return current


def best_contact(contacts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int]:
    if not contacts:
        return None, 0
    def quality(contact: dict[str, Any]) -> tuple[int, int, int]:
        source = str(contact.get("source") or "").lower()
        communicated = int("communicat" in source or "responded" in source or "conversation" in source)
        return (7 if communicated else CONTACT_RANK.get(str(contact.get("contact_type", "unknown")), 1),
                int(bool(contact.get("verified"))), int(bool(contact.get("email") or contact.get("profile_url"))))
    selected = max(contacts, key=quality)
    return selected, quality(selected)[0]


def recommend_follow_up(application: dict[str, Any], config: dict[str, Any] | None = None,
                        today: date | datetime | None = None) -> dict[str, Any]:
    settings = {**DEFAULTS, **(config or {})}
    today_date = _date(today or datetime.now(timezone.utc))
    applied = _date(application.get("applied_at"))
    status = str(application.get("status", "")).lower()
    contact, contact_quality = best_contact(application.get("contacts", []))
    result = {**application, "business_days_elapsed": None, "follow_up_score": 0.0,
              "due": False, "category": "excluded", "best_contact": contact,
              "recommendation": "No follow-up recommended.", "reason": ""}
    if application.get("do_not_follow_up"):
        result["reason"] = "Follow-up was explicitly disabled."
        return result
    if status in EXCLUDED_STATUSES:
        result["reason"] = f"Application status is {status}."
        return result
    if status == "recruiter screen":
        result["category"] = "recruiter_screen"
        result["reason"] = "Recruiter-screen follow-up should be based on the promised interaction timeline, not the unanswered-application schedule."
        result["recommendation"] = "Review the recruiter’s stated timeline separately."
        return result
    if status not in AWAITING_STATUSES:
        result["reason"] = "No submitted application is awaiting a response."
        return result
    if applied is None:
        result["category"] = "attention"
        result["reason"] = "Application date is unknown; backfill it before timing a follow-up."
        result["recommendation"] = "Backfill the application date."
        return result
    elapsed = business_days_between(applied, today_date, settings["holidays"])
    result["business_days_elapsed"] = elapsed
    count = int(application.get("follow_up_count") or 0)
    next_follow_up = _date(application.get("next_follow_up_at"))
    if count and next_follow_up is not None and today_date < next_follow_up:
        result["category"] = "upcoming"
        result["reason"] = f"A follow-up was already recorded; review again on {next_follow_up.isoformat()}."
        result["recommendation"] = "Continue waiting."
        return result
    concrete_history = any(item.get("contact_id") or str(item.get("note") or "").strip()
                           for item in application.get("follow_up_history", []))
    second_allowed = bool(settings["allow_second_follow_up"] or concrete_history)
    if count >= int(settings["max_unsolicited_follow_ups"]) and not second_allowed:
        result["category"] = "already_followed_up"
        result["reason"] = "The normal unsolicited follow-up limit has been reached."
        result["recommendation"] = "No additional unsolicited follow-up recommended."
        return result
    minimum = int(settings["minimum_business_days"])
    if elapsed < minimum:
        result["category"] = "upcoming"
        result["reason"] = f"Only {elapsed} business days have elapsed; wait at least {minimum}."
        result["recommendation"] = "Continue waiting."
        return result
    priority = float(application.get("priority_score") or 0)
    relevance_adjustment = {"strong match": 10, "possible": 3, "unreviewed": 0,
                            "poor match": -15, "irrelevant": -30}.get(application.get("relevance"), 0)
    if elapsed < int(settings["urgent_business_days"]):
        timing, timing_reason = 42, "First follow-up window"
    elif elapsed <= int(settings["late_business_days"]):
        timing, timing_reason = 62, "High-value follow-up window"
    elif elapsed < int(settings["stale_business_days"]):
        timing, timing_reason = 52, "Late follow-up window"
    else:
        timing, timing_reason = 30, "Application is old; repeated outreach has diminishing value"
    score = timing + (priority - 50) * .25 + relevance_adjustment + contact_quality * 3 - count * 25
    if not application.get("is_active", True):
        score -= 35
        timing_reason += "; posting is closed"
    result["follow_up_score"] = round(max(0, min(100, score)), 1)
    result["due"] = result["follow_up_score"] >= 35 and bool(application.get("is_active", True))
    result["category"] = "due" if result["due"] else "attention"
    if contact is None:
        result["recommendation"] = "No direct follow-up recommended — continue waiting or pursue networking separately."
        result["reason"] = f"{timing_reason}; no reasonable contact is recorded."
    else:
        label = contact.get("name") or contact.get("contact_type")
        result["recommendation"] = f"Consider one concise follow-up with {label}."
        result["reason"] = f"{timing_reason}; best contact is {contact.get('contact_type')}."
    return result


def rank_follow_ups(applications: list[dict[str, Any]], config: dict[str, Any] | None = None,
                    today: date | datetime | None = None) -> list[dict[str, Any]]:
    return sorted((recommend_follow_up(item, config, today) for item in applications),
                  key=lambda item: (-item["follow_up_score"], item.get("company", ""), item.get("title", "")))
