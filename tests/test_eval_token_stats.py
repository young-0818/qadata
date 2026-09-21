"""票 10：实测 token/延迟统计——逐题字段、tokens-<run_id>.json、账本自动记行、东八区 ts/latency_s。

测试语义：ScriptedLLM＋calls 计数（此处以假 run_question 直接驱动 TraceLogger，
真实图链路已由 test_graph/test_nodes 覆盖），零真实 API。
"""
import json
import re
from types import SimpleNamespace

import pytest

from qadata.eval.bird import run_eval
from qadata.types import Answer
from tests.conftest import make_fixture_db

STAT_KEYS = ("llm_calls", "input_tokens", "output_tokens", "total_tokens", "latency_s")


def _setup(tmp_path, monkeypatch, n=2):
    """tmp 下的 school 夹具库＋n 题 dev.json（判分 SQL 与假答案一致），chdir 隔离 runs/。"""
    db_path = make_fixture_db(tmp_path)
    (tmp_path / "school").mkdir(exist_ok=True)
    import shutil
    shutil.move(db_path, tmp_path / "school" / "school.sqlite")
    data = [{"question_id": i, "db_id": "school", "question": f"q{i}", "evidence": "",
             "SQL": "SELECT name FROM students", "difficulty": "simple"} for i in range(n)]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return str(tmp_path / "dev.json"), str(tmp_path)


def _recs(tmp_path, name="runs/eval-last.jsonl"):
    return [json.loads(l) for l in
            (tmp_path / name).read_text(encoding="utf-8").strip().splitlines()]


def _fake_run_question(tmp_ids_fail=()):
    """每题固定记 2 次 LLM 调用（1.5s+0.5s；14/7/21 in/out/total）。"""

    def fake(db_path_, question_, **kw):
        t = kw.get("tracer")
        if t is not None:
            t.log("understand", latency_s=1.5, input_tokens=10, output_tokens=5, total_tokens=15)
        if question_ in tmp_ids_fail:
            raise RuntimeError("模拟图内裸异常")
        if t is not None:
            t.log("generate", latency_s=0.5, input_tokens=4, output_tokens=2, total_tokens=6)
        return Answer(conclusion="c", sql="SELECT name FROM students", failed=False)

    return fake


def test_serial_records_carry_run_stats(tmp_path, monkeypatch):
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    run_eval(questions_path, db_dir, settings=SimpleNamespace(model="fake-model-v1"))

    records = _recs(tmp_path)
    assert len(records) == 2
    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 1 and re.fullmatch(r"[0-9a-f]{12}", run_ids.pop())
    for r in records:
        assert r["llm_calls"] == 2
        assert r["input_tokens"] == 14 and r["output_tokens"] == 7 and r["total_tokens"] == 21
        assert r["latency_s"] == 2.0


def test_failed_question_still_carries_stats(tmp_path, monkeypatch):
    """成本已烧的失败题不得漏计：异常前记过的调用要出现在记录里。"""
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question(tmp_ids_fail={"q1"}))
    run_eval(questions_path, db_dir)

    by_qid = {r["question_id"]: r for r in _recs(tmp_path)}
    fail = by_qid[1]
    assert fail["error_class"] == "answer_failed"
    assert fail["llm_calls"] == 1  # 只烧了 understand 一次
    assert fail["input_tokens"] == 10 and fail["total_tokens"] == 15
    assert fail["latency_s"] == 1.5
    assert by_qid[0]["llm_calls"] == 2


def test_tokens_file_and_totals(tmp_path, monkeypatch):
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    run_eval(questions_path, db_dir, settings=SimpleNamespace(model="fake-model-v1"))

    records = _recs(tmp_path)
    run_id = records[0]["run_id"]
    doc = json.loads((tmp_path / "runs" / f"tokens-{run_id}.json").read_text(encoding="utf-8"))
    assert doc["run_id"] == run_id
    assert doc["model"] == "fake-model-v1"
    assert doc["n_questions"] == 2
    assert doc["totals"] == {"llm_calls": 4, "input_tokens": 28, "output_tokens": 14,
                             "total_tokens": 42, "latency_s": 4.0}
    assert [q["question_id"] for q in doc["questions"]] == [0, 1]
    assert doc["questions"][0]["correct"] is True


