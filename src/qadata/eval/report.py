"""评测报告：两轮对比 diff、N 路变体汇总（M3 评测器完备）与轨道①分路径报告（M5 票 06）。
记录字段一律 .get 容错：旧 run 文件可能缺 gold_failed/error_class/path。"""
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


def load_records(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"评测文件不存在：{path}")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _by_id(records: list[dict]) -> dict[int, dict]:
    return {r["question_id"]: r for r in records}


def _accuracy(records: list[dict]) -> float:
    return sum(int(r["correct"]) for r in records) / len(records) if records else 0.0


# 题型切片展示顺序（与 qtypes 六类一致）；未知兜底排最后
_LABEL_ORDER = ("趋势", "对比", "排名", "极值", "分布", "明细", "未知")


def _load_types(types_path: str) -> dict[int, str]:
    p = Path(types_path)
    if not p.exists():
        raise FileNotFoundError(f"标签文件不存在：{types_path}")
    qtypes = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            qtypes[r["question_id"]] = r.get("qtype") or "未知"
    return qtypes


def build_report(baseline_path: str, current_path: str,
                 types_path: str | None = None) -> str:
    base, cur = load_records(baseline_path), load_records(current_path)
    b_ids, c_ids = _by_id(base), _by_id(cur)
    rescued = sorted(i for i in b_ids if b_ids[i]["correct"] is False
                     and c_ids.get(i, {}).get("correct") is True)
    broken = sorted(i for i in b_ids if b_ids[i]["correct"] is True
                    and c_ids.get(i, {}).get("correct") is False)
    acc_b, acc_c = _accuracy(base), _accuracy(cur)
    lines = [
        "# 评测对比报告",
        "",
        f"- 基线 `{baseline_path}`：{len(base)} 题，准确率 {acc_b:.1%}",
        f"- 当前 `{current_path}`：{len(cur)} 题，准确率 {acc_c:.1%}",
        f"- Δ：{acc_c - acc_b:+.1%}",
        "",
        "## 翻转",
        f"- 救回（错→对）：{rescued or '无'}",
        f"- 改坏（对→错）：{broken or '无'}",
        "",
        "## 失败样本（当前错误题）",
    ]
    failures = [r for r in cur if not r["correct"]]
    if failures:
        lines.append("| 题号 | 错误分类 | 错误摘要 |")
        lines.append("|---|---|---|")
        for r in failures:
            lines.append(f"| {r['question_id']} | {r.get('error_class') or '未知'} | "
                         f"{(r.get('error') or '')[:60]} |")
    else:
        lines.append("无")
    if types_path:
        # E2 题型切片（M4）：按 qtype 分组出准确率，M5 分路径报告复用同一机制
        qtypes = _load_types(types_path)
        by_type: dict[str, list[bool]] = {}
        for r in cur:
            by_type.setdefault(qtypes.get(r["question_id"], "未知"), []).append(r["correct"])
        lines += ["", f"## 题型切片（来源：{types_path}）",
                  "| 题型 | 题数 | 准确率 |", "|---|---|---|"]
        for t in _LABEL_ORDER:
            if t in by_type:
                vs = by_type[t]
                lines.append(f"| {t} | {len(vs)} | {sum(vs) / len(vs):.1%} |")
    gold_failed = [r for r in cur if r.get("gold_failed")]
    lines += ["", f"## gold_failed：{len(gold_failed)} 题 {[r['question_id'] for r in gold_failed]}"]
    return "\n".join(lines)


def print_report_summary(baseline_path: str, current_path: str) -> None:
    base, cur = load_records(baseline_path), load_records(current_path)
    acc_b, acc_c = _accuracy(base), _accuracy(cur)
    t = Table(title="评测对比")
    t.add_column("口径")
    t.add_column("题数")
    t.add_column("准确率")
    t.add_row("基线", str(len(base)), f"{acc_b:.1%}")
    t.add_row("当前", str(len(cur)), f"{acc_c:.1%}")
    t.add_row("Δ", "", f"{acc_c - acc_b:+.1%}")
    console.print(t)


