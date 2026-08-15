"""Deterministic general and defense/export-control eligibility rules."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any

from models.job import Job


@dataclass
class EligibilityResult:
    rejected: bool = False
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    entry_level_signals: list[str] = field(default_factory=list)
    citizenship_requirement: str = "none"
    export_control_requirement: str = "none"
    security_clearance_requirement: str = "none"
    required_clearance_level: str | None = None
    active_clearance_required: bool = False
    clearance_eligibility_required: bool = False
    work_authorization_eligibility: str = "eligible"
    defense_eligibility_status: str = "no_special_requirement"
    defense_eligibility_reasons: list[str] = field(default_factory=list)
    eligibility_evidence_snippets: list[str] = field(default_factory=list)
    eligibility_status: str = "unknown"
    structured_reasons: list[dict[str, str]] = field(default_factory=list)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase.lower())}(?!\w)", text.lower()) is not None


def _evidence(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 70)
    end = min(len(text), match.end() + 100)
    return " ".join(text[start:end].split())[:240]


def _find(text: str, patterns: list[str]) -> tuple[re.Match[str] | None, str | None]:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return match, _evidence(text, match)
    return None, None


def _public_question_entries(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode the collector's concatenated minimal public-question objects."""
    text = str(metadata.get("eligibility_text", ""))
    decoder = json.JSONDecoder()
    entries: list[dict[str, Any]] = []
    offset = 0
    while offset < len(text):
        offset += len(text[offset:]) - len(text[offset:].lstrip())
        try:
            value, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            entries.append(value)
    return entries


def _optional_clearance_context(text: str, match: re.Match[str]) -> bool:
    if re.search(r"\b(?:secret|top secret|TS/SCI) required\b", match.group(0), re.I):
        return False
    before = text[max(0, match.start() - 100):match.start()]
    after = text[match.end():min(len(text), match.end() + 100)]
    context = before + match.group(0) + after
    conditional = re.search(
        r"\b(?:encouraged|not required|no (?:security )?clearance required|"
        r"may (?:be )?require(?:d)?|required depending on program|depending on (?:the )?program|"
        r"clearance is preferred)\b",
        context,
        re.I,
    ) is not None
    preferred = re.search(r"preferred qualifications?", before, re.I) is not None or re.match(
        r"\s*(?:is\s+)?preferred\b", after, re.I
    ) is not None
    return conditional or preferred


def _find_mandatory_clearance(texts: list[str], patterns: list[str]) -> tuple[re.Match[str] | None, str | None]:
    for text in texts:
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I | re.S):
                if not _optional_clearance_context(text, match):
                    return match, _evidence(text, match)
    return None, None


