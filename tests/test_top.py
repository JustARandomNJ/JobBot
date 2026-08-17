import app
import pytest
from models.job import Job, JobScore
from storage.database import Database


def add_scored_job(database: Database, external_id: str, score: float, *, active: bool = True,
                   status: str = "not reviewed", eligibility: str = "eligible",
                   relevance: str = "unreviewed") -> int:
    job_id, _ = database.upsert_job(Job(
        source="ashby", external_id=external_id, company=f"Company {external_id}",
        title=f"Engineer {external_id}", apply_url=f"https://example.test/{external_id}",
    ))
    database.save_score(job_id, JobScore(
        fit_score=score, competitiveness_score=score, preference_score=score,
        recency_score=score, priority_score=score, recommendation="Good match",
        defense_eligibility_status=eligibility, eligibility_status=eligibility,
    ))
    if status != "not reviewed":
        database.update_status(job_id, status)
    if not active:
        with database.connect() as connection:
            connection.execute("UPDATE jobs SET is_active=0 WHERE id=?", (job_id,))
    if relevance != "unreviewed":
        database.update_review(job_id, relevance)
    return job_id


def output_ids(output: str) -> list[int]:
    return [int(line.split(" |", 1)[0].removeprefix("===== #"))
            for line in output.splitlines() if line.startswith("===== #")]


def show_output(database_path, job_id: int, capsys) -> str:
    assert app.main(["--database", str(database_path), "show", str(job_id)]) == 0
    return capsys.readouterr().out.rstrip()


def top_job_outputs(output: str) -> list[str]:
    sections = output.rstrip().split("\n\n===== #")
    return [section.split("=====\n\n", 1)[1] for section in sections]


def daily_ids(output: str) -> list[int]:
    applications = output.split("Follow-ups worth doing today", 1)[0]
    return [int(line.strip().split(" |", 1)[0].removeprefix("#"))
            for line in applications.splitlines() if line.strip().startswith("#")]


def run_daily(database_path, tmp_path, monkeypatch, capsys, *, target: int = 5) -> list[int]:
    monkeypatch.setattr(app, "load_configuration", lambda _: ({}, {"follow_up": {}}, {}))
    assert app.main([
        "--database", str(database_path), "--config-dir", str(tmp_path),
        "daily", "--target", str(target),
    ]) == 0
    return daily_ids(capsys.readouterr().out)


def test_top_orders_by_score_and_defaults_to_five(tmp_path, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    ids_by_score = {
        score: add_scored_job(database, str(score), score)
        for score in (60, 90, 70, 100, 80, 50)
    }

    assert app.main(["--database", str(database_path), "top"]) == 0

    output = capsys.readouterr().out
    assert output_ids(output) == [ids_by_score[score] for score in (100, 90, 80, 70, 60)]
    assert "Rank 1/5" in output


def test_top_honors_custom_limit(tmp_path, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    expected = [add_scored_job(database, str(score), score) for score in range(70, 60, -1)]

    assert app.main(["--database", str(database_path), "top", "--limit", "10"]) == 0

    output = capsys.readouterr().out
    assert output_ids(output) == expected
    assert "Rank 10/10" in output


def test_top_rejects_non_positive_limit() -> None:
    with pytest.raises(SystemExit):
        app.build_parser().parse_args(["top", "--limit", "0"])


def test_top_excludes_inactive_and_non_actionable_application_statuses(tmp_path, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    actionable = add_scored_job(database, "saved", 70, status="saved")
    add_scored_job(database, "inactive", 100, active=False)
    for index, status in enumerate((
        "applied", "rejected", "withdrawn", "skipped", "recruiter screen",
        "technical interview", "final interview", "offer", "no response",
    )):
        add_scored_job(database, status, 99 - index, status=status)

    assert app.main(["--database", str(database_path), "top"]) == 0

    assert output_ids(capsys.readouterr().out) == [actionable]


def test_top_uses_the_same_detailed_rendering_as_show(tmp_path, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    expected_ids = [add_scored_job(database, str(score), score) for score in (90, 80)]
    expected_outputs = [show_output(database_path, job_id, capsys) for job_id in expected_ids]

    assert app.main(["--database", str(database_path), "top"]) == 0

    assert top_job_outputs(capsys.readouterr().out) == expected_outputs


def test_daily_and_top_have_identical_ids_and_order(tmp_path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    expected = [add_scored_job(database, str(score), score) for score in (75, 95, 85, 65, 55, 45)]
    expected = [expected[index] for index in (1, 2, 0, 3, 4)]

    from_daily = run_daily(database_path, tmp_path, monkeypatch, capsys)
    assert app.main(["--database", str(database_path), "top"]) == 0
    from_top = output_ids(capsys.readouterr().out)

    assert from_daily == from_top == expected


def test_top_custom_limit_matches_daily_without_backfill(tmp_path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    recommended = [add_scored_job(database, f"valid-{score}", score) for score in (90, 80, 70, 60)]
    add_scored_job(database, "reviewed", 100, relevance="strong match")
    add_scored_job(database, "manual", 99, eligibility="manual_review")

    from_daily = run_daily(database_path, tmp_path, monkeypatch, capsys, target=10)
    assert app.main(["--database", str(database_path), "top", "--limit", "10"]) == 0

    assert from_daily == output_ids(capsys.readouterr().out) == recommended


@pytest.mark.parametrize("status", ["applied", "skipped"])
def test_status_change_removes_job_from_daily_and_top(status, tmp_path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    removed = add_scored_job(database, "first", 100)
    replacement = add_scored_job(database, "replacement", 90)
    database.update_status(removed, status)

    assert run_daily(database_path, tmp_path, monkeypatch, capsys) == [replacement]
    assert app.main(["--database", str(database_path), "top"]) == 0
    assert output_ids(capsys.readouterr().out) == [replacement]


def test_daily_rules_exclude_inactive_ineligible_and_manual_review(tmp_path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "jobs.db"
    database = Database(database_path)
    database.initialize()
    included = add_scored_job(database, "included", 50)
    add_scored_job(database, "inactive", 100, active=False)
    add_scored_job(database, "ineligible", 99, eligibility="ineligible")
    add_scored_job(database, "manual", 98, eligibility="manual_review")

    assert run_daily(database_path, tmp_path, monkeypatch, capsys) == [included]
    assert app.main(["--database", str(database_path), "top"]) == 0
    assert output_ids(capsys.readouterr().out) == [included]


def test_top_delegates_selection_to_daily_recommendations(monkeypatch) -> None:
    selected = [{"id": 42}]
    database = object()
    calls = []
    monkeypatch.setattr(app, "get_daily_recommendations",
                        lambda actual_database, limit: calls.append((actual_database, limit)) or selected)
    monkeypatch.setattr(app, "render_detailed_jobs", lambda rows: calls.append(rows))

    app.top_jobs(database, 7)

    assert calls == [(database, 7), selected]