def test_budget_ledger_appends_one_row(tmp_path, monkeypatch):
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| 日期 | 运行 | 题数×调用 | 估算口径 | 估算成本 | 累计 | 备注 |\n"
                      "|---|---|---|---|---|---|---|\n", encoding="utf-8")

    run_eval(questions_path, db_dir, budget_path=str(ledger),
             settings=SimpleNamespace(model="fake-model-v1"))

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # 表头两行＋本轮自动一行
    row = lines[2]
    assert row.startswith("|") and row.count("|") == 8  # 7 列表头同款
    assert "2 题 4 调用" in row
    assert "in 28 / out 14 / total 42 tokens" in row
    assert "待填" in row  # 成本折算保持人审
    assert "2/2" in row  # 准确率随行进账


def test_no_budget_flag_writes_nothing(tmp_path, monkeypatch):
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    run_eval(questions_path, db_dir)
    assert list((tmp_path / "runs").glob("ledger*")) == []


def test_concurrent_shard_stats_merge_into_one_run(tmp_path, monkeypatch):
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    run_eval(questions_path, db_dir, concurrency=2)

    records = _recs(tmp_path)
    assert len(records) == 2
    run_ids = {r["run_id"] for r in records}
    assert len(run_ids) == 1
    for r in records:  # 每题独立分片 tracer：各自 2 次调用，互不串账
        assert r["llm_calls"] == 2 and r["total_tokens"] == 21
    run_id = records[0]["run_id"]
    doc = json.loads((tmp_path / "runs" / f"tokens-{run_id}.json").read_text(encoding="utf-8"))
    assert doc["totals"]["llm_calls"] == 4


def test_concurrent_fallback_record_carries_stats(tmp_path, monkeypatch):
    """并发兜底路径（_run_one 裸异常）也要挂 run_id＋部分统计：已落账的调用不能丢。"""
    questions_path, db_dir = _setup(tmp_path, monkeypatch)

    def raiser(q, db_dir_, llm, max_rows, tracer, *a, **k):
        tracer.log("understand", latency_s=2.0, input_tokens=9, output_tokens=3, total_tokens=12)
        raise RuntimeError("分片裸异常")

    monkeypatch.setattr("qadata.eval.bird._run_one", raiser)
    run_eval(questions_path, db_dir, concurrency=2)

    records = _recs(tmp_path)
    assert len(records) == 2
    for r in records:
        assert r["error_class"] == "answer_failed" and "并发分片异常" in r["error"]
        assert len(r["run_id"]) == 12
        assert r["llm_calls"] == 1 and r["input_tokens"] == 9 and r["latency_s"] == 2.0


def test_resume_totals_only_this_round(tmp_path, monkeypatch):
    """断点续跑不重复计账：第二轮 tokens 合计只含新跑的题。"""
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("qadata.eval.bird.run_question", _fake_run_question())
    run_eval(questions_path, db_dir, question_ids=[0])
    run_eval(questions_path, db_dir, question_ids=[0, 1], resume=True)

    records = _recs(tmp_path)
    assert len(records) == 2 and len({r["run_id"] for r in records}) == 2
    run2 = records[1]["run_id"]
    doc = json.loads((tmp_path / "runs" / f"tokens-{run2}.json").read_text(encoding="utf-8"))
    assert doc["n_questions"] == 1
    assert doc["totals"] == {"llm_calls": 2, "input_tokens": 14, "output_tokens": 7,
                             "total_tokens": 21, "latency_s": 2.0}


def test_missing_budget_file_refuses_before_any_call(tmp_path, monkeypatch):
    """账本路径写错＝开跑前拒绝，绝不烧完整轮钱再报错；也不静默造无表头孤儿文件。"""
    questions_path, db_dir = _setup(tmp_path, monkeypatch)
    seen = {"n": 0}

    def counting(db_path_, question_, **kw):
        seen["n"] += 1
        return Answer(conclusion="c", sql="SELECT name FROM students", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", counting)
    with pytest.raises(FileNotFoundError):
        run_eval(questions_path, db_dir, budget_path=str(tmp_path / "nope.md"))
    assert seen["n"] == 0
    assert not (tmp_path / "nope.md").exists()
