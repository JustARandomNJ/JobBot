import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

import app
from collectors.greenhouse import GreenhouseCollector
from models.job import Job, JobScore
from storage.database import Database


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.payload


def listing(*ids, updated="2026-08-01T00:00:00Z"):
    return {"jobs": [{"id": value, "title": f"Engineer {value}", "content": "Firmware",
                       "absolute_url": f"https://example.test/{value}", "updated_at": updated}
                      for value in ids]}


class DetailSession:
    def __init__(self, board, detail):
        self.board, self.detail, self.calls = board, detail, []
        self.lock = threading.Lock()

    def get(self, url, **kwargs):
        with self.lock:
            self.calls.append(url)
        if url.endswith("/jobs"):
            return Response(self.board)
        action = self.detail[str(url.rsplit("/", 1)[-1])]
        if isinstance(action, list):
            action = action.pop(0)
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            return action()
        return action


def test_one_timeout_does_not_lose_other_jobs():
    session = DetailSession(listing(1, 2), {"1": requests.Timeout("slow"), "2": Response({"id": 2})})
    collector = GreenhouseCollector(session=session, retries=0, workers=2)
    jobs = collector.collect("Acme", "acme")
    assert {job.external_id for job in jobs} == {"1", "2"}
    assert next(j for j in jobs if j.external_id == "1").source_metadata["eligibility_text_complete"] is False
    assert collector.stats["incomplete_details"] == 1


def test_completed_detail_is_consumed_without_ordered_blocking():
    def slow():
        time.sleep(.08)
        return Response({"id": 1})
    session = DetailSession(listing(1, 2), {"1": slow, "2": Response({"id": 2})})
    jobs = GreenhouseCollector(session=session, retries=0, workers=2).collect("Acme", "acme")
    assert jobs[0].external_id == "2"


def test_timeout_retry_can_succeed(monkeypatch):
    monkeypatch.setattr("collectors.base.time.sleep", lambda _: None)
    session = DetailSession(listing(1), {"1": [requests.Timeout("slow"), Response({"id": 1})]})
    collector = GreenhouseCollector(session=session, retries=2)
    assert collector.collect("Acme", "acme")[0].source_metadata["eligibility_text_complete"]
    assert collector.stats["retries"] == 1


def test_retry_exhaustion_keeps_incomplete_job(monkeypatch):
    monkeypatch.setattr("collectors.base.time.sleep", lambda _: None)
    session = DetailSession(listing(1), {"1": [requests.Timeout("a"), requests.Timeout("b"), requests.Timeout("c")]})
    job = GreenhouseCollector(session=session, retries=2).collect("Acme", "acme")[0]
    assert not job.source_metadata["eligibility_text_complete"]
    assert job.source_metadata["detail_inspection_status"] == "failed"


def test_429_retries_and_honors_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("collectors.base.time.sleep", sleeps.append)
    session = DetailSession(listing(1), {"1": [Response(status=429, headers={"Retry-After": "3"}), Response({"id": 1})]})
    GreenhouseCollector(session=session, retries=1).collect("Acme", "acme")
    assert sleeps == [3.0]


def test_404_is_not_retried(monkeypatch):
    monkeypatch.setattr("collectors.base.time.sleep", lambda _: pytest.fail("should not sleep"))
    session = DetailSession(listing(1), {"1": Response(status=404)})
    job = GreenhouseCollector(session=session, retries=2).collect("Acme", "acme")[0]
    assert not job.source_metadata["eligibility_text_complete"]
    assert len(session.calls) == 2


def successful_cache(item):
    marker = GreenhouseCollector._marker(item)
    return {"detail_source_marker": marker, "eligibility_text_complete": True,
            "eligibility_text": "U.S. person required", "eligibility_text_sources": ["questions"],
            "detail_inspection_status": "complete"}


def test_unchanged_reuses_cache_and_new_job_is_inspected():
    board = listing(1, 2)
    session = DetailSession(board, {"2": Response({"id": 2})})
    collector = GreenhouseCollector(session=session, cached_details={"1": successful_cache(board["jobs"][0])})
    collector.collect("Acme", "acme")
    assert collector.stats["cached_reused"] == 1 and collector.stats["detail_requests"] == 1


def test_changed_job_refreshes_detail():
    old = listing(1, updated="2026-07-01T00:00:00Z")["jobs"][0]
    session = DetailSession(listing(1), {"1": Response({"id": 1})})
    collector = GreenhouseCollector(session=session, cached_details={"1": successful_cache(old)})
    collector.collect("Acme", "acme")
    assert collector.stats["detail_requests"] == 1


def test_failed_inspection_retried_after_interval():
    board = listing(1)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    cache = {"detail_source_marker": GreenhouseCollector._marker(board["jobs"][0]),
             "eligibility_text_complete": False, "detail_inspection_attempted_at": old}
    collector = GreenhouseCollector(session=DetailSession(board, {"1": Response({"id": 1})}),
                                    cached_details={"1": cache}, retry_interval=60)
    collector.collect("Acme", "acme")
    assert collector.stats["detail_requests"] == 1


def test_force_refresh_inspects_every_job():
    board = listing(1, 2)
    cache = {str(i["id"]): successful_cache(i) for i in board["jobs"]}
    session = DetailSession(board, {"1": Response({"id": 1}), "2": Response({"id": 2})})
    collector = GreenhouseCollector(session=session, cached_details=cache, force_detail_refresh=True)
    collector.collect("Acme", "acme")
    assert collector.stats["detail_requests"] == 2


def test_interrupted_scan_records_history_without_missing_advance(tmp_path, monkeypatch):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job_id, _ = database.upsert_job(Job(source="ashby", external_id="1", company="Acme", title="Engineer"))
    database.save_score(job_id, JobScore(fit_score=1, competitiveness_score=1, preference_score=1,
                                        recency_score=1, priority_score=1, recommendation="Review"))
    class Interrupted:
        def collect(self, company, identifier):
            raise KeyboardInterrupt
    monkeypatch.setattr(app, "load_configuration", lambda _: ({}, {}, {"companies": [
        {"name": "Acme", "source": "ashby", "identifier": "a", "enabled": True}]}))
    monkeypatch.setitem(app.COLLECTORS, "ashby", Interrupted)
    assert app.scan(database, tmp_path) == 130
    assert database.get_job(job_id)["missing_scan_count"] == 0
    with database.connect() as connection:
        row = connection.execute("SELECT outcome, completed_at FROM scan_history ORDER BY id DESC").fetchone()
    assert row["outcome"] == "interrupted" and row["completed_at"]


def test_interrupt_during_company_persistence_rolls_back_company(tmp_path, monkeypatch):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    existing_id, _ = database.upsert_job(Job(source="ashby", external_id="old", company="Acme", title="Old"))
    database.save_score(existing_id, JobScore(fit_score=1, competitiveness_score=1, preference_score=1,
                                              recency_score=1, priority_score=1, recommendation="Review"))
    class Collector:
        def collect(self, company, identifier):
            return [Job(source="ashby", external_id="new", company="Acme", title="New")]
    monkeypatch.setattr(app, "load_configuration", lambda _: ({}, {}, {"companies": [
        {"name": "Acme", "source": "ashby", "identifier": "a", "enabled": True}]}))
    monkeypatch.setitem(app.COLLECTORS, "ashby", Collector)
    monkeypatch.setattr(app, "score_job", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert app.scan(database, tmp_path) == 130
    assert database.get_job(existing_id)["missing_scan_count"] == 0
    assert all(job.external_id != "new" for _, job in database.all_jobs())
