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

    def fake_run_question(db_path_, question_, evidence="", llm=None, tracer=None):
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

    def fake_run_question(db_path_, question_, evidence="", llm=None, tracer=None):
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

    def fake_run_question(db_path_, question_, evidence="", llm=None, tracer=None):
        if question_ == "q1":
            lines = (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1  # q0 已 flush 落盘
        return Answer(conclusion="ok", sql="SELECT 1", failed=False)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    run_eval(str(tmp_path / "dev.json"), db_dir)
