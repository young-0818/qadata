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


def test_serve_shell_delegates_to_run_server(monkeypatch):
    """CLI 薄壳：serve 参数原样交给包内 run_server，不碰 uvicorn。"""
    import qadata.web.serve as serve_mod

    seen = {}
    monkeypatch.setattr(serve_mod, "run_server",
                        lambda **kw: seen.update(kw))
    code = cli_main.main(["serve", "--host", "0.0.0.0", "--port", "9"])
    assert code == 0
    assert seen == {"host": "0.0.0.0", "port": 9, "agents_dir": "data/agents"}


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


def test_report_with_types(tmp_path, capsys):
    import json

    def rec(qid, correct):
        return {"question_id": qid, "db_id": "s", "difficulty": "simple",
                "question": f"q{qid}", "correct": correct}

    b = tmp_path / "b.jsonl"
    c = tmp_path / "c.jsonl"
    t = tmp_path / "t.jsonl"
    b.write_text("\n".join(json.dumps(rec(i, True), ensure_ascii=False) for i in (0, 1)), encoding="utf-8")
    c.write_text("\n".join(json.dumps(rec(i, False), ensure_ascii=False) for i in (0, 1)), encoding="utf-8")
    t.write_text(json.dumps({"question_id": 0, "qtype": "极值"}, ensure_ascii=False), encoding="utf-8")
    code = cli_main.main(["report", "--baseline", str(b), "--current", str(c), "--types", str(t)])
    assert code == 0
    assert "题型切片" in capsys.readouterr().out


def test_report_paths_mode(tmp_path, capsys):
    """票 06：--paths 走出分路径报告（三个数＋判卷表），非默认 diff 报告。"""
    import json

    def rec(qid, correct, path):
        return {"question_id": qid, "db_id": "s", "difficulty": "simple",
                "question": f"q{qid}", "correct": correct, "path": path,
                "metric_name": "m" if path == "metric" else None,
                "template_fell_back": False}

    def round_records(path_for):
        return "\n".join(json.dumps(rec(i, True, path_for(i)), ensure_ascii=False)
                         for i in range(12)) + "\n"

    b = tmp_path / "b.jsonl"
    c = tmp_path / "c.jsonl"
    b.write_text(round_records(lambda i: "fallback"), encoding="utf-8")
    c.write_text(round_records(lambda i: "metric" if i < 10 else "fallback"), encoding="utf-8")
    code = cli_main.main(["report", "--baseline", str(b), "--current", str(c), "--paths"])
    assert code == 0
    out = capsys.readouterr().out
    assert "分路径报告" in out and "命中率" in out and "判卷" in out
    assert "失败样本" not in out  # 未走 diff 报告形态