# ── M5 票 06：轨道①分路径报告（命中率／命中路径／兜底路径 × 题型切片＋判卷）──
# 判卷主判据（2026-09-09 裁决）：同端点同时段配对——基线口径一律取对照轮
# 「同一批题」上的对数；deepseek 27 分母只作水平参照，不进本函数。


def path_stats(current: list[dict], baseline: list[dict]) -> dict:
    """按 path 字段拆两轮记录出分路径统计（纯函数，文件读取归 build_*）。

    缺 path 字段的记录（票 05 前产物）一律计兜底——容错不崩（旧 run 惯例）。
    降级题（template_fell_back）的最终路径是 fallback，自然计入兜底。
    """
    b = _by_id(baseline)

    def _grp(rows: list[dict]) -> dict:
        n = len(rows)
        cc = sum(int(r["correct"]) for r in rows)
        # 对照轮同题对数：题号在对照轮缺失时不计分母（配对题集一致，理论不可达）
        matched = [r for r in rows if r["question_id"] in b]
        bc = sum(int(b[r["question_id"]]["correct"]) for r in matched)
        return {"n": n, "correct": cc, "acc": cc / n if n else 0.0,
                "baseline_correct": bc, "baseline_n": len(matched)}

    hits = [r for r in current if r.get("path") == "metric"]
    fbs = [r for r in current if r.get("path") != "metric"]
    return {"total": len(current),
            "hit_rate": round(len(hits) / len(current), 4) if current else 0.0,
            "hit": _grp(hits), "fallback": _grp(fbs),
            "fell_back": sum(1 for r in current if r.get("template_fell_back"))}


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _verdict_rows(st: dict) -> list[tuple[str, str, str]]:
    """验收条款①②③逐条判定（spec Q9 五条款中轨道①的三条；④已在票 02 判负成文）。

    ②先行：命中 <10 题时分路径噪声压倒信号，①不给判过也不给判负——无结论。
    ①命中 ≥ 基线：本轮命中题对数 ≥ 对照轮同题对数；
    ③兜底 ≥ 基线−2：容忍 2 题端点抖动。
    """
    hit, fb = st["hit"], st["fallback"]
    rows = []
    if hit["n"] < 10:
        rows.append(("① 命中路径 ≥ 纯 SQL 基线（同题）", "无结论",
                     f"条款②先行：命中仅 {hit['n']} 题 <10，样本不足"))
        rows.append(("② 命中样本量", "样本不足，无结论",
                     f"命中 {hit['n']}/{st['total']} 题 <10"))
    else:
        ok1 = hit["correct"] >= hit["baseline_correct"]
        rows.append(("① 命中路径 ≥ 纯 SQL 基线（同题）", "判过" if ok1 else "判负",
                     f"命中题本轮 {hit['correct']} 对 vs 对照轮同题 {hit['baseline_correct']} 对"))
        rows.append(("② 命中样本量", "足够", f"命中 {hit['n']} 题 ≥10"))
    ok3 = fb["correct"] >= fb["baseline_correct"] - 2
    rows.append(("③ 兜底 ≥ 基线−2 题", "判过" if ok3 else "判负",
                 f"兜底本轮 {fb['correct']} 对 vs 对照轮同题 {fb['baseline_correct']} 对（容忍带 −2）"))
    return rows


