"""Case-insensitive, boundary-aware skill matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from models.job import Job


@dataclass
class SkillMatch:
    matching: list[str]
    matching_required: list[str]
    matching_preferred: list[str]
    missing_required: list[str]
    missing_preferred: list[str]
    required_match_percentage: float
    weighted_score: float


ALIASES: dict[str, tuple[str, ...]] = {
    "C": ("c language", "embedded c", "c programming"),
    "C++": ("c++", "cpp"),
    "RTOS": ("rtos", "real-time operating system", "real time operating system"),
    "Microcontrollers": ("microcontroller", "mcu"),
    "Hardware debugging": ("hardware debug", "debugging hardware"),
    "Board bring-up": ("board bring-up", "board bringup"),
}


def _mentions(text: str, skill: str) -> bool:
    terms = (skill, *ALIASES.get(skill, ()))
    if skill == "C":
        return any(re.search(rf"(?<![\w+]){re.escape(term.lower())}(?![\w+])", text.lower()) for term in terms)
    return any(re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text.lower()) for term in terms)


def match_skills(job: Job, profile: dict[str, Any]) -> SkillMatch:
    known = list(dict.fromkeys(profile.get("expert_skills", []) + profile.get("developing_skills", [])))
    developing = {skill.lower() for skill in profile.get("developing_skills", [])}
    job_required = job.required_skills
    job_preferred = job.preferred_skills
    text = " ".join((job.title, job.description, *job_required, *job_preferred))
    matching = [skill for skill in known if _mentions(text, skill)]
    matching_required = [skill for skill in job_required if any(_mentions(skill, known_skill) for known_skill in known)]
    matching_preferred = [skill for skill in job_preferred if any(_mentions(skill, known_skill) for known_skill in known)]
    missing_required = [skill for skill in job_required if not any(_mentions(skill, known_skill) for known_skill in known)]
    missing_preferred = [skill for skill in job_preferred if not any(_mentions(skill, known_skill) for known_skill in known)]
    matched_required = len(job_required) - len(missing_required)
    # Missing API structure is uncertainty, not evidence that every requirement is met.
    required_pct = 50.0 if not job_required else 100.0 * matched_required / len(job_required)
    expert_matches = sum(skill.lower() not in developing for skill in matching)
    developing_matches = len(matching) - expert_matches
    breadth_score = min(100.0, expert_matches * 11.0 + developing_matches * 6.0)
    weighted = 0.65 * required_pct + 0.35 * breadth_score
    return SkillMatch(matching, matching_required, matching_preferred, missing_required, missing_preferred, required_pct, round(weighted, 1))
