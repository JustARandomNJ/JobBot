"""Combine eligibility, skills, preferences, and recency into explainable scores."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from models.job import Job, JobScore
from ranking.eligibility import evaluate_eligibility
from ranking.skill_match import SkillMatch, match_skills
from ranking.role_classifier import classify_role
from ranking.posting_health import freshness_score
from ranking.priority import calculate_priority


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _profile_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [term for item in value for term in _profile_terms(item)]
    if isinstance(value, dict):
        return [term for item in value.values() for term in _profile_terms(item)]
    return []


def _category(job: Job, preferences: dict[str, Any]) -> tuple[str | None, float]:
    title = job.title.lower()
    for name, score in (("primary", 100.0), ("stretch", 78.0), ("backup", 68.0)):
        configured_titles = preferences.get("job_categories", {}).get(name, [])
        configured_keywords = preferences.get("job_category_keywords", {}).get(name, [])
        if any(category.lower() in title for category in configured_titles) or any(
            keyword.lower() in title for keyword in configured_keywords
        ):
            return name, score
    return None, 5.0


def _recency(job: Job, preferences: dict[str, Any], now: datetime) -> float:
    if job.date_posted is None:
        return 40.0
    posted = job.date_posted if job.date_posted.tzinfo else job.date_posted.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - posted).total_seconds() / 86400)
    config = preferences.get("scoring", {})
    full = float(config.get("recency_full_score_days", 7))
    zero = float(config.get("recency_zero_score_days", 90))
    if age <= full:
        return 100.0
    return _clamp(100.0 * (zero - age) / max(1.0, zero - full))


def recommendation_for(score: float, rejected: bool, preferences: dict[str, Any]) -> str:
    if rejected:
        return "Skip"
    thresholds = preferences.get("scoring", {}).get("recommendation_thresholds", {})
    if score >= float(thresholds.get("apply_immediately", 80)):
        return "Apply immediately"
    if score >= float(thresholds.get("good_match", 65)):
        return "Good match"
    if score >= float(thresholds.get("stretch_application", 45)):
        return "Stretch application"
    return "Skip"


def _reasons(job: Job, match: SkillMatch, category: str | None, flags: list[str], entry_signals: list[str]) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    if category:
        strengths.append(f"Title fits the configured {category} category")
    if match.matching:
        strengths.append(f"Profile overlap includes {', '.join(match.matching[:8])}")
    if entry_signals:
        strengths.append(f"Entry-level signals detected: {', '.join(entry_signals[:4])}")
    if not strengths:
        strengths.append("Limited direct title or skill overlap was detected")
    negatives = list(flags)
    if match.missing_required:
        negatives.append(f"Missing required skills: {', '.join(match.missing_required[:8])}")
    if match.missing_preferred:
        negatives.append(f"Missing preferred skills: {', '.join(match.missing_preferred[:8])}")
    if job.date_posted is None:
        negatives.append("Posting date is unknown, so recency receives conservative credit")
    return strengths, negatives


def _seniority(job: Job, entry_signals: list[str]) -> str:
    if job.seniority:
        return job.seniority
    title = job.title.lower()
    for label, terms in (
        ("senior", ("senior", "sr.", "sr ", "staff", "principal", "lead", "manager", "director", "chief")),
        ("entry-level", ("junior", "associate", "new grad", "early career", "engineer i", "intern")),
    ):
        if any(term in title for term in terms):
            return label
    return "entry-level" if entry_signals else "unspecified"


def score_job(job: Job, profile: dict[str, Any], preferences: dict[str, Any], now: datetime | None = None) -> JobScore:
    now = now or datetime.now(timezone.utc)
    eligibility = evaluate_eligibility(job, preferences, profile)
    match = match_skills(job, profile)
    category, category_score = _category(job, preferences)
    configured_degree = profile.get("degree", {})
    degree_terms = configured_degree.get("keywords", []) if isinstance(configured_degree, dict) else [str(configured_degree)]
    degree_relevance = 100.0 if any(term.lower() in job.description.lower() for term in degree_terms) else 55.0
    project_terms = _profile_terms(profile.get("projects_and_technologies", []))
    project_hits = sum(term.lower() in job.description.lower() for term in project_terms)
    project_score = min(100.0, project_hits * 20.0)
    fit = _clamp(0.55 * match.weighted_score + 0.25 * category_score + 0.10 * degree_relevance + 0.10 * project_score)

    years = job.required_experience_years or 0.0
    experience_score = 100.0 if years <= 1 else 82.0 if years <= 3 else 35.0 if years < 5 else 0.0
    new_grad_score = 100.0 if eligibility.entry_level_signals else 55.0
    preferred_gap_penalty = min(20.0, len(match.missing_preferred) * 4.0)
    competitiveness = _clamp(0.45 * match.required_match_percentage + 0.35 * experience_score + 0.20 * new_grad_score - preferred_gap_penalty)
    if eligibility.rejected:
        competitiveness = min(competitiveness, 10.0)

    location_terms = preferences.get("locations", {}).get("preferred_keywords", [])
    location_score = 100.0 if any(term.lower() in job.location.lower() for term in location_terms) else 35.0
    employment = (job.employment_type or "").lower()
    preferred_employment = preferences.get("employment", {}).get("preferred", [])
    acceptable_employment = preferences.get("employment", {}).get("acceptable", [])
    employment_score = 100.0 if any(term in employment for term in preferred_employment) else 75.0 if any(term in employment for term in acceptable_employment) else 55.0
    remote_score = 90.0 if (job.remote_status or "").lower() in {"remote", "hybrid"} else 60.0
    preference_score = _clamp(0.50 * category_score + 0.25 * location_score + 0.15 * employment_score + 0.10 * remote_score)
    if category is None:
        preference_score = min(preference_score, 25.0)
    if eligibility.rejected:
        preference_score = min(preference_score, 10.0)

    recency = _recency(job, preferences, now)
    weights = preferences.get("scoring", {}).get("weights", {})
    priority = _clamp(
        float(weights.get("fit", 0.40)) * fit
        + float(weights.get("competitiveness", 0.25)) * competitiveness
        + float(weights.get("preference", 0.20)) * preference_score
        + float(weights.get("recency", 0.15)) * recency
    )
    if eligibility.rejected:
        priority = min(priority, 20.0)
    recommendation = recommendation_for(priority, eligibility.rejected, preferences)
    if eligibility.defense_eligibility_status == "manual_review" and recommendation in {"Apply immediately", "Good match"}:
        recommendation = "Manual eligibility review"
    all_flags = eligibility.reasons + eligibility.flags
    positive_reasons, negative_reasons = _reasons(
        job, match, category, all_flags, eligibility.entry_level_signals
    )
    explanation = f"Recommended: {recommendation}. " + " ".join(f"{reason}." for reason in positive_reasons)
    if negative_reasons:
        explanation += " " + " ".join(f"{reason}." for reason in negative_reasons)
    role = classify_role(job)
    role_weights = profile.get("target_role_weights", {})
    # Profiles without v2 role preferences retain the historical aggregate
    # during migration. Once configured, priority gets its distinct meaning.
    application_priority, priority_factors = calculate_priority(
        overall_score=priority, eligibility=eligibility.eligibility_status,
        role_weight=role_weights.get(role.role_family) if role_weights else None,
        freshness=freshness_score(job.date_posted, preferences.get("posting_health", {}), now)[0],
        config=preferences.get("priority", {}),
    )
    if not role_weights and eligibility.eligibility_status not in {"ineligible", "manual_review"}:
        application_priority = priority
    return JobScore(
        fit_score=fit, competitiveness_score=competitiveness, preference_score=preference_score,
        recency_score=recency, priority_score=application_priority, detected_category=category,
        detected_seniority=_seniority(job, eligibility.entry_level_signals), matching_skills=match.matching,
        matching_required_skills=match.matching_required,
        matching_preferred_skills=match.matching_preferred,
        missing_required_skills=match.missing_required, missing_preferred_skills=match.missing_preferred,
        eligibility_flags=all_flags, positive_reasons=positive_reasons, negative_reasons=negative_reasons,
        citizenship_requirement=eligibility.citizenship_requirement,
        export_control_requirement=eligibility.export_control_requirement,
        security_clearance_requirement=eligibility.security_clearance_requirement,
        required_clearance_level=eligibility.required_clearance_level,
        active_clearance_required=eligibility.active_clearance_required,
        clearance_eligibility_required=eligibility.clearance_eligibility_required,
        work_authorization_eligibility=eligibility.work_authorization_eligibility,
        defense_eligibility_status=eligibility.defense_eligibility_status,
        defense_eligibility_reasons=eligibility.defense_eligibility_reasons,
        eligibility_evidence_snippets=eligibility.eligibility_evidence_snippets,
        rejected=eligibility.rejected, explanation=explanation, recommendation=recommendation,
        overall_score=priority, eligibility_status=eligibility.eligibility_status,
        eligibility_reasons=eligibility.structured_reasons, role_family=role.role_family,
        role_subfamily=role.role_subfamily, role_evidence=list(role.evidence),
        priority_factors=priority_factors,
    )
