"""BIRD 评测器：加载题目 → 跑 Agent → 执行准确率 → JSONL 记录 + 汇总。

M4 升级：分片并发（--concurrency，每题独立分片文件＋单线程收口合并）；
串行语义（concurrency=1）与 M3 完全一致（逐题 flush、单文件 --resume）。
"""
import json
import random
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.table import Table

from qadata.eval.match import results_match
from qadata.graph.build import run_question
from qadata.llm.tracing import TraceLogger
from qadata.tools.db import open_readonly

console = Console()
TRACE_PATH = "runs/traces.jsonl"


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


def _shard_paths(qid: int, out: Path) -> tuple[Path, Path]:
    """分片文件与 out 同目录的 eval-shards/ 下——按题号隔离，崩溃恢复安全。"""
    d = out.parent / "eval-shards"
    return d / f"{qid}.jsonl", d / f"{qid}.traces.jsonl"


def _merge_out(questions: list[dict], out: Path) -> None:
    """单线程收口：out 按题目顺序重写（分片优先，其次 out 既有记录）。

    幂等——任意时刻调用输出一致；中断恢复后 resume 只需补跑缺失分片。"""
    shard_dir = out.parent / "eval-shards"
    recs: dict[int, dict] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs.setdefault(r["question_id"], r)
    for q in questions:
        f = shard_dir / f"{q['question_id']}.jsonl"
        if f.exists():
            recs[q["question_id"]] = json.loads(f.read_text(encoding="utf-8"))
    with out.open("w", encoding="utf-8") as fh:
        for q in questions:
            r = recs.get(q["question_id"])
            if r:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _summarize(records: list[dict]) -> dict:
    n_correct = sum(int(r["correct"]) for r in records)
    by_difficulty: dict[str, list[bool]] = {}
    for r in records:
        by_difficulty.setdefault(r.get("difficulty", "unknown"), []).append(r["correct"])
    return {
        "total": len(records),
        "correct": n_correct,
        "accuracy": round(n_correct / len(records), 4) if records else 0.0,
        "by_difficulty": {k: round(sum(v) / len(v), 4) for k, v in by_difficulty.items()},
        "gold_failed": sum(1 for r in records if r.get("gold_failed")),
    }


def run_eval(questions_path: str, db_dir: str, sample: int | None = None,
             question_ids: list[int] | None = None,
             llm=None, max_rows: int = 10000,
             out_path: str = "runs/eval-last.jsonl", resume: bool = False,
             concurrency: int = 1, settings=None, limiter=None,
             skip_respond: bool = False) -> dict:
    """跑评测。concurrency>1 走分片并发；=1 保持 M3 串行语义（逐题 flush）。

    skip_respond：评测模式跳过结论 LLM 生成（判分只读 answer.sql 的执行结果，
    省 1 次调用/题）；settings/limiter 由调用方构造后透传（共享实例贯穿所有线程）；
    concurrency=1 且未提供时行为与历史版本完全一致。"""
    questions = load_questions(questions_path, sample=sample, question_ids=question_ids)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if concurrency > 1:
        return _run_concurrent_path(questions, db_dir, llm, max_rows, out, resume,
                                    concurrency, settings, limiter, skip_respond)

    tracer = TraceLogger(TRACE_PATH)
    done_ids: set[int] = set()
    if resume and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done_ids.add(json.loads(line)["question_id"])
    todo = [q for q in questions if q["question_id"] not in done_ids]
    mode = "a" if resume and done_ids else "w"

    with out.open(mode, encoding="utf-8") as f:
        for q in todo:
            tracer.set_context(question_id=str(q["question_id"]))
            rec = _run_one(q, db_dir, llm, max_rows, tracer, settings, limiter,
                           skip_respond)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()  # 逐题落盘：运行中 tail 文件可见进度（M2 痛点）

    records = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary = _summarize(records)
    _print_summary(summary)
    return summary


def _run_concurrent_path(questions, db_dir, llm, max_rows, out, resume,
                         concurrency, settings, limiter, skip_respond) -> dict:
    """并发分片流程：每题独立分片（评测记录＋traces），主线程单写者收口合并。"""
    run_id = uuid.uuid4().hex[:12]
    todo = [q for q in questions
            if not (resume and _shard_paths(q["question_id"], out)[0].exists())]
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(todo)))) as ex:
            futs = {}
            for q in todo:
                shard_rec, shard_trace = _shard_paths(q["question_id"], out)
                tracer = TraceLogger(shard_trace, run_id=run_id)
                tracer.set_context(question_id=str(q["question_id"]))
                fut = ex.submit(_run_one, q, db_dir, llm, max_rows, tracer, settings,
                                limiter, skip_respond)
                futs[fut] = (q, shard_rec, shard_trace)
            for fut in as_completed(futs):
                q, shard_rec, shard_trace = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:  # noqa: BLE001 并发兜底：分片线程裸异常不炸整批
                    rec = {"question_id": q["question_id"], "db_id": q["db_id"],
                           "difficulty": q.get("difficulty"), "question": q["question"],
                           "gold_sql": q["SQL"], "gold_failed": False, "pred_sql": None,
                           "correct": False, "error": f"并发分片异常：{e}",
                           "error_class": "answer_failed"}
                shard_rec.parent.mkdir(parents=True, exist_ok=True)
                shard_rec.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
                _merge_out(questions, out)  # 主线程收口：进度实时可见，合并幂等
                if shard_trace.exists():
                    with open(TRACE_PATH, "a", encoding="utf-8") as tf:
                        tf.write(shard_trace.read_text(encoding="utf-8"))
    else:
        _merge_out(questions, out)
    records = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary = _summarize(records)
    _print_summary(summary)
    return summary


def _run_one(q: dict, db_dir: str, llm, max_rows: int, tracer, settings=None,
             limiter=None, skip_respond: bool = False) -> dict:
    """单题评测：异常隔离，单题失败不阻塞整批；错误分类进 error_class。"""
    db_path = Path(db_dir) / q["db_id"] / f"{q['db_id']}.sqlite"
    base = {"question_id": q["question_id"], "db_id": q["db_id"],
            "difficulty": q.get("difficulty"), "question": q["question"],
            "gold_sql": q["SQL"], "gold_failed": False}
    try:
        answer = run_question(str(db_path), q["question"], evidence=q.get("evidence", ""),
                              llm=llm, tracer=tracer, settings=settings, limiter=limiter,
                              skip_respond=skip_respond)
    except Exception as e:  # noqa: BLE001 单题隔离：评测器最外层，单题任何失败不阻塞整批
        return {**base, "pred_sql": None, "correct": False, "error": str(e),
                "error_class": "answer_failed"}
    if answer.failed or not answer.sql:
        return {**base, "pred_sql": answer.sql, "correct": False,
                "error": answer.error_summary or "no sql", "error_class": "answer_failed"}
    try:
        pred_rows = _exec(str(db_path), answer.sql, max_rows)
    except Exception as e:  # noqa: BLE001 单题隔离
        return {**base, "pred_sql": answer.sql, "correct": False, "error": str(e),
                "error_class": "pred_exec_failed"}
    try:
        gold_rows = _exec(str(db_path), q["SQL"], max_rows)
    except Exception as e:  # noqa: BLE001 gold 失败单独成类：与 pred 失败分标
        return {**base, "pred_sql": answer.sql, "correct": False,
                "error": f"gold 执行失败：{e}", "gold_failed": True,
                "error_class": "gold_failed"}
    correct = results_match(pred_rows, gold_rows)
    return {**base, "pred_sql": answer.sql, "correct": correct, "error": None,
            "error_class": None if correct else "judge_mismatch"}


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