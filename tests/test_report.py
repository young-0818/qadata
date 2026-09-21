import json

from qadata.eval.report import build_report, load_records


def _write_records(tmp_path, name, records):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return str(p)


def _rec(qid, correct, **kw):
    return {"question_id": qid, "db_id": "s", "difficulty": "simple", "question": f"q{qid}",
            "correct": correct, **kw}


def test_build_report_flips_delta_and_failure_section(tmp_path):
    base = _write_records(tmp_path, "base.jsonl", [_rec(0, True), _rec(1, False), _rec(2, True)])
    cur = _write_records(tmp_path, "cur.jsonl", [
        _rec(0, False, error_class="judge_mismatch", gold_failed=False),
        _rec(1, True),
        _rec(2, False, error_class="pred_exec_failed", gold_failed=False),
    ])
    md = build_report(base, cur)
    assert "救回" in md and "1" in md           # 错→对：题 1
    assert "改坏" in md and "0" in md           # 对→错：题 0
    assert "judge_mismatch" in md and "pred_exec_failed" in md  # 失败样本段


def test_build_report_tolerates_missing_new_fields(tmp_path):
    """旧 run 文件缺 gold_failed/error_class 字段（M2 产物）：报告不得崩溃。"""
    base = _write_records(tmp_path, "b.jsonl", [_rec(0, True)])
    cur = _write_records(tmp_path, "c.jsonl", [_rec(0, False)])
    md = build_report(base, cur)
    assert "改坏" in md


def test_load_records_parses(tmp_path):
    p = _write_records(tmp_path, "r.jsonl", [_rec(0, True)])
    recs = load_records(p)
    assert len(recs) == 1 and recs[0]["question_id"] == 0


def _write_types(tmp_path, pairs):
    p = tmp_path / "types.jsonl"
    p.write_text("\n".join(
        json.dumps({"question_id": i, "qtype": t}, ensure_ascii=False) for i, t in pairs),
        encoding="utf-8")
    return str(p)


def test_build_report_types_slice(tmp_path):
    """E2 题型切片：按 qtype 分组出准确率；缺标签题计「未知」不炸。"""
    base = _write_records(tmp_path, "b.jsonl",
                          [_rec(i, True) for i in (0, 1, 2, 4)])
    cur = _write_records(tmp_path, "c.jsonl",
                         [_rec(0, True), _rec(1, False), _rec(2, True), _rec(4, False)])
    types = _write_types(tmp_path, [(0, "极值"), (1, "极值"), (2, "分布")])
    md = build_report(base, cur, types_path=types)
    assert "题型切片" in md
    assert "| 极值 | 2 | 50.0% |" in md
    assert "| 分布 | 1 | 100.0% |" in md
    assert "| 未知 | 1 | 0.0% |" in md  # 题 4 无标签


def test_build_report_without_types_no_slice(tmp_path):
    """不传 types_path 时报告保持 M3 形态（无切片段）。"""
    base = _write_records(tmp_path, "b.jsonl", [_rec(0, True)])
    cur = _write_records(tmp_path, "c.jsonl", [_rec(0, False)])
    md = build_report(base, cur)
    assert "题型切片" not in md
