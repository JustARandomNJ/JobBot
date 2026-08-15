"""Transparent application-priority calculation, separate from technical fit."""

from __future__ import annotations

from typing import Any

DEFAULT_WEIGHTS = {
    "technical_fit": 0.50, "role_preference": 0.22, "freshness": 0.12,
    "historical_conversion": 0.07, "posting_health": 0.05,
    "company_saturation": 0.02, "application_effort": 0.02,
}


def calculate_priority(*, overall_score: float, eligibility: str, role_weight: float | None = None,
                       freshness: float | None = None, historical_conversion: float | None = None,
                       posting_health: float | None = None, company_saturation: float | None = None,
                       application_effort: float | None = None, config: dict[str, Any] | None = None
                       ) -> tuple[float, list[dict[str, Any]]]:
    cfg = config or {}
    weights = {**DEFAULT_WEIGHTS, **cfg.get("weights", {})}
    values = {
        "technical_fit": overall_score,
        "role_preference": None if role_weight is None else max(0.0, min(100.0, role_weight * 100)),
        "freshness": freshness, "historical_conversion": historical_conversion,
        "posting_health": posting_health, "company_saturation": company_saturation,
        "application_effort": application_effort,
    }
    factors = []
    weighted = 0.0
    total_weight = sum(float(value) for value in weights.values()) or 1.0
    for name, weight in weights.items():
        value = values[name]
        neutral = value is None
        effective = 50.0 if neutral else float(value)
        weighted += float(weight) * effective
        factors.append({"factor": name, "value": value, "weight": float(weight),
                        "effect": "neutral" if neutral else "strong" if effective >= 75 else "weak" if effective < 40 else "moderate"})
    score = max(0.0, min(100.0, weighted / total_weight))
    if eligibility == "ineligible":
        factors.append({"factor": "eligibility", "value": "ineligible", "weight": "gate", "effect": "blocked"})
        return 0.0, factors
    if eligibility == "manual_review":
        penalty = float(cfg.get("manual_review_penalty", 15))
        score -= penalty
        factors.append({"factor": "eligibility", "value": "manual_review", "weight": "gate", "effect": f"-{penalty:g}"})
    else:
        factors.append({"factor": "eligibility", "value": eligibility, "weight": "gate", "effect": "pass" if eligibility == "eligible" else "neutral"})
    return round(max(0.0, score), 1), factors
