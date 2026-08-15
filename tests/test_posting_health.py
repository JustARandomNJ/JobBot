from datetime import datetime, timedelta, timezone

from models.job import Job
from ranking.posting_health import freshness_score, repost_risk
from storage.database import Database


def test_freshness_buckets():
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    assert freshness_score(now - timedelta(hours=12), now=now) == (100.0, "<1 day")
    assert freshness_score(now - timedelta(days=5), now=now)[0] == 78.0
    assert freshness_score(now - timedelta(days=40), now=now)[0] == 20.0
    assert freshness_score(None, now=now)[0] == 50.0


def test_observations_change_and_reopen(tmp_path):
    db = Database(tmp_path / "jobs.db"); db.initialize()
    job = Job(source="x", external_id="1", company="A", title="Engineer", description="same", apply_url="https://x/1")
    job_id, _ = db.upsert_job(job, scan_id=1)
    db.upsert_job(job, scan_id=2)
    changed = job.model_copy(update={"description": "changed"})
    db.upsert_job(changed, scan_id=3)
    with db.connect() as c:
        c.execute("UPDATE jobs SET is_active=0 WHERE id=?", (job_id,))
    db.upsert_job(changed, scan_id=4)
    with db.connect() as c:
        row = c.execute("""SELECT count(*) times_seen, sum(description_changed) description_changes,
                                  sum(reopened) reopened_count, max(CASE WHEN reopened=1 THEN observed_at END) reopened_at
                           FROM job_observations WHERE job_id=?""", (job_id,)).fetchone()
    assert row["times_seen"] == 4 and row["description_changes"] == 1
    assert row["reopened_count"] == 1 and row["reopened_at"]


def test_repost_risk_levels():
    assert repost_risk(age_days=2)[0] == "low"
    assert repost_risk(age_days=50)[0] == "moderate"
    assert repost_risk(age_days=100, reopened_count=2)[0] == "high"


def test_existing_database_is_backed_up_before_migration(tmp_path):
    path = tmp_path / "jobs.db"
    db = Database(path); db.initialize()
    with db.connect() as c:
        c.execute("DROP TABLE job_observations")
    Database(path).initialize()
    assert list((tmp_path / "backups").glob("jobs-*.db.bak"))
