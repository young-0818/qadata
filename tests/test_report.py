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
