from app import DEFAULT_CONFIG, reanalyze
from models.job import Job, JobScore
from storage.database import Database


def test_reanalysis_refreshes_v2_fields_and_preserves_history_and_overall(tmp_path):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job_id, _ = database.upsert_job(Job(
        title="FPGA Engineer",
        description="Must be a U.S. person under ITAR. RTL Verilog timing constraints.",
    ))
    database.save_score(job_id, JobScore(
        fit_score=88, competitiveness_score=88, preference_score=88, recency_score=88,
        priority_score=88, overall_score=88, recommendation="Good match",
    ))
    assert database.update_status(job_id, "applied")

    assert reanalyze(database, DEFAULT_CONFIG) == 1
    refreshed = database.get_job(job_id)
    assert refreshed["role_family"] == "fpga"
    assert refreshed["eligibility_status"] == "eligible"
    assert refreshed["overall_score"] == 88
    assert refreshed["priority_score"] != refreshed["overall_score"]
    assert refreshed["analysis_version"] > 0
    assert refreshed["status"] == "applied"
