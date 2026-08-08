from datetime import datetime, timezone

import app
from models.job import Job
from storage.database import Database


def add_job(database: Database, external_id: str, company: str, title: str) -> int:
    job_id, _ = database.upsert_job(Job(
        source="ashby", external_id=external_id, company=company, title=title,
        apply_url=f"https://example.test/{external_id}",
    ))
    return job_id


def test_list_applications_filters_and_sorts_by_latest_activity(tmp_path) -> None:
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    untouched = add_job(database, "untouched", "Allen Control Systems", "Test Engineer")
    applied = add_job(database, "applied", "Allen Control Systems", "Firmware Engineer")
    rejected = add_job(database, "rejected", "Other Company", "Controls Engineer")
    saved = add_job(database, "saved", "allen control systems division", "Embedded Engineer")

    database.update_status(applied, "applied", datetime(2026, 7, 1, tzinfo=timezone.utc))
    database.update_status(rejected, "rejected")
    database.update_status(saved, "saved")
    with database.connect() as connection:
        connection.execute("UPDATE application_status SET updated_at=? WHERE job_id=?", ("2026-08-01T00:00:00+00:00", applied))
        connection.execute("UPDATE application_status SET updated_at=? WHERE job_id=?", ("2026-08-03T00:00:00+00:00", rejected))
        connection.execute("UPDATE application_status SET updated_at=? WHERE job_id=?", ("2026-08-02T00:00:00+00:00", saved))

    assert [row["id"] for row in database.list_applications()] == [rejected, saved, applied]
    assert untouched not in [row["id"] for row in database.list_applications()]
    assert [row["id"] for row in database.list_applications(status="applied")] == [applied]
    assert [row["id"] for row in database.list_applications(company="ALLEN CONTROL")] == [saved, applied]
    assert database.list_applications(status="rejected", company="Allen Control Systems") == []


def test_applications_cli_displays_required_fields(tmp_path, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    job_id = add_job(database, "one", "Allen Control Systems", "Firmware Engineer")
    database.update_status(job_id, "applied", datetime(2026, 8, 1, tzinfo=timezone.utc))
    with database.connect() as connection:
        connection.execute("UPDATE jobs SET is_active=0 WHERE id=?", (job_id,))
        connection.execute("UPDATE application_status SET updated_at=? WHERE job_id=?", ("2026-08-04T12:00:00+00:00", job_id))

    assert app.main(["--database", str(database_path), "applications", "--status", "applied", "--company", "ALLEN"]) == 0
    output = capsys.readouterr().out
    assert f"Job ID: {job_id}" in output
    assert "Company: Allen Control Systems" in output
    assert "Job title: Firmware Engineer" in output
    assert "Application status: applied" in output
    assert "Applied date: 2026-08-01" in output
    assert "Last status update: 2026-08-04" in output
    assert "Posting state: inactive" in output