def build_path_report(baseline_path: str, current_path: str,
                      types_path: str | None = None) -> str:
    """分路径 markdown 报告：三个数＋判卷表＋题型×路径切片＋命中明细／翻转清单。

    baseline＝对照轮（metric_layer 关），current＝指标轮（关开各一轮同时段配对）。
    """
    base, cur = load_records(baseline_path), load_records(current_path)
    st = path_stats(cur, base)
    hit, fb = st["hit"], st["fallback"]

    def _row(name: str, g: dict) -> str:
        if g["n"] == 0:
            return f"| {name} | 0 | — | — | — | — |"
        return (f"| {name} | {g['n']} | {g['correct']}/{g['n']} | {_pct(g['correct'] / g['n'])} | "
                f"{g['baseline_correct']}/{g['baseline_n']} | "
                f"{_pct(g['baseline_correct'] / g['baseline_n']) if g['baseline_n'] else '—'} |")

    lines = [
        "# 轨道①分路径报告（M5 票 06）",
        "",
        f"- 对照轮（纯 SQL）`{baseline_path}`：{len(base)} 题，{_accuracy(base):.1%}",
        f"- 指标轮 `{current_path}`：{st['total']} 题，{_accuracy(cur):.1%}（Δ {_accuracy(cur) - _accuracy(base):+.1%}）",
        "",
        "## 三个数",
        "",
        (f"- 命中率：{hit['n']}/{st['total']} = {_pct(st['hit_rate'])}"
         f"（模板降级 {st['fell_back']} 题计入兜底）"),
        "",
        "| 路径 | 题数 | 本轮对 | 本轮准确率 | 对照轮同题对 | 对照准确率 |",
        "|---|---|---|---|---|---|",
        _row("命中路径", hit),
        _row("兜底路径", fb),
        "",
        "## 判卷（Q9 轨道①三条款，主判据＝同端点同时段配对）",
        "",
        "| 条款 | 判定 | 依据 |",
        "|---|---|---|",
    ]
    lines += [f"| {c} | {v} | {why} |" for c, v, why in _verdict_rows(st)]

    # 命中明细＋翻转清单：判卷依据的题级证据（⑨闸在体观测＝极值题是否误入命中路径）
    by_metric: dict[str, list[dict]] = {}
    for r in cur:
        if r.get("path") == "metric":
            by_metric.setdefault(str(r.get("metric_name")), []).append(r)
    lines += ["", "## 命中明细", "", "| 指标 | 题数 | 对 | 题号 |", "|---|---|---|---|"]
    for name, rows in sorted(by_metric.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        ids = ",".join(str(r["question_id"]) for r in rows)
        lines.append(f"| {name} | {len(rows)} | {sum(int(r['correct']) for r in rows)} | {ids} |")
    fell = [str(r["question_id"]) for r in cur if r.get("template_fell_back")]
    lines.append(f"\n降级题：{'、'.join(fell) or '无'}")
    b = _by_id(base)

    def _flips(rows: list[dict]) -> tuple[list, list]:
        return ([r["question_id"] for r in rows
                 if b.get(r["question_id"], {}).get("correct") is False and r["correct"]],
                [r["question_id"] for r in rows
                 if b.get(r["question_id"], {}).get("correct") is True and not r["correct"]])

    for label, rows in (("命中路径", [r for r in cur if r.get("path") == "metric"]),
                        ("兜底路径", [r for r in cur if r.get("path") != "metric"])):
        res, bro = _flips(rows)
        lines.append(f"- {label}翻转：救回 {res or '无'}｜改坏 {bro or '无'}")

    if types_path:
        # 题型×路径切片：复用 E2 标签器与 _LABEL_ORDER 展示序（spec 双轨评测第 6 条）
        qtypes = _load_types(types_path)
        lines += ["", f"## 题型×路径切片（来源：{types_path}）", "",
                  "| 题型 | 题数 | 命中 | 命中准确率 | 兜底准确率 |", "|---|---|---|---|---|"]
        seen = set()
        for r in cur:
            seen.add(qtypes.get(r["question_id"], "未知"))
        for t in _LABEL_ORDER:
            if t not in seen:
                continue
            rows = [r for r in cur if qtypes.get(r["question_id"], "未知") == t]
            hs = [r for r in rows if r.get("path") == "metric"]
            fs = [r for r in rows if r.get("path") != "metric"]
            lines.append(f"| {t} | {len(rows)} | {len(hs)} | "
                         f"{_pct(sum(int(r['correct']) for r in hs) / len(hs)) if hs else '—'} | "
                         f"{_pct(sum(int(r['correct']) for r in fs) / len(fs)) if fs else '—'} |")
    return "\n".join(lines)


def build_multi_report(paths: list[str], names: list[str]) -> str:
    lines = ["# 变体矩阵对比", "", "| 变体 | 题数 | 准确率 |", "|---|---|---|"]
    for name, path in zip(names, paths):
        recs = load_records(path)
        lines.append(f"| {name} | {len(recs)} | {_accuracy(recs):.1%} |")
    return "\n".join(lines)
