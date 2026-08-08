from models.job import Job
from storage.database import Database
from models.job import JobScore


def test_duplicate_prevention_and_missing_fields(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job = Job(source="greenhouse", external_id="123", title="Firmware Engineer", company="Example", apply_url="https://example.com/job/123?utm_source=test")
    first_id, first_created = database.upsert_job(job)
    duplicate = job.model_copy(update={
        "apply_url": "https://example.com/job/123", "description": "Updated description",
        "location": "San Francisco, CA",
    })
    second_id, second_created = database.upsert_job(duplicate)
    assert first_created
    assert not second_created
    assert first_id == second_id
    stored = database.connect()
    with stored as connection:
        row = connection.execute("SELECT description, location FROM jobs WHERE id=?", (first_id,)).fetchone()
    assert row["description"] == "Updated description"
    assert row["location"] == "San Francisco, CA"
    assert job.description == ""
    assert job.date_posted is None


def test_refreshed_text_and_rescore_overwrite_previous_eligibility(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    original = Job(source="greenhouse", external_id="same", company="Acme", title="Embedded Linux Engineer", apply_url="https://example.test/same", description="U.S. Person status required")
    job_id, _ = database.upsert_job(original)
    database.save_score(job_id, JobScore(fit_score=80, competitiveness_score=80, preference_score=80, recency_score=80, priority_score=80, recommendation="Apply immediately", defense_eligibility_status="eligible"))
    refreshed = original.model_copy(update={"description": "Must be eligible for a US security clearance."})
    same_id, created = database.upsert_job(refreshed)
    assert same_id == job_id and not created
    database.save_score(job_id, JobScore(fit_score=80, competitiveness_score=10, preference_score=10, recency_score=80, priority_score=20, recommendation="Skip", rejected=True, defense_eligibility_status="ineligible_clearance"))
    stored = database.get_job(job_id)
    assert "eligible for a US security clearance" in stored["description"]
    assert stored["defense_eligibility_status"] == "ineligible_clearance"
