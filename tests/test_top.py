import app
import pytest
from models.job import Job, JobScore
from storage.database import Database


def add_scored_job(database: Database, external_id: str, score: float, *, active: bool = True,
                   status: str = "not reviewed") -> int:
    job_id, _ = database.upsert_job(Job(
        source="ashby", external_id=external_id, company=f"Company {external_id}",
        title=f"Engineer {external_id}", apply_url=f"https://example.test/{external_id}",
    ))
    database.save_score(job_id, JobScore(
        fit_score=score, competitiveness_score=score, preference_score=score,
        recency_score=score, priority_score=score, recommendation="Good match",
        defense_eligibility_status="eligible",
    ))
    if status != "not reviewed":
        database.update_status(job_id, status)
    if not active:
        with database.connect() as connection:
            connection.execute("UPDATE jobs SET is_active=0 WHERE id=?", (job_id,))
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
