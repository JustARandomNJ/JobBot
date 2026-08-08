"""Normalized job and score models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    source: str = "unknown"
    external_id: str = ""
    title: str = "Untitled role"
    company: str = "Unknown company"
    location: str = "Unspecified"
    employment_type: str | None = None
    description: str = ""
    apply_url: str = ""
    salary_text: str | None = None
    date_posted: datetime | None = None
    date_discovered: datetime = Field(default_factory=utc_now)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    required_experience_years: float | None = None
    seniority: str | None = None
    remote_status: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def safe_text(cls, value: Any) -> str:
        return str(value).strip() if value not in (None, "") else "Unspecified"


class JobScore(BaseModel):
    fit_score: float = Field(ge=0, le=100)
    competitiveness_score: float = Field(ge=0, le=100)
    preference_score: float = Field(ge=0, le=100)
    recency_score: float = Field(ge=0, le=100)
    priority_score: float = Field(ge=0, le=100)
    detected_category: str | None = None
    detected_seniority: str | None = None
    matching_skills: list[str] = Field(default_factory=list)
    matching_required_skills: list[str] = Field(default_factory=list)
    matching_preferred_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)
    eligibility_flags: list[str] = Field(default_factory=list)
    citizenship_requirement: str = "none"
    export_control_requirement: str = "none"
    security_clearance_requirement: str = "none"
    required_clearance_level: str | None = None
    active_clearance_required: bool = False
    clearance_eligibility_required: bool = False
    work_authorization_eligibility: str = "eligible"
    defense_eligibility_status: str = "no_special_requirement"
    defense_eligibility_reasons: list[str] = Field(default_factory=list)
    eligibility_evidence_snippets: list[str] = Field(default_factory=list)
    positive_reasons: list[str] = Field(default_factory=list)
    negative_reasons: list[str] = Field(default_factory=list)
    rejected: bool = False
    explanation: str = ""
    recommendation: str
