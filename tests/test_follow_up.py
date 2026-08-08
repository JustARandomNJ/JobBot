import sqlite3
from datetime import date, datetime, timezone

import app
from models.job import Job, JobScore
from ranking.follow_up import business_days_between, recommend_follow_up
from storage.database import Database


TODAY = date(2026, 8, 21)


def application(days="2026-08-10", **changes):
    value = {
        "id": 1, "company": "Acme", "title": "Firmware Engineer", "status": "applied",
        "applied_at": days, "priority_score": 80, "relevance": "strong match",
        "is_active": True, "follow_up_count": 0, "do_not_follow_up": False,
        "contacts": [], "follow_up_history": [], "next_follow_up_at": None,
    }
    value.update(changes)
    return value


def seeded_database(tmp_path):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    job_id, _ = database.upsert_job(Job(source="test", external_id="1", company="Acme", title="Firmware Engineer"))
    database.save_score(job_id, JobScore(fit_score=80, competitiveness_score=80, preference_score=80,
                                        recency_score=80, priority_score=80, recommendation="Apply immediately"))
    return database, job_id


def test_first_application_records_date_and_reapplying_preserves_it(tmp_path):
    database, job_id = seeded_database(tmp_path)
    database.update_status(job_id, "applied")
    first = database.get_job(job_id)
    with database.connect() as connection:
        applied = connection.execute("SELECT applied_at FROM application_status WHERE job_id=?", (job_id,)).fetchone()[0]
    assert applied is not None
    database.update_status(job_id, "saved")
    database.update_status(job_id, "applied", datetime(2020, 1, 1, tzinfo=timezone.utc))
    with database.connect() as connection:
        assert connection.execute("SELECT applied_at FROM application_status WHERE job_id=?", (job_id,)).fetchone()[0] == applied


def test_manual_application_date_works(tmp_path):
    database, job_id = seeded_database(tmp_path)
    database.update_status(job_id, "applied", datetime(2026, 8, 7, tzinfo=timezone.utc))
    assert database.follow_up_candidates()[0]["applied_at"].startswith("2026-08-07")


def test_business_days_skip_weekends():
    assert business_days_between(date(2026, 8, 7), date(2026, 8, 10)) == 1
    assert business_days_between(date(2026, 8, 7), date(2026, 8, 14)) == 5


def test_under_four_business_days_is_not_due():
    assert not recommend_follow_up(application("2026-08-18"), today=TODAY)["due"]


def test_five_to_seven_days_is_candidate_and_eight_to_twelve_is_more_urgent():
    early = recommend_follow_up(application("2026-08-14"), today=TODAY)
    urgent = recommend_follow_up(application("2026-08-10"), today=TODAY)
    assert early["due"] and urgent["due"]
    assert urgent["follow_up_score"] > early["follow_up_score"]


def test_very_old_application_has_diminishing_value():
    urgent = recommend_follow_up(application("2026-08-10"), today=TODAY)
    old = recommend_follow_up(application("2026-07-01"), today=TODAY)
    assert old["follow_up_score"] < urgent["follow_up_score"]


def test_terminal_and_interview_statuses_never_due():
    for status in ("rejected", "offer", "technical interview", "final interview", "skipped", "withdrawn"):
        assert not recommend_follow_up(application(status=status), today=TODAY)["due"]


def test_recruiter_screen_is_separate_from_unanswered_follow_up():
    result = recommend_follow_up(application(status="recruiter screen"), today=TODAY)
    assert not result["due"] and result["category"] == "recruiter_screen"


def test_inactive_posting_eliminates_due_recommendation():
    result = recommend_follow_up(application(is_active=False), today=TODAY)
    assert not result["due"]


def test_existing_follow_up_reduces_priority_and_default_prevents_repeat():
    first = recommend_follow_up(application(), today=TODAY)
    repeated = recommend_follow_up(application(follow_up_count=1), today=TODAY)
    assert repeated["follow_up_score"] < first["follow_up_score"]
    assert not repeated["due"] and repeated["category"] == "already_followed_up"


def test_do_not_follow_up_suppresses_recommendation():
    assert not recommend_follow_up(application(do_not_follow_up=True), today=TODAY)["due"]


def test_contact_quality_affects_score_and_no_contact_message_is_safe():
    unknown = [{"name": "Public inbox", "contact_type": "unknown", "verified": False}]
    referral = [{"name": "Jane", "contact_type": "referral", "verified": True}]
    low = recommend_follow_up(application(contacts=unknown), today=TODAY)
    high = recommend_follow_up(application(contacts=referral), today=TODAY)
    none = recommend_follow_up(application(), today=TODAY)
    assert high["follow_up_score"] > low["follow_up_score"]
    assert none["recommendation"] == "No direct follow-up recommended — continue waiting or pursue networking separately."


def test_follow_up_history_is_preserved_and_updates_summary(tmp_path):
    database, job_id = seeded_database(tmp_path)
    database.update_status(job_id, "applied", datetime(2026, 8, 1, tzinfo=timezone.utc))
    contact_id = database.add_contact(job_id, name="Jane", contact_type="recruiter", email="jane@example.com")
    database.record_follow_up(job_id, method="email", contact_id=contact_id, note="First")
    database.record_follow_up(job_id, method="phone", note="Second")
    history = database.follow_up_history(job_id)
    candidate = database.follow_up_candidates()[0]
    assert [item["note"] for item in history] == ["First", "Second"]
    assert candidate["follow_up_count"] == 2 and candidate["last_follow_up_at"]


def test_initialize_preserves_existing_application_data(tmp_path):
    database, job_id = seeded_database(tmp_path)
    database.update_status(job_id, "applied", datetime(2026, 8, 7, tzinfo=timezone.utc))
    database.initialize()
    candidate = database.follow_up_candidates()[0]
    assert candidate["status"] == "applied" and candidate["applied_at"].startswith("2026-08-07")


def test_existing_applied_status_without_date_stays_unknown(tmp_path):
    database, job_id = seeded_database(tmp_path)
    with database.connect() as connection:
        connection.execute("UPDATE application_status SET status='applied', applied_at=NULL WHERE job_id=?", (job_id,))
    database.initialize()
    database.update_status(job_id, "saved")
    database.update_status(job_id, "applied")
    assert database.follow_up_candidates()[0]["applied_at"] is None


def test_daily_selects_five_strong_unapplied_jobs(tmp_path, monkeypatch, capsys):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    for index in range(7):
        job_id, _ = database.upsert_job(Job(source="test", external_id=str(index), company="Acme", title=f"Engineer {index}"))
        database.save_score(job_id, JobScore(fit_score=90, competitiveness_score=90, preference_score=90,
                                            recency_score=90, priority_score=90-index, recommendation="Apply immediately"))
    monkeypatch.setattr(app, "load_configuration", lambda _: ({}, {"follow_up": {}}, {}))
    app.daily(database, tmp_path)
    output = capsys.readouterr().out
    assert output.split("Follow-ups worth doing today")[0].count("  #") == 5
