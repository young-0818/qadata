"""评测报告：两轮对比 diff、N 路变体汇总（M3 评测器完备）与轨道①分路径报告（M5 票 06）。
记录字段一律 .get 容错：旧 run 文件可能缺 gold_failed/error_class，
M5 时代产物另有 path/metric_name/template_fell_back 三键（指标层退役 ADR-0007 后新产物不再写）。"""
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


def build_multi_report(paths: list[str], names: list[str]) -> str:
    """M3 变体矩阵多轮对比（非指标面，随 M5 整段截除后单独接回）。"""
    lines = ["# 变体矩阵对比", "", "| 变体 | 题数 | 准确率 |", "|---|---|---|"]
    for name, path in zip(names, paths):
        recs = load_records(path)
        lines.append(f"| {name} | {len(recs)} | {_accuracy(recs):.1%} |")
    return "\n".join(lines)
