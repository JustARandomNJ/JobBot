from models.job import Job
from ranking.eligibility import evaluate_eligibility
from tests.conftest import load_config

PROFILE = {"work_authorization": {"authorized_in_us": True, "requires_sponsorship": False,
    "status": "lawful_permanent_resident", "us_citizen": False, "itar_us_person": True,
    "active_security_clearance": "none", "assume_standard_clearance_eligible": False}}

def defense(description: str, profile: dict = PROFILE):
    return evaluate_eligibility(Job(title="Embedded Engineer", description=description), load_config("preferences.yaml"), profile)

def test_itar_us_person_accepts_lpr() -> None:
    assert defense("Position requires status as an ITAR U.S. person.").defense_eligibility_status == "eligible"

def test_explicit_lpr_alternative_is_eligible() -> None:
    assert defense("Must be a U.S. citizen, lawful permanent resident, refugee, or asylee.").defense_eligibility_status == "eligible"

def test_citizenship_only_is_rejected() -> None:
    result = defense("U.S. citizenship is required for this position.")
    assert result.defense_eligibility_status == "ineligible_citizenship" and result.rejected

def test_active_secret_clearance_is_rejected() -> None:
    result = defense("An active Secret clearance is required.")
    assert result.defense_eligibility_status == "ineligible_clearance" and result.active_clearance_required

def test_active_clearance_without_level_is_rejected() -> None:
    assert defense("Active clearance required.").defense_eligibility_status == "ineligible_clearance"

def test_eligibility_to_obtain_secret_is_rejected() -> None:
    result = defense("Must be eligible to obtain and maintain a Secret clearance.")
    assert result.defense_eligibility_status == "ineligible_clearance" and result.clearance_eligibility_required

def test_ts_sci_eligibility_is_rejected() -> None:
    assert defense("Ability to obtain a TS/SCI clearance is mandatory.").defense_eligibility_status == "ineligible_clearance"

def test_clearance_preferred_needs_manual_review() -> None:
    assert defense("A Secret clearance is preferred.").defense_eligibility_status == "manual_review"


def test_obtainable_clearance_preferred_is_not_rejected() -> None:
    result = defense("Ability to obtain a Top Secret or Top Secret SCI clearance is preferred.")
    assert result.defense_eligibility_status == "manual_review"


def test_preferred_higher_level_does_not_weaken_active_requirement() -> None:
    result = defense("Active Secret clearance (Top Secret preferred).")
    assert result.defense_eligibility_status == "ineligible_clearance"


def test_following_preferred_heading_does_not_weaken_requirement() -> None:
    result = defense("Eligible to obtain and maintain an active U.S. Top Secret security clearance. PREFERRED QUALIFICATIONS: RF experience.")
    assert result.defense_eligibility_status == "ineligible_clearance"


def test_may_require_clearance_is_not_rejected() -> None:
    result = defense("US citizenship and ability to obtain security clearance may be required.")
    assert result.defense_eligibility_status == "manual_review"


def test_required_application_control_with_conditional_wording_is_not_mandatory() -> None:
    job = Job(title="Embedded Engineer", source_metadata={"eligibility_text": (
        '{"label": "CLEARANCE ELIGIBILITY - This position may require eligibility to obtain and maintain '
        'a U.S. security clearance.", "description": "Do you presently hold an active U.S. security '
        'clearance, or are you eligible to obtain and maintain one?", "required": true}'
    )})
    result = evaluate_eligibility(job, load_config("preferences.yaml"), PROFILE)
    assert result.defense_eligibility_status == "manual_review"


def test_required_application_control_with_mandatory_wording_is_rejected() -> None:
    job = Job(title="Embedded Engineer", source_metadata={"eligibility_text": (
        '{"label": "CLEARANCE ELIGIBILITY - This position requires eligibility to obtain and maintain '
        'a U.S. security clearance.", "required": true}'
    )})
    result = evaluate_eligibility(job, load_config("preferences.yaml"), PROFILE)
    assert result.defense_eligibility_status == "ineligible_clearance"
    assert result.clearance_eligibility_required and not result.active_clearance_required

def test_defense_and_government_language_alone_do_not_reject() -> None:
    for text in ("Build systems for defense customers.", "Support a government contract."):
        assert defense(text).defense_eligibility_status == "no_special_requirement"

def test_export_control_and_clearance_are_separate() -> None:
    result = defense("ITAR U.S. person status required. Must be eligible to obtain a Secret clearance.")
    assert result.export_control_requirement != "none" and result.defense_eligibility_status == "ineligible_clearance"

def test_candidate_configuration_controls_result() -> None:
    citizen = {"work_authorization": {**PROFILE["work_authorization"], "us_citizen": True}}
    assert defense("U.S. citizenship is required.", citizen).defense_eligibility_status == "no_special_requirement"

