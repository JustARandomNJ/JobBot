from models.job import Job, JobScore
from storage.database import Database


def score(value: float = 75) -> JobScore:
    return JobScore(
        fit_score=value, competitiveness_score=value, preference_score=value,
        recency_score=value, priority_score=value, recommendation="Good match",
        detected_category="primary", detected_seniority="entry-level",
        matching_required_skills=["C++"], positive_reasons=["Relevant title"],
    )


def test_job_closes_only_after_two_successful_missing_scans(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job = Job(source="ashby", external_id="one", company="Acme", title="Firmware Engineer", apply_url="https://example.test/one")
    job_id, _ = database.upsert_job(job, scan_id=1)
    database.save_score(job_id, score())

    assert database.reconcile_company_scan("Acme", "ashby", set()) == 0
    after_one = database.get_job(job_id)
    assert after_one is not None and after_one["is_active"]
    assert after_one["missing_scan_count"] == 1

    # A failed collector never invokes reconciliation, so state remains unchanged.
    unchanged = database.get_job(job_id)
    assert unchanged is not None and unchanged["missing_scan_count"] == 1

    assert database.reconcile_company_scan("Acme", "ashby", set()) == 1
    closed = database.get_job(job_id)
    assert closed is not None and not closed["is_active"]
    assert closed["closed_at"] is not None


def test_seen_job_reactivates_and_preserves_review_and_status(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job = Job(source="lever", external_id="one", company="Acme", title="Firmware Engineer", apply_url="https://example.test/one")
    job_id, _ = database.upsert_job(job, scan_id=1)
    database.save_score(job_id, score())
    database.update_status(job_id, "applied")
    database.update_review(job_id, "strong-match", "MCU overlap")
    database.reconcile_company_scan("Acme", "lever", set())
    database.reconcile_company_scan("Acme", "lever", set())

    same_id, created = database.upsert_job(job, scan_id=4)
    refreshed = database.get_job(job_id)
    assert same_id == job_id and not created
    assert refreshed is not None and refreshed["is_active"]
    assert refreshed["missing_scan_count"] == 0 and refreshed["closed_at"] is None
    assert refreshed["status"] == "applied"
    assert refreshed["relevance"] == "strong match"
    assert refreshed["review_note"] == "MCU overlap"


def test_company_reconciliation_does_not_touch_other_companies(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    other = Job(source="ashby", external_id="two", company="Other", title="Firmware Engineer", apply_url="https://example.test/two")
    other_id, _ = database.upsert_job(other, scan_id=1)
    database.save_score(other_id, score())
    database.reconcile_company_scan("Acme", "ashby", set())
    stored = database.get_job(other_id)
    assert stored is not None and stored["missing_scan_count"] == 0


def test_active_filters_and_review_are_independent(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    first = Job(source="ashby", external_id="one", company="Acme", title="Firmware Engineer", apply_url="https://example.test/one")
    second = Job(source="ashby", external_id="two", company="Other", title="FPGA Engineer", apply_url="https://example.test/two")
    first_id, _ = database.upsert_job(first, scan_id=1)
    second_id, _ = database.upsert_job(second, scan_id=1)
    database.save_score(first_id, score(80))
    database.save_score(second_id, score(70))
    database.update_review(first_id, "possible", "Review requirements")
    database.reconcile_company_scan("Other", "ashby", set())
    database.reconcile_company_scan("Other", "ashby", set())

    active = database.list_ranked_jobs(active=True, company="Acme", limit=None)
    inactive = database.list_ranked_jobs(active=False, limit=None)
    assert [job["id"] for job in active] == [first_id]
    assert [job["id"] for job in inactive] == [second_id]
    assert active[0]["relevance"] == "possible"
    assert active[0]["status"] == "not reviewed"
