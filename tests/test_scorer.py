from datetime import datetime, timezone

import pytest

from models.job import Job
from ranking.scorer import recommendation_for, score_job
from tests.conftest import load_config


PROFILE = load_config("candidate_profile.yaml")
PREFERENCES = load_config("preferences.yaml")


def test_priority_score_uses_documented_weights() -> None:
    job = Job(title="Firmware Engineer", location="San Francisco, CA", employment_type="Full-time", description="Entry level firmware using C++, Python, Linux and STM32. Computer engineering degree.", required_skills=["C++", "Python", "Linux"], date_posted=datetime(2026, 8, 5, tzinfo=timezone.utc), remote_status="hybrid")
    score = score_job(job, PROFILE, PREFERENCES, now=datetime(2026, 8, 6, tzinfo=timezone.utc))
    expected = .40 * score.fit_score + .25 * score.competitiveness_score + .20 * score.preference_score + .15 * score.recency_score
    assert score.overall_score == pytest.approx(expected, abs=.1)
    assert score.priority_score != score.overall_score
    assert score.matching_skills


def test_experience_reduces_competitiveness() -> None:
    base = dict(title="Firmware Engineer", description="C++ firmware", required_skills=["C++"])
    junior = score_job(Job(**base, required_experience_years=1), PROFILE, PREFERENCES)
    experienced = score_job(Job(**base, required_experience_years=3), PROFILE, PREFERENCES)
    assert junior.competitiveness_score > experienced.competitiveness_score


def test_recommendation_categories() -> None:
    assert recommendation_for(85, False, PREFERENCES) == "Apply immediately"
    assert recommendation_for(70, False, PREFERENCES) == "Good match"
    assert recommendation_for(50, False, PREFERENCES) == "Stretch application"
    assert recommendation_for(90, True, PREFERENCES) == "Skip"


def test_unrelated_live_title_cannot_score_highly() -> None:
    job = Job(title="Legal Counsel", location="Costa Mesa, CA", description="Work with embedded software teams using Python and Linux.", date_posted=datetime(2026, 8, 6, tzinfo=timezone.utc))
    score = score_job(job, PROFILE, PREFERENCES, now=datetime(2026, 8, 6, tzinfo=timezone.utc))
    assert score.rejected
    assert score.priority_score <= 20


def test_embedded_title_variant_receives_primary_category_credit() -> None:
    job = Job(title="Embedded Linux Engineer", location="San Francisco, CA", description="Entry-level C++ and Linux firmware", required_skills=["C++", "Linux"])
    score = score_job(job, PROFILE, PREFERENCES)
    assert score.preference_score >= 70


def test_generic_engineer_titles_do_not_inherit_specialized_categories() -> None:
    now = datetime(2026, 8, 6, tzinfo=timezone.utc)
    for title in ("Software Engineer", "Systems Engineer", "Test Engineer"):
        job = Job(title=title, location="San Francisco, CA", description="Python and Linux", date_posted=now)
        score = score_job(job, PROFILE, PREFERENCES, now=now)
        assert score.preference_score <= 25
        assert score.priority_score < 70


def test_string_degree_profile_is_supported() -> None:
    score = score_job(Job(title="Engineer", description="Bachelor of Science in Computer Engineering"),
                      {"degree": "Bachelor of Science in Computer Engineering"}, {"eligibility": {}})
    assert score.fit_score > 0


def test_structured_projects_are_supported() -> None:
    profile = {"projects_and_technologies": [{"name": "Vehicle", "technologies": ["CAN", "RTOS"]}]}
    score = score_job(Job(title="Firmware Engineer", description="CAN RTOS firmware"), profile, {"eligibility": {}})
    assert score.fit_score > 0
