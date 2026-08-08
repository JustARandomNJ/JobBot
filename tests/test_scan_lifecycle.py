import requests

import app
from models.job import Job, JobScore
from storage.database import Database


def basic_score() -> JobScore:
    return JobScore(
        fit_score=70, competitiveness_score=70, preference_score=70,
        recency_score=70, priority_score=70, recommendation="Good match",
    )


def config() -> tuple[dict, dict, dict]:
    return {}, {}, {"companies": [{
        "name": "Acme", "source": "ashby", "identifier": "acme", "enabled": True,
    }]}


def test_failed_company_scan_does_not_advance_missing_count(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job = Job(source="ashby", external_id="one", company="Acme", title="Firmware Engineer", apply_url="https://example.test/one")
    job_id, _ = database.upsert_job(job)
    database.save_score(job_id, basic_score())

    class FailingCollector:
        def collect(self, company: str, identifier: str) -> list[Job]:
            raise requests.Timeout("temporary failure")

    monkeypatch.setattr(app, "load_configuration", lambda _: config())
    monkeypatch.setitem(app.COLLECTORS, "ashby", FailingCollector)
    assert app.scan(database, tmp_path) == 1
    stored = database.get_job(job_id)
    assert stored is not None and stored["missing_scan_count"] == 0 and stored["is_active"]


def test_two_successful_empty_scans_close_missing_job(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job = Job(source="ashby", external_id="one", company="Acme", title="Firmware Engineer", apply_url="https://example.test/one")
    job_id, _ = database.upsert_job(job)
    database.save_score(job_id, basic_score())

    class EmptyCollector:
        def collect(self, company: str, identifier: str) -> list[Job]:
            return []

    monkeypatch.setattr(app, "load_configuration", lambda _: config())
    monkeypatch.setitem(app.COLLECTORS, "ashby", EmptyCollector)
    assert app.scan(database, tmp_path) == 0
    assert database.get_job(job_id)["is_active"]
    assert app.scan(database, tmp_path) == 0
    assert not database.get_job(job_id)["is_active"]
