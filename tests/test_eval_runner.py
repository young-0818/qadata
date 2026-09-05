import json

import pytest

from qadata.eval.bird import load_questions, run_eval
from tests.conftest import make_fixture_db
from tests.fakes import ScriptedLLM


def test_load_questions_sample_deterministic(tmp_path):
    data = [
        {"question_id": 0, "db_id": "school", "question": "q0", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"},
        {"question_id": 1, "db_id": "school", "question": "q1", "evidence": "", "SQL": "SELECT 2", "difficulty": "simple"},
        {"question_id": 2, "db_id": "school", "question": "q2", "evidence": "", "SQL": "SELECT 3", "difficulty": "simple"},
    ]
    p = tmp_path / "dev.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    qs = load_questions(str(p), sample=2, seed=42)
    assert len(qs) == 2
    assert load_questions(str(p), sample=2, seed=42) == qs  # 固定种子可复现


def test_run_eval_accuracy_with_fake_llm(tmp_path, monkeypatch):
    db_path = make_fixture_db(tmp_path)
    (tmp_path / "school").mkdir(exist_ok=True)
    import shutil
    shutil.move(db_path, tmp_path / "school" / "school.sqlite")
    db_dir = str(tmp_path)
    data = [
        {"question_id": 0, "db_id": "school", "question": "所有人名字", "evidence": "", "SQL": "SELECT name FROM students", "difficulty": "simple"},
        {"question_id": 1, "db_id": "school", "question": "错误题", "evidence": "", "SQL": "SELECT name FROM students", "difficulty": "simple"},
    ]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # runs/ 写到临时目录

    # fake LLM：第一题答对，第二题答错（两次 run_question 各配独立假模型）。
    # 终审修复：FakeListChatModel 在 M2 自纠错下静默循环（第二题实际 4 次调用，
    # 第 4 次靠循环供"q"恰好提取失败才维持结论）——换 ScriptedLLM 显式含重试行数，
    # 调用次数一旦变化立即报错而非碰运气。
    calls = {"n": 0}

    from qadata.graph.build import run_question

    def fake_run_question(db_path_, question_, evidence="", **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            llm1 = ScriptedLLM(["q", "SELECT name FROM students", "ok"])
            return run_question(db_path_, question_, llm=llm1)
        # 坏 SQL → 执行失败重试：预算 3 次共 3 次 generate；respond 失败路径不调 LLM
        llm2 = ScriptedLLM(["q"] + ["SELECT nope FROM students"] * 3)
        return run_question(db_path_, question_, llm=llm2)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    summary = run_eval(str(tmp_path / "dev.json"), db_dir)
    assert summary["total"] == 2
    assert summary["accuracy"] == 0.5
    records = [json.loads(l) for l in (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert records[0]["correct"] is True and records[1]["correct"] is False
    assert records[0]["error_class"] is None and records[0]["gold_sql"] == "SELECT name FROM students"
    assert records[1]["error_class"] == "answer_failed"
    assert summary["gold_failed"] == 0


def test_load_questions_by_ids_order(tmp_path):
    """question_ids 按给定顺序返回，不随机。"""
    data = [
        {"question_id": i, "db_id": "school", "question": f"q{i}", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"}
        for i in range(5)
    ]
    p = tmp_path / "dev.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    qs = load_questions(str(p), question_ids=[3, 1])
    assert [q["question_id"] for q in qs] == [3, 1]


def test_load_questions_unknown_id_raises(tmp_path):
    data = [{"question_id": 0, "db_id": "s", "question": "q", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"}]
    p = tmp_path / "dev.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="99"):
        load_questions(str(p), question_ids=[99])


def test_load_questions_ids_and_sample_conflict(tmp_path):
    data = [{"question_id": 0, "db_id": "s", "question": "q", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"}]
    p = tmp_path / "dev.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="不能同时"):
        load_questions(str(p), sample=1, question_ids=[0])


def test_run_one_gold_failed_flagged(tmp_path, monkeypatch):
    """gold 执行失败与 pred 失败分标（M1 §11.1 遗留）。"""
    from qadata.eval import bird
    from qadata.types import Answer

    # 目录约定：db_dir/{db_id}/{db_id}.sqlite
    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    monkeypatch.setattr(bird, "run_question",
                        lambda *a, **k: Answer(conclusion="ok", sql="SELECT 1", failed=False))
    q = {"question_id": 0, "db_id": "school", "question": "q", "evidence": "",
         "SQL": "SELECT nope FROM students", "difficulty": "simple"}
    rec = bird._run_one(q, str(tmp_path), None, 100, None)
    assert rec["correct"] is False
    assert rec["gold_failed"] is True and rec["error_class"] == "gold_failed"
    assert "gold 执行失败" in rec["error"]


def test_run_one_judge_mismatch_classified(tmp_path, monkeypatch):
    from qadata.eval import bird
    from qadata.types import Answer

    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    monkeypatch.setattr(bird, "run_question",
                        lambda *a, **k: Answer(conclusion="ok",
                                               sql="SELECT name FROM students WHERE id = 999", failed=False))
    q = {"question_id": 0, "db_id": "school", "question": "q", "evidence": "",
         "SQL": "SELECT name FROM students", "difficulty": "simple"}
    rec = bird._run_one(q, str(tmp_path), None, 100, None)
    assert rec["correct"] is False and rec["gold_failed"] is False
    assert rec["error_class"] == "judge_mismatch"


def test_run_eval_resume_skips_completed(tmp_path, monkeypatch):
    """断点续跑：已有记录跳过，只补跑缺失题，文件顺序与总量一致。"""
    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    db_dir = str(tmp_path)
    data = [
        {"question_id": 0, "db_id": "school", "question": "q0", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"},
        {"question_id": 1, "db_id": "school", "question": "q1", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"},
    ]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "eval-last.jsonl").write_text(
        json.dumps({"question_id": 0, "db_id": "school", "question": "q0", "gold_sql": "SELECT 1",
                    "gold_failed": False, "pred_sql": "SELECT 1", "correct": True,
                    "error": None, "error_class": None}, ensure_ascii=False) + "\n",
        encoding="utf-8")

    seen = []
    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        seen.append(question_)
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    summary = run_eval(str(tmp_path / "dev.json"), db_dir, resume=True)
    assert seen == ["q1"]  # 只补跑缺失题
    assert summary["total"] == 2 and summary["accuracy"] == 1.0
    lines = (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(l)["question_id"] for l in lines] == [0, 1]


def test_run_eval_flush_makes_progress_visible(tmp_path, monkeypatch):
    """逐题 flush：第 2 题开跑时第 1 题记录必须已落盘（M2 进度不可见痛点）。"""
    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    db_dir = str(tmp_path)
    data = [
        {"question_id": i, "db_id": "school", "question": f"q{i}", "evidence": "", "SQL": "SELECT 1", "difficulty": "simple"}
        for i in range(2)
    ]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        if question_ == "q1":
            lines = (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1  # q0 已 flush 落盘
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    run_eval(str(tmp_path / "dev.json"), db_dir)


def _make_three(tmp_path, monkeypatch):
    """并发测试共用脚手架：school 库 + 3 题 dev.json + chdir 隔离 runs/。"""
    make_fixture_db(tmp_path)
    import shutil
    (tmp_path / "school").mkdir(exist_ok=True)
    shutil.move(str(tmp_path / "school.sqlite"), str(tmp_path / "school" / "school.sqlite"))
    data = [
        {"question_id": i, "db_id": "school", "question": f"q{i}", "evidence": "",
         "SQL": "SELECT 1", "difficulty": "simple"}
        for i in range(3)
    ]
    (tmp_path / "dev.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_run_eval_concurrent_creates_shards_and_merged(tmp_path, monkeypatch):
    """并发分片：每题独立分片文件；out 合并按题目顺序；limiter 透传到 run_question。"""
    _make_three(tmp_path, monkeypatch)
    calls = []
    limiters = set()
    from qadata.types import Answer

    sentinel = object()

    def fake_run_question(db_path_, question_, evidence="", **kw):
        calls.append(question_)
        limiters.add(kw.get("limiter"))
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    summary = run_eval(str(tmp_path / "dev.json"), str(tmp_path), concurrency=3, limiter=sentinel)
    shard_dir = tmp_path / "runs" / "eval-shards"
    assert sorted(p.name for p in shard_dir.glob("*.jsonl")) == ["0.jsonl", "1.jsonl", "2.jsonl"]
    lines = (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(l)["question_id"] for l in lines] == [0, 1, 2]
    assert set(calls) == {"q0", "q1", "q2"}
    assert summary["total"] == 3 and summary["accuracy"] == 1.0
    assert limiters == {sentinel}


def test_run_eval_concurrent_resume_skips_completed_shards(tmp_path, monkeypatch):
    """并发 resume：按分片存在判断，不重复跑；out 由分片+旧记录合并重建。"""
    _make_three(tmp_path, monkeypatch)
    shard_dir = tmp_path / "runs" / "eval-shards"
    shard_dir.mkdir(parents=True)
    # 预置 0 分片：模拟上次跑到一半中断（q0 已完成，q1/q2 未跑）
    (shard_dir / "0.jsonl").write_text(
        json.dumps({"question_id": 0, "db_id": "school", "difficulty": "simple",
                    "question": "q0", "gold_sql": "SELECT 1", "gold_failed": False,
                    "pred_sql": "SELECT 1", "correct": True, "error": None,
                    "error_class": None}, ensure_ascii=False) + "\n", encoding="utf-8")

    seen = []
    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        seen.append(question_)
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    summary = run_eval(str(tmp_path / "dev.json"), str(tmp_path), resume=True, concurrency=3)
    assert seen == ["q1", "q2"] or set(seen) == {"q1", "q2"}
    assert summary["total"] == 3
    lines = (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(l)["question_id"] for l in lines] == [0, 1, 2]


def test_run_eval_concurrent_merge_idempotent(tmp_path, monkeypatch):
    """合并幂等：out 已有记录、无分片——重复合并不重复、不丢。"""
    _make_three(tmp_path, monkeypatch)
    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    run_eval(str(tmp_path / "dev.json"), str(tmp_path), concurrency=3)
    out = tmp_path / "runs" / "eval-last.jsonl"
    first = out.read_text(encoding="utf-8")
    # 第二次 resume：分片齐全 → 零调用，out 内容一字不差
    calls = {"n": 0}

    def counting(db_path_, question_, evidence="", **kw):
        calls["n"] += 1
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", counting)
    summary = run_eval(str(tmp_path / "dev.json"), str(tmp_path), resume=True, concurrency=3)
    assert calls["n"] == 0
    assert out.read_text(encoding="utf-8") == first
    assert summary["total"] == 3


def test_run_eval_skip_respond_plumbed(tmp_path, monkeypatch):
    """--skip-respond 透传：run_eval → _run_one → run_question 全链路。"""
    _make_three(tmp_path, monkeypatch)
    captured = {}
    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        captured["seen"] = captured.get("seen", 0) + 1
        captured["skip_respond"] = kw.get("skip_respond")
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    run_eval(str(tmp_path / "dev.json"), str(tmp_path), concurrency=3, skip_respond=True)
    assert captured["seen"] == 3
    assert captured["skip_respond"] is True


def test_run_eval_concurrent_traces_merged(tmp_path, monkeypatch):
    """traces 同分片：worker 写各自 traces 分片，主线程收口追加进 runs/traces.jsonl。"""
    _make_three(tmp_path, monkeypatch)
    from qadata.types import Answer

    def fake_run_question(db_path_, question_, evidence="", **kw):
        tracer = kw.get("tracer")
        if tracer is not None:
            tracer.log("generate", latency_ms=1, input_tokens=10, output_tokens=5, total_tokens=15)
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    run_eval(str(tmp_path / "dev.json"), str(tmp_path), concurrency=3)
    trace_lines = [json.loads(l) for l in
                   (tmp_path / "runs" / "traces.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert len(trace_lines) == 3
    assert {t["question_id"] for t in trace_lines} == {"0", "1", "2"}
    assert len({t["run_id"] for t in trace_lines}) == 1  # 同一次运行同一 run_id
