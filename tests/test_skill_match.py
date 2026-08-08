import pytest

from models.job import Job
from ranking.skill_match import match_skills
from tests.conftest import load_config


def test_skill_matching_distinguishes_required_gaps() -> None:
    job = Job(title="Embedded Engineer", description="Develop firmware in C++ on Linux.", required_skills=["C++", "Linux", "Rust"], preferred_skills=["Zephyr RTOS", "Kubernetes"])
    result = match_skills(job, load_config("candidate_profile.yaml"))
    assert {"C++", "Linux", "Zephyr RTOS"}.issubset(result.matching)
    assert result.missing_required == ["Rust"]
    assert result.missing_preferred == ["Kubernetes"]
    assert result.required_match_percentage == pytest.approx(2 / 3 * 100)


def test_c_does_not_match_every_letter_c() -> None:
    result = match_skills(Job(title="Account Executive", description="Customer accounts"), load_config("candidate_profile.yaml"))
    assert "C" not in result.matching


def test_missing_structured_requirements_are_neutral_not_perfect() -> None:
    result = match_skills(Job(title="Firmware Engineer", description="Firmware in C++"), load_config("candidate_profile.yaml"))
    assert result.required_match_percentage == 50