def evaluate_eligibility(job: Job, preferences: dict[str, Any], profile: dict[str, Any] | None = None) -> EligibilityResult:
    config = preferences.get("eligibility", {})
    candidate = (profile or {}).get("work_authorization", {})
    result = EligibilityResult()
    title = job.title.lower()
    if any(_contains_phrase(title, term) for term in config.get("reject_seniority_keywords", [])):
        result.rejected = True
        result.reasons.append("Title indicates a senior or leadership role")
    if any(_contains_phrase(title, term) for term in config.get("unrelated_title_keywords", [])):
        result.rejected = True
        result.reasons.append("Title is in an avoided job category")
    years = job.required_experience_years
    if years is not None and years >= float(config.get("reject_experience_years", 5)) and not config.get("allow_experience_override", False):
        result.rejected = True
        result.reasons.append(f"Requires {years:g}+ years of experience")
    elif years is not None and years >= float(config.get("flag_experience_years", 2)):
        result.flags.append(f"Requests {years:g} years of experience; review equivalent experience language")
    result.entry_level_signals = [term for term in config.get("entry_level_keywords", []) if _contains_phrase(f"{title} {job.description[:1000]}", term)]
    preferred_locations = preferences.get("locations", {}).get("preferred_keywords", [])
    if preferred_locations and not any(term.lower() in job.location.lower() for term in preferred_locations):
        result.flags.append("Location may conflict with configured preferences")

    public_eligibility_text = str(job.source_metadata.get("eligibility_text", ""))
    text = clean_match_text = f"{job.title}\n{job.description}\n{public_eligibility_text}"
    # Normalize punctuation and whitespace while preserving evidence from the
    # public text. This handles HTML boundaries, line breaks, US/U.S., and
    # curly punctuation without weakening optional-clearance handling.
    clean_match_text = re.sub(r"\s+", " ", clean_match_text.replace("’", "'").replace("–", "-").replace("—", "-"))
    export_match, export_evidence = _find(text, [
        r"\bITAR\b", r"\bEAR\b", r"export[- ]control", r"\bU\.S\. person\b",
        r"lawful permanent resident", r"green card holder", r"protected individual under 8\s*U\.S\.C\.\s*1324b",
    ])
    if export_match:
        result.export_control_requirement = "us_person_or_export_control"
        if export_evidence:
            result.eligibility_evidence_snippets.append(export_evidence)

    lpr_alt, lpr_evidence = _find(text, [
        r"U\.S\. citizen.{0,100}(?:lawful permanent resident|green card|refugee|asylee|protected individual)",
        r"(?:lawful permanent resident|green card holder).{0,100}U\.S\. citizen",
    ])
    citizen, citizen_evidence = _find(text, [
        r"(?:must be|must be a|requires?|required to be) (?:an? )?U\.S\. citizen",
        r"U\.S\. citizenship (?:is )?required", r"U\.S\. citizens only", r"only U\.S\. citizens",
    ])
    clearance_patterns = [
        r"\bactive (?:security )?clearance\b",
        r"active\s+(?:DoD\s+)?(?:secret|top secret|TS/SCI)[^.\n]{0,80}(?:clearance|required)",
        r"must (?:possess|hold|have)[^.\n]{0,80}(?:security )?clearance",
        r"must (?:be able|be eligible|obtain|maintain)[^.\n]{0,100}(?:U\.?S\.? )?(?:DoD |personnel )?(?:secret |top secret |TS/SCI )?(?:security )?clearance",
        r"(?:must |be )?(?:eligible|able|ability) (?:to|for) (?:apply for and )?(?:obtain|attain)(?: and|/)? ?(?:hold|maintain)?[^.\n]{0,100}(?:U\.?S\.? )?(?:active )?(?:DoD |personnel )?(?:secret(?: or top secret)? |top secret |TS(?:/SCI)? )?(?:security )?clearance",
        r"eligible to apply for and maintain.{0,60}(?:U\.?S\.? )?(?:security )?clearance",
        r"(?:eligible|ability) too? obtain and maintain.{0,70}(?:U\.?S\.? )?(?:secret |top secret |TS(?:/SCI)? )?(?:security )?clearance",
        r"eligible to obtain and maintain.{0,70}top secret (?:SCI |TS/SCI )?(?:security )?clearance",
        r"currently possesses.{0,80}active.{0,50}(?:security )?clearance",
        r"(?:secret|top secret|TS/SCI) required",
        r"(?:requires?|required)[^.\n]{0,30}eligibility to obtain(?: and maintain)?[^.\n]{0,100}(?:security )?clearance",
        r"(?:eligible|eligibility|ability) (?:for|to obtain)(?: and maintain)?[^.\n]{0,100}(?:U\.?S\.? )?(?:active |DoD |personnel )*(?:secret(?: or top secret)? |top secret |TS(?:/SCI)? )?(?:security )?clearance",
        r"(?:secret|top secret|TS/SCI)[^.\n]{0,50}(?:clearance)[^.\n]{0,30}(?:required|mandatory)",
        r"clearance eligibility\s*[-:]\s*this position requires eligibility",
    ]
    clean_description = re.sub(r"\s+", " ", f"{job.title}\n{job.description}")
    question_texts: list[str] = []
    public_entries = _public_question_entries(job.source_metadata)
    for entry in public_entries:
        # A required form control only means an answer is required. The wording
        # must still make clearance a condition of this particular position.
        for key in ("label", "description"):
            entry_text = str(entry.get(key, ""))
            if re.search(r"\bthis position requires\b|\bthis role requires\b|\bmust\b|\brequired for this role\b", entry_text, re.I):
                question_texts.append(re.sub(r"\s+", " ", entry_text))
    if not public_entries and re.search(r"\bthis position requires\b|\bthis role requires\b|\bmust\b|\brequired for this role\b", public_eligibility_text, re.I):
        question_texts.append(re.sub(r"\s+", " ", public_eligibility_text))
    clearance, clearance_evidence = _find_mandatory_clearance(
        [clean_description, *question_texts], clearance_patterns
    )
    ambiguous, ambiguous_evidence = _find(text, [
        r"clearance (?:is )?preferred", r"may require (?:a )?(?:security )?clearance",
        r"clearance may be required", r"may require[^.\n]{0,180}clearance",
        r"clearance eligibility may be required", r"this position may require eligibility",
        r"ability to access classified environments?",
    ])
    if lpr_alt:
        result.citizenship_requirement = "us_person_including_lpr"
        if lpr_evidence:
            result.eligibility_evidence_snippets.append(lpr_evidence)
    elif citizen:
        result.citizenship_requirement = "us_citizen_only"
        if citizen_evidence:
            result.eligibility_evidence_snippets.append(citizen_evidence)
    if clearance:
        lowered = clearance.group(0).lower()
        result.required_clearance_level = "TS/SCI" if "ts/sci" in lowered else "Top Secret" if "top secret" in lowered else "Secret" if "secret" in lowered else "Unspecified"
        result.active_clearance_required = "active" in lowered or any(word in lowered for word in ("possess", "hold", "have"))
        result.clearance_eligibility_required = not result.active_clearance_required
        result.security_clearance_requirement = "active_required" if result.active_clearance_required else "obtainable_required"
        if clearance_evidence:
            result.eligibility_evidence_snippets.append(clearance_evidence)
    elif ambiguous:
        result.security_clearance_requirement = "ambiguous_or_preferred"
        if ambiguous_evidence:
            result.eligibility_evidence_snippets.append(ambiguous_evidence)

    authorized = bool(candidate.get("authorized_in_us", True))
    sponsorship_needed = bool(candidate.get("requires_sponsorship", False))
    if not authorized or sponsorship_needed:
        result.work_authorization_eligibility = "ineligible"
    inspection_complete = job.source_metadata.get("eligibility_text_complete", True)
    if result.citizenship_requirement == "us_citizen_only" and not candidate.get("us_citizen", False):
        result.defense_eligibility_status = "ineligible_citizenship"
        result.defense_eligibility_reasons.append("Job requires U.S. citizenship; candidate configuration says us_citizen=false")
    elif result.security_clearance_requirement in {"active_required", "obtainable_required"}:
        active = str(candidate.get("active_security_clearance", "none")).lower()
        can_obtain = bool(candidate.get("assume_standard_clearance_eligible", False))
        if (result.active_clearance_required and active == "none") or (result.clearance_eligibility_required and not can_obtain):
            result.defense_eligibility_status = "ineligible_clearance"
            result.defense_eligibility_reasons.append("Job mandates a standard U.S. security clearance that the candidate configuration does not satisfy")
    elif result.security_clearance_requirement == "ambiguous_or_preferred":
        result.defense_eligibility_status = "manual_review"
        result.defense_eligibility_reasons.append("Clearance language is conditional or preferred; manual review is required")
    elif result.citizenship_requirement == "us_person_including_lpr" or result.export_control_requirement != "none":
        if candidate.get("itar_us_person", False):
            result.defense_eligibility_status = "eligible"
            result.defense_eligibility_reasons.append("Export-control language accepts a U.S. person; candidate configuration says itar_us_person=true")
        else:
            result.defense_eligibility_status = "manual_review"
            result.defense_eligibility_reasons.append("Export-control eligibility is not established by candidate configuration")
    if inspection_complete is False and not result.defense_eligibility_status.startswith("ineligible_"):
        result.defense_eligibility_status = "manual_review"
        result.defense_eligibility_reasons.append("Warning: public eligibility fields could not be fully inspected")
    if result.defense_eligibility_status.startswith("ineligible_"):
        result.rejected = True
    # Consolidate existing authorization/defense logic with conservative general
    # hard-requirement parsing. Ambiguous language always routes to review.
    hard: list[tuple[str, str, str]] = []
    combined = re.sub(r"\s+", " ", f"{job.title}. {job.description}")
    candidate_profile = profile or {}
    current_student = candidate_profile.get("current_student")
    graduation = str(candidate_profile.get("graduation_date", ""))
    degree = candidate_profile.get("degree")
    degree_text = degree if isinstance(degree, str) else str((degree or {}).get("name", ""))
    checks = [
        ("student_only", r"(?:must be|requires?) (?:a )?currently enrolled|current(?:ly)? (?:university |college )?student|required to return to (?:school|university)", "Position requires current university enrollment."),
        ("graduation_window", r"graduat(?:e|ing|ion)[^.;]{0,80}(?:between|after|before)[^.;]{0,60}", "Posting specifies a graduation window."),
        ("drivers_license", r"(?:valid|current) driver'?s license (?:is )?(?:required|must)", "Position requires a driver's license."),
        ("relocation", r"must (?:be willing to )?relocate|relocation (?:is )?required", "Position requires relocation."),
        ("travel", r"(?:requires?|ability to) travel (?:up to )?(\d{1,3})%", "Position has a mandatory travel requirement."),
        ("onsite", r"(?:must|required to) (?:work|be) (?:on[- ]?site|in office)", "Position requires onsite work."),
    ]
    for code, pattern, reason in checks:
        match = re.search(pattern, combined, re.I)
        if match:
            if code == "student_only" and current_student is False:
                hard.append(("ineligible", code, reason))
            elif code == "graduation_window" and graduation:
                hard.append(("manual_review", code, reason + f" Candidate graduation is {graduation}."))
            elif code not in {"student_only", "graduation_window"}:
                hard.append(("manual_review", code, reason))
    degree_match = re.search(r"(?:bachelor'?s|master'?s|ph\.?d\.?|[A-Z]\.?S\.?) degree (?:is )?(?:required|minimum)", combined, re.I)
    if degree_match and not degree_text:
        hard.append(("manual_review", "degree", "Posting has a mandatory degree requirement not established by the profile."))
    if result.defense_eligibility_status.startswith("ineligible_"):
        code = "citizenship" if "citizenship" in result.defense_eligibility_status else "clearance"
        hard.append(("ineligible", code, "; ".join(result.defense_eligibility_reasons)))
    elif result.defense_eligibility_status == "manual_review":
        hard.append(("manual_review", "clearance", "; ".join(result.defense_eligibility_reasons)))
    if result.work_authorization_eligibility == "ineligible":
        hard.append(("ineligible", "sponsorship", "Candidate work authorization does not satisfy the posting."))
    result.structured_reasons = [{"code": code, "message": message} for _, code, message in hard]
    if any(level == "ineligible" for level, _, _ in hard):
        result.eligibility_status = "ineligible"
    elif any(level == "manual_review" for level, _, _ in hard):
        result.eligibility_status = "manual_review"
    elif job.description.strip():
        result.eligibility_status = "eligible"
    else:
        result.eligibility_status = "unknown"
    result.reasons.extend(result.defense_eligibility_reasons)
    result.flags.extend(result.eligibility_evidence_snippets)
    return result
