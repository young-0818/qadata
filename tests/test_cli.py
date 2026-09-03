import pytest

from qadata.cli import main as cli_main
from qadata.types import Answer, QueryResult


def test_ask_prints_answer(fixture_db, monkeypatch, capsys):
    import qadata.cli.main as m

    fake_answer = Answer(
        conclusion="Alice 成绩最好",
        sql="SELECT name FROM students WHERE id = 1",
        result=QueryResult(columns=["name"], rows=[("Alice",)], row_count=1, truncated=False, elapsed_ms=1),
    )
    monkeypatch.setattr(m, "run_question", lambda *a, **k: fake_answer)
    code = cli_main.main(["ask", fixture_db, "谁成绩最好"])
    out = capsys.readouterr().out
    assert code == 0 and "Alice 成绩最好" in out and "SELECT name" in out


def test_ask_failure_returns_zero_but_honest(fixture_db, monkeypatch, capsys):
    import qadata.cli.main as m

    monkeypatch.setattr(m, "run_question", lambda *a, **k: Answer(conclusion="未能完成查询：x", failed=True, error_summary="x"))
    code = cli_main.main(["ask", fixture_db, "q"])
    assert code == 0 and "未能完成查询" in capsys.readouterr().out


def test_missing_args_exit_nonzero():
    with pytest.raises(SystemExit) as e:
        cli_main.main(["ask"])
    assert e.value.code != 0


def test_ask_passes_evidence(fixture_db, monkeypatch):
    import qadata.cli.main as m
    from qadata.types import Answer

    captured = {}

    def fake_run_question(db_path, question, evidence="", **kw):
        captured["evidence"] = evidence
        return Answer(conclusion="ok", sql="SELECT 1")

    monkeypatch.setattr(m, "run_question", fake_run_question)
    cli_main.main(["ask", fixture_db, "问题", "--evidence", "A2 = district name"])
    assert captured["evidence"] == "A2 = district name"
