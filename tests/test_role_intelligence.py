from models.job import Job
from ranking.role_classifier import classify_role
from ranking.scorer import score_job
from storage.database import Database


def test_role_families_are_deterministic():
    cases = [
        ("Firmware Engineer", "MCU RTOS SPI embedded firmware", "embedded_firmware"),
        ("Embedded Linux Engineer", "Yocto device tree", "embedded_linux"),
        ("Linux Device Driver Engineer", "BSP kernel module", "bsp_drivers"),
        ("FPGA Engineer", "Vivado programmable logic", "fpga"),
        ("RTL Design Engineer", "ASIC Verilog synthesis", "asic_rtl"),
        ("Design Verification Engineer", "SystemVerilog UVM functional coverage", "asic_design_verification"),
        ("Hardware Validation Engineer", "oscilloscope JTAG hardware test", "hardware_test_validation"),
        ("Software Engineer", "Python services", "general_software"),
    ]
    for title, description, expected in cases:
        assert classify_role(Job(title=title, description=description)).role_family == expected


def test_real_world_title_patterns_classify_without_company_special_cases():
    cases = [
        ("Embedded Software Engineer - MCU Platforms", "microcontroller C RTOS", "embedded_firmware"),
        ("Embedded Software Engineer - Body Systems", "embedded controllers and CAN bus", "embedded_firmware"),
        ("Firmware Engineer I-II", "low-level firmware", "firmware"),
        ("Early Career Firmware Engineer", "firmware development", "firmware"),
        ("FPGA Engineer", "", "fpga"),
        ("Embedded Software Engineer", "Linux on NVIDIA Jetson with device drivers", "bsp_drivers"),
        ("Computer Vision & Machine Learning, Associate", "", "computer_vision_ml"),
    ]
    for title, description, expected in cases:
        assert classify_role(Job(title=title, description=description)).role_family == expected


def test_student_only_candidate_is_ineligible():
    score = score_job(Job(title="Firmware Intern", description="Applicants must be currently enrolled university students."),
                      {"current_student": False}, {"eligibility": {}})
    assert score.eligibility_status == "ineligible"
    assert score.eligibility_reasons[0]["code"] == "student_only"


def test_ambiguous_clearance_is_manual_review():
    score = score_job(Job(title="Engineer", description="This position may require a security clearance."), {}, {"eligibility": {}})
    assert score.eligibility_status == "manual_review"


def test_hard_experience_cutoff_is_ineligible():
    score = score_job(Job(title="Engineer", description="Requires extensive experience", required_experience_years=5),
                      {}, {"eligibility": {"reject_experience_years": 5}})
    assert score.eligibility_status == "ineligible"
    assert score.eligibility_reasons[0]["code"] == "experience_requirement"


def test_realistic_student_and_graduation_gates_are_not_unknown():
    profile = {"current_student": False, "work_authorization": {"authorized_in_us": True}}
    internship = score_job(Job(
        title="Flight Software Associate (Fall 2026)",
        description="Applicants must be currently enrolled and graduate between December 2026 and June 2027.",
    ), profile, {"eligibility": {}})
    assert internship.eligibility_status == "ineligible"
    assert {reason["code"] for reason in internship.eligibility_reasons} >= {"student_only", "graduation_window"}

    ambiguous = score_job(Job(
        title="Flight Software Associate",
        description="Candidates must graduate between December 2026 and June 2027.",
    ), {"work_authorization": {"authorized_in_us": True}}, {"eligibility": {}})
    assert ambiguous.eligibility_status == "manual_review"


def test_skip_reason_validation_and_storage(tmp_path):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    job_id, _ = db.upsert_job(Job(title="Engineer"))
    assert db.update_status(job_id, "skipped", skip_reason="student_only")
    with db.connect() as connection:
        assert connection.execute("SELECT skip_reason FROM application_status WHERE job_id=?", (job_id,)).fetchone()[0] == "student_only"
    try:
        db.update_status(job_id, "skipped", skip_reason="made_up")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid reason accepted")
