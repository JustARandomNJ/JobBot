from datetime import datetime, timedelta, timezone

from models.job import Job
from ranking.priority import calculate_priority
from ranking.scorer import score_job


def test_ineligible_is_gated_and_manual_review_penalized():
    eligible, _ = calculate_priority(overall_score=80, eligibility="eligible", role_weight=1, freshness=100)
    manual, _ = calculate_priority(overall_score=80, eligibility="manual_review", role_weight=1, freshness=100)
    blocked, _ = calculate_priority(overall_score=100, eligibility="ineligible", role_weight=1, freshness=100)
    assert blocked == 0 and manual < eligible


def test_role_preference_and_freshness_influence_priority():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    profile = {"target_role_weights": {"embedded_firmware": 1.0, "general_software": .4}}
    prefs = {"eligibility": {}}
    fresh = score_job(Job(title="Embedded Firmware Engineer", description="MCU RTOS", date_posted=now), profile, prefs, now)
    stale = score_job(Job(title="Embedded Firmware Engineer", description="MCU RTOS", date_posted=now-timedelta(days=40)), profile, prefs, now)
    assert fresh.priority_score > stale.priority_score
    expected_overall = .40*fresh.fit_score + .25*fresh.competitiveness_score + .20*fresh.preference_score + .15*fresh.recency_score
    assert fresh.overall_score == round(expected_overall, 1)


def test_equal_technical_fit_gets_distinct_contextual_priority():
    preferred, _ = calculate_priority(overall_score=80, eligibility="eligible", role_weight=1.0, freshness=100)
    stale_backup, _ = calculate_priority(overall_score=80, eligibility="eligible", role_weight=.4, freshness=20)
    assert preferred != stale_backup


def test_unknown_components_are_neutral_and_inspectable():
    score, factors = calculate_priority(overall_score=70, eligibility="unknown")
    assert 0 < score < 100
    assert any(f["factor"] == "application_effort" and f["effect"] == "neutral" for f in factors)
