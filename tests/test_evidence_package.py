from ranking.evidence import extract_requirements, map_evidence
from ranking.company import saturation


def test_evidence_states_and_aliases():
    profile = {"expert_skills": ["C programming"], "developing_skills": ["Yocto"],
               "projects_and_technologies": [{"name": "Vehicle", "technologies": ["CAN bus"]}]}
    mapped = {x["requirement"]: x for x in map_evidence(["C", "CAN", "Yocto", "UVM"], profile)}
    assert mapped["C"]["state"] == "strong_evidence"
    assert mapped["CAN"]["evidence"] == "Vehicle"
    assert mapped["Yocto"]["state"] == "developing"
    assert mapped["UVM"]["state"] == "no_profile_evidence"


def test_extract_requirements():
    assert extract_requirements("Firmware", "C and an RTOS with CAN bus") == ["C", "CAN", "RTOS"]


def test_company_saturation_coherent_and_unrelated():
    coherent = [{"status": "applied", "role_family": "firmware"}] * 3
    assert saturation(coherent)["level"] == "moderate"
    mixed = [{"status": "applied", "role_family": x} for x in ("firmware", "fpga", "general_software", "computer_vision_ml")]
    assert saturation(mixed)["warning"]