def test_high_technical_fit_cannot_override_clearance_rejection() -> None:
    from ranking.scorer import score_job
    profile = {**PROFILE, "expert_skills": ["C", "C++", "RTOS", "firmware"]}
    job = Job(title="Firmware Engineer", description="C C++ RTOS firmware. Must be eligible to obtain and maintain a Secret clearance.")
    score = score_job(job, profile, load_config("preferences.yaml"))
    assert score.rejected and score.priority_score <= 20 and score.recommendation == "Skip"

def test_production_secret_clearance_wording() -> None:
    result = defense("Eligible to obtain and maintain an active U.S. Secret security clearance.")
    assert result.defense_eligibility_status == "ineligible_clearance"

def test_must_be_eligible_for_us_clearance() -> None:
    assert defense("Security Clearance: Must be eligible for a US security clearance.").defense_eligibility_status == "ineligible_clearance"

def test_obtain_and_hold_clearance() -> None:
    assert defense("Must be able to obtain and hold a U.S. security clearance").defense_eligibility_status == "ineligible_clearance"

def test_html_and_line_break_clearance_text() -> None:
    assert defense("Eligible to obtain<br>and maintain\na U.S. security clearance").defense_eligibility_status == "ineligible_clearance"

def test_required_public_application_question_is_analyzed() -> None:
    job = Job(title="Embedded Linux Engineer", description="U.S. Person status required.", source_metadata={
        "eligibility_text": "CLEARANCE ELIGIBILITY - This position requires eligibility to obtain and maintain a U.S. security clearance.",
        "eligibility_text_complete": True,
    })
    assert evaluate_eligibility(job, load_config("preferences.yaml"), PROFILE).defense_eligibility_status == "ineligible_clearance"

def test_early_career_us_person_only_remains_eligible() -> None:
    result = defense("Early Career Firmware Engineer. U.S. Person status is required to access export controlled data.")
    assert result.defense_eligibility_status == "eligible"

def test_incomplete_public_inspection_is_manual_review() -> None:
    job = Job(title="Defense Firmware Engineer", source="greenhouse", source_metadata={"eligibility_text_complete": False})
    result = evaluate_eligibility(job, load_config("preferences.yaml"), PROFILE)
    assert result.defense_eligibility_status == "manual_review"
    assert any("could not be fully inspected" in reason for reason in result.defense_eligibility_reasons)

def test_same_title_can_have_different_eligibility() -> None:
    clear = defense("U.S. Person status required.")
    restricted = defense("Must be eligible for a US security clearance.")
    assert clear.defense_eligibility_status == "eligible"
    assert restricted.defense_eligibility_status == "ineligible_clearance"

def test_live_ts_and_obtain_hold_variants_are_mandatory() -> None:
    phrases = (
        "Must be eligible to obtain and maintain a U.S. TS clearance",
        "Be able to obtain and hold a U.S. Top Secret security clearance",
        "Must be eligible to obtain and maintain an active U.S. Secret or Top Secret security clearance",
        "Must be a U.S. person and eligible to attain a U.S. Security Clearance",
        "Able to obtain/maintain an active U.S. security clearance",
        "Currently possesses and is able to maintain an active U.S. Secret security clearance",
        "Secret required, Top Secret clearance preferred",
        "Eligible to apply for and maintain a US security clearance",
        "Eligible to obtain and maintain an active U.S. Top Secret SCI security clearance",
        "Ability too obtain and maintain a US Secret Clearance",
    )
    for phrase in phrases:
        assert defense(phrase).defense_eligibility_status == "ineligible_clearance", phrase


def test_senior_role_is_rejected() -> None:
    result = evaluate_eligibility(Job(title="Senior Firmware Engineering Manager"), load_config("preferences.yaml"))
    assert result.rejected
    assert any("senior" in reason.lower() for reason in result.reasons)


def test_unrelated_role_is_rejected() -> None:
    result = evaluate_eligibility(Job(title="Frontend Web Developer"), load_config("preferences.yaml"))
    assert result.rejected
    assert any("avoided" in reason.lower() for reason in result.reasons)


def test_two_to_three_years_is_flagged_not_rejected() -> None:
    result = evaluate_eligibility(Job(title="Firmware Engineer", required_experience_years=3), load_config("preferences.yaml"))
    assert not result.rejected
    assert result.flags


def test_five_years_is_rejected() -> None:
    result = evaluate_eligibility(Job(title="Firmware Engineer", required_experience_years=5), load_config("preferences.yaml"))
    assert result.rejected


def test_abbreviated_senior_and_chief_roles_are_rejected() -> None:
    preferences = load_config("preferences.yaml")
    assert evaluate_eligibility(Job(title="Sr. Systems Integration Engineer"), preferences).rejected
    assert evaluate_eligibility(Job(title="Chief Engineer"), preferences).rejected


def test_live_data_unrelated_titles_are_rejected() -> None:
    preferences = load_config("preferences.yaml")
    for title in ("Legal Counsel", "Full Stack Software Engineer", "Software Engineer- Backend Intern", "UI Engineer"):
        assert evaluate_eligibility(Job(title=title), preferences).rejected
