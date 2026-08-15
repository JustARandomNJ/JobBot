from pathlib import Path

import app


def test_missing_answer_bank_is_graceful(tmp_path, capsys):
    assert app.main(["--config-dir", str(tmp_path), "answers"]) == 1
    assert "not found" in capsys.readouterr().err


def test_answer_bank_lists_keys_and_gets_value(tmp_path, capsys):
    (tmp_path / "application_answers.yaml").write_text("work_authorization: Authorized\ngithub: https://example.test\n", encoding="utf-8")
    assert app.main(["--config-dir", str(tmp_path), "answers"]) == 0
    output = capsys.readouterr().out
    assert "work_authorization" in output and "Authorized" not in output
    assert app.main(["--config-dir", str(tmp_path), "answers", "--get", "work_authorization"]) == 0
    assert capsys.readouterr().out.strip() == "Authorized"


def test_example_is_present_and_real_file_is_ignored():
    root = Path(__file__).parents[1]
    assert (root / "config" / "application_answers.example.yaml").exists()
    assert "config/application_answers.yaml" in (root / ".gitignore").read_text(encoding="utf-8")
