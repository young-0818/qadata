import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata.eval.bird import load_questions, run_eval
from tests.conftest import make_fixture_db


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

    # fake LLM：第一题答对，第二题答错（两次 run_question 各配独立假模型）
    calls = {"n": 0}

    from qadata.graph.build import run_question

    def fake_run_question(db_path_, question_, evidence="", llm=None, tracer=None):
        calls["n"] += 1
        if calls["n"] == 1:
            llm1 = FakeListChatModel(responses=["q", "SELECT name FROM students", "ok"])
            return run_question(db_path_, question_, llm=llm1)
        llm2 = FakeListChatModel(responses=["q", "SELECT nope FROM students", "ok"])
        return run_question(db_path_, question_, llm=llm2)

    monkeypatch.setattr("qadata.eval.bird.run_question", fake_run_question)
    summary = run_eval(str(tmp_path / "dev.json"), db_dir)
    assert summary["total"] == 2
    assert summary["accuracy"] == 0.5
    records = [json.loads(l) for l in (tmp_path / "runs" / "eval-last.jsonl").read_text(encoding="utf-8").strip().splitlines()]
    assert records[0]["correct"] is True and records[1]["correct"] is False
