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


def test_ask_clarification_prints_and_exits_zero(fixture_db, monkeypatch, capsys):
    """M8 票 03：澄清不是失败——打印问句后 exit 0（CLI 无合成通道，续问把
    「补充说明：…」带进下一问题面即可——防循环标记闸认它）。"""
    import qadata.cli.main as m

    monkeypatch.setattr(m, "run_question",
                        lambda *a, **k: Answer(conclusion="「表现」指成绩还是违约率？",
                                               clarification="「表现」指成绩还是违约率？"))
    code = cli_main.main(["ask", fixture_db, "学生的表现如何"])
    assert code == 0
    assert "「表现」指成绩还是违约率？" in capsys.readouterr().out


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


# （M1 CLI --evidence 透传钉已随 ADR-0008 退役删除——flag 不复存在；ask 面零口径参数。）

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

