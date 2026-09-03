"""BIRD 最小评测器：加载题目 → 跑 Agent → 执行准确率 → JSONL 记录 + 汇总。

M3 将升级：断点续跑、失败样本库、报告 diff、变体矩阵（见设计文档 §7）。
"""
import json
import random
from pathlib import Path

from rich.console import Console
from rich.table import Table

from qadata.eval.match import results_match
from qadata.graph.build import run_question
from qadata.llm.tracing import TraceLogger
from qadata.tools.db import open_readonly

console = Console()


def load_questions(path: str, sample: int | None = None, seed: int = 42,
                   question_ids: list[int] | None = None) -> list[dict]:
    """加载题目。question_ids 给定则按该顺序精确取题（固定题集机制）；否则可选随机抽样。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if question_ids is not None:
        if sample is not None:
            raise ValueError("question_ids 与 sample 不能同时指定")
        index = {q["question_id"]: q for q in data}
        missing = [i for i in question_ids if i not in index]
        if missing:
            raise ValueError(f"question_ids 含未知题号：{missing}")
        return [index[i] for i in question_ids]
    if sample is not None and sample < len(data):
        data = random.Random(seed).sample(data, sample)
    return data


def run_eval(questions_path: str, db_dir: str, sample: int | None = None,
             question_ids: list[int] | None = None,
             llm=None, max_rows: int = 10000) -> dict:
    questions = load_questions(questions_path, sample=sample, question_ids=question_ids)
    out_path = Path("runs/eval-last.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tracer = TraceLogger("runs/traces.jsonl")

    records, n_correct = [], 0
    by_difficulty: dict[str, list[bool]] = {}
    with out_path.open("w", encoding="utf-8") as f:
        for q in questions:
            tracer.set_context(question_id=str(q["question_id"]))
            rec = _run_one(q, db_dir, llm, max_rows, tracer)
            records.append(rec)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            by_difficulty.setdefault(q.get("difficulty", "unknown"), []).append(rec["correct"])
            n_correct += int(rec["correct"])

    summary = {
        "total": len(records),
        "correct": n_correct,
        "accuracy": round(n_correct / len(records), 4) if records else 0.0,
        "by_difficulty": {k: round(sum(v) / len(v), 4) for k, v in by_difficulty.items()},
    }
    _print_summary(summary)
    return summary


def _run_one(q: dict, db_dir: str, llm, max_rows: int, tracer) -> dict:
    """单题评测：异常隔离，单题失败不阻塞整批。"""
    db_path = Path(db_dir) / q["db_id"] / f"{q['db_id']}.sqlite"
    base = {"question_id": q["question_id"], "db_id": q["db_id"],
            "difficulty": q.get("difficulty"), "question": q["question"]}
    try:
        answer = run_question(str(db_path), q["question"], evidence=q.get("evidence", ""), llm=llm, tracer=tracer)
        if answer.failed or not answer.sql:
            return {**base, "pred_sql": answer.sql, "correct": False,
                    "error": answer.error_summary or "no sql"}
        pred_rows = _exec(str(db_path), answer.sql, max_rows)
        gold_rows = _exec(str(db_path), q["SQL"], max_rows)
        return {**base, "pred_sql": answer.sql,
                "correct": results_match(pred_rows, gold_rows), "error": None}
    except Exception as e:  # noqa: BLE001 单题隔离：评测器最外层，单题任何失败不阻塞整批
        return {**base, "pred_sql": None, "correct": False, "error": str(e)}


def _exec(db_path: str, sql: str, max_rows: int) -> list[tuple]:
    conn = open_readonly(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql).fetchmany(max_rows)]
    finally:
        conn.close()


def _print_summary(summary: dict) -> None:
    t = Table(title="BIRD 评测结果")
    t.add_column("难度")
    t.add_column("准确率")
    for k, v in summary["by_difficulty"].items():
        t.add_row(k, f"{v:.1%}")
    console.print(t)
    console.print(f"总准确率：[bold]{summary['accuracy']:.1%}[/bold] "
                  f"（{summary['correct']}/{summary['total']}）")
