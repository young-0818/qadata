import json

from qadata.eval.report import build_path_report, build_report, load_records, path_stats


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


# ── M5 票 06：分路径报告（命中率／命中路径／兜底路径 × 题型切片＋判卷）───


def _prec(qid, correct, path="fallback", metric=None, fell=False):
    return {**_rec(qid, correct), "path": path, "metric_name": metric,
            "template_fell_back": fell}


def _baseline_round(correct_ids, n=12):
    """纯 SQL 对照轮（off）：全部 path=fallback，correct 按 correct_ids。"""
    return [_prec(i, i in correct_ids) for i in range(n)]


def test_path_stats_three_numbers():
    """命中路径与兜底路径各自准确率＝本轮该路径题的对数／题数；
    基线口径＝对照轮**同一批题**上的对数（同端点同时段配对，票 06 主判据）。"""
    # 命中 5 题：本轮对 4，off 轮同题对 3；兜底 7 题：本轮对 4，off 轮同题对 4
    cur = ([_prec(i, i in {0, 1, 2, 3}, "metric", "m") for i in range(5)]
           + [_prec(i, i in {5, 6, 8, 9}, "fallback") for i in range(5, 12)])
    # cur 命中题 0..4（对 0,1,2,3）；兜底题 5..11（对 5,6,8,9）
    base = ([_prec(i, i in {0, 1, 2}, "fallback") for i in range(5)]
            + [_prec(i, i in {5, 6, 8, 9}, "fallback") for i in range(5, 12)])
    st = path_stats(cur, base)
    assert st["total"] == 12
    assert st["hit"]["n"] == 5 and st["hit"]["correct"] == 4
    assert st["hit"]["baseline_correct"] == 3   # off 轮在同一批命中题上对 3
    assert st["fallback"]["n"] == 7 and st["fallback"]["correct"] == 4
    assert st["fallback"]["baseline_correct"] == 4
    assert st["hit_rate"] == round(5 / 12, 4)


def test_path_stats_missing_path_field_is_fallback():
    """旧 run 无 path 字段（票 05 前产物）：一律计兜底，报告不崩。"""
    cur = [{"question_id": 0, "correct": True}, {"question_id": 1, "correct": False}]
    base = _baseline_round({0, 1}, n=2)
    st = path_stats(cur, base)
    assert st["hit"]["n"] == 0
    assert st["fallback"]["n"] == 2 and st["fallback"]["correct"] == 1


def test_path_stats_fell_back_counted():
    """模板降级题走兜底路径，但降级旗标单列（命中率分母不含降级为命中）。"""
    cur = [_prec(0, True, "fallback", fell=True), _prec(1, True, "metric", "m")]
    base = _baseline_round({0, 1}, n=2)
    st = path_stats(cur, base)
    assert st["fell_back"] == 1
    assert st["fallback"]["n"] == 1 and st["hit"]["n"] == 1


def test_build_path_report_pass_verdict(tmp_path):
    """①命中≥基线、③兜底≥基线−2、命中≥10 → 三条款逐条判过。"""
    cur = ([_prec(i, True, "metric", "m") for i in range(10)]   # 命中 10 全对
           + [_prec(i, True, "fallback") for i in range(10, 20)])  # 兜底 10 全对
    base = ([_prec(i, i < 8, "fallback") for i in range(10)]    # off 轮命中题对 8
            + [_prec(i, True, "fallback") for i in range(10, 20)])
    c = _write_records(tmp_path, "c.jsonl", cur)
    b = _write_records(tmp_path, "b.jsonl", base)
    md = build_path_report(b, c)
    assert "命中率" in md and "10/20" in md
    assert "| 命中路径 |" in md and "| 兜底路径 |" in md
    assert "判过" in md
    assert "①" in md and "③" in md


def test_build_path_report_hit_insufficient_sample(tmp_path):
    """命中 <10 题 → 条款②判「样本不足，无结论」，且①不给判过。"""
    cur = [_prec(0, True, "metric", "m")] + [_prec(i, True, "fallback") for i in range(1, 10)]
    base = _baseline_round(set(range(10)), n=10)
    c = _write_records(tmp_path, "c.jsonl", cur)
    b = _write_records(tmp_path, "b.jsonl", base)
    md = build_path_report(b, c)
    assert "样本不足" in md and "无结论" in md


def test_build_path_report_fallback_regression(tmp_path):
    """③兜底路径掉分超 2 题 → 判负（metric_match 领错门被回归闸拦住）。"""
    # 命中 10 题对 9；兜底 10 题本轮只对 5，off 轮同题对 9 → 掉 4 题 > 2
    cur = ([_prec(i, i < 9, "metric", "m") for i in range(10)]
           + [_prec(i, i < 15, "fallback") for i in range(10, 20)])
    base = ([_prec(i, True, "fallback") for i in range(10)]
            + [_prec(i, i < 19, "fallback") for i in range(10, 20)])
    c = _write_records(tmp_path, "c.jsonl", cur)
    b = _write_records(tmp_path, "b.jsonl", base)
    md = build_path_report(b, c)
    assert "判负" in md
    # 兜底路径行体现本轮 5 对、基线 9 对
    assert "5/10" in md and "9" in md


def test_build_path_report_qtype_path_slice(tmp_path):
    """题型×路径切片：每题型给命中数/命中准确率/兜底准确率，缺路径记「—」。"""
    cur = [_prec(0, True, "metric", "m"), _prec(1, False, "metric", "m"),
           _prec(2, True, "fallback"), _prec(3, False, "fallback")]
    base = _baseline_round({0, 2}, n=4)
    types = _write_types(tmp_path, [(0, "极值"), (1, "极值"), (2, "分布"), (3, "分布")])
    c = _write_records(tmp_path, "c.jsonl", cur)
    b = _write_records(tmp_path, "b.jsonl", base)
    md = build_path_report(b, c, types_path=types)
    assert "题型×路径" in md
    assert "| 极值 |" in md and "| 分布 |" in md
    # 极值：命中 2 题对 1（50.0%），无兜底题 →「—」
    assert "| 极值 | 2 | 2 | 50.0% | — |" in md
    assert "| 分布 | 2 | 0 | — | 50.0% |" in md
