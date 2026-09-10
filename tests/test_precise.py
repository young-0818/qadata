"""多候选自一致性票决测试（M4-C）：纯函数层＋图级账本（批量=一轮）。"""
import json

from qadata.config import Settings
from qadata.graph.build import build_graph
from qadata.graph.precise import NO_MAJORITY_ERROR, run_precise_batch
from qadata.llm.tracing import TraceLogger
from tests.fakes import ScriptedLLM


def test_majority_wins_and_vetoes_do_not_flood_ledger(fixture_db, tmp_path):
    """多数派胜出：等价结果两票压一票坏 SQL；否决候选不进 attempts，批量统计进 tracer。"""
    cands = ["SELECT name FROM students",
             "SELECT name FROM students WHERE id > 0",
             "SELECT nope FROM students"]
    tracer = TraceLogger(tmp_path / "t.jsonl")
    outcome = run_precise_batch(fixture_db, cands, max_rows=50, timeout_s=5.0, tracer=tracer)
    assert outcome.winner_sql in cands[:2]
    assert outcome.winner_result.row_count == 2
    assert outcome.no_majority is False
    assert outcome.veto_count == 1
    lines = [json.loads(l) for l in
             (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r["node"] == "precise" and r.get("majority") is True for r in lines)


def test_no_majority_when_all_results_differ(fixture_db):
    cands = ["SELECT name FROM students", "SELECT id FROM students",
             "SELECT grade FROM students"]
    outcome = run_precise_batch(fixture_db, cands, max_rows=50, timeout_s=5.0, tracer=None)
    assert outcome.no_majority is True
    assert outcome.winner_sql == "SELECT name FROM students"  # 并列取先出现组


def test_all_fail_returns_condensed_excerpt(fixture_db):
    cands = ["SELECT nope1 FROM students", "SELECT nope2 FROM students",
             "SELECT nope3 FROM students"]
    outcome = run_precise_batch(fixture_db, cands, max_rows=50, timeout_s=5.0, tracer=None)
    assert outcome.winner_sql is None
    assert "nope1" in outcome.fail_excerpt and "nope3" in outcome.fail_excerpt


def _settings(**kw):
    return Settings(api_key="", base_url="", model="", **kw)


def test_precise_graph_round_counts_as_single_ledger_entry(fixture_db):
    """账本语义：一轮三候选（两票等价＋一票执行失败）→ attempts 仅记一条胜者。"""
    s = _settings(precise_candidates=3)
    llm = ScriptedLLM([
        "谁成绩最好",
        "SELECT name FROM students WHERE id = 1",
        "SELECT name FROM students WHERE id = 1 AND grade >= 0",  # 等价结果
        "SELECT nope FROM students",                              # 否决候选
        "Alice 最好",
    ])
    final = build_graph(llm, settings=s).invoke(
        {"db_path": fixture_db, "question": "谁成绩最好"})
    assert llm.calls == 5
    assert len(final["attempts"]) == 1
    assert final["attempts"][0].sql == "SELECT name FROM students WHERE id = 1"
    assert final["answer"].failed is False
    assert final["answer"].conclusion.startswith("【结论】Alice 最好")  # 票 07 三节形态


def test_precise_no_majority_forced_suspicious_with_annotation(fixture_db):
    """无多数派：规则校验即使通过也判可疑；预算耗尽 → 带标注作答（不计正确路线）。"""
    s = _settings(precise_candidates=3, retry_budget=1)
    llm = ScriptedLLM([
        "谁成绩最好",
        "SELECT name FROM students",   # 两行
        "SELECT id FROM students",     # 两行但值不同
        "SELECT grade FROM students",  # 又不同
        "结论见数据",
    ])
    final = build_graph(llm, settings=s).invoke(
        {"db_path": fixture_db, "question": "谁成绩最好"})
    assert len(final["attempts"]) == 1
    assert final["attempts"][0].error == NO_MAJORITY_ERROR
    ans = final["answer"]
    assert ans.failed is False
    assert "无多数派" in ans.conclusion  # 带校验标注作答


def test_precise_all_fail_honest_failure_and_round_count(fixture_db):
    """全败：一条浓缩摘录入账；预算 1 耗尽 → 诚实失败（永不编造）。"""
    s = _settings(precise_candidates=3, retry_budget=1)
    llm = ScriptedLLM([
        "谁成绩最好",
        "SELECT nope1 FROM students",
        "SELECT nope2 FROM students",
        "SELECT nope3 FROM students",
    ])
    final = build_graph(llm, settings=s).invoke(
        {"db_path": fixture_db, "question": "谁成绩最好"})
    assert len(final["attempts"]) == 1
    assert "候选全部执行失败" in final["attempts"][0].error
    assert final["answer"].failed is True


def test_precise_off_default_path_unchanged(fixture_db):
    """precise_candidates=1（默认关闭）：一切如旧，state 无 precise_candidates 残留。"""
    s = _settings()
    llm = ScriptedLLM(["谁成绩最好", "SELECT name FROM students WHERE id = 1", "Alice 最好"])
    final = build_graph(llm, settings=s).invoke(
        {"db_path": fixture_db, "question": "谁成绩最好"})
    assert llm.calls == 3
    assert len(final["attempts"]) == 1
    assert final["attempts"][0].sql == "SELECT name FROM students WHERE id = 1"