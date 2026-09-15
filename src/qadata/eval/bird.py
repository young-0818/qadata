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
from qadata.llm.tracing import TRACE_PATH, TraceLogger, now_beijing
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
             skip_respond: bool = False, budget_path: str | None = None) -> dict:
    """跑评测。concurrency>1 走分片并发；=1 保持 M3 串行语义（逐题 flush）。

    skip_respond：评测模式跳过结论 LLM 生成（判分只读 answer.sql 的执行结果，
    省 1 次调用/题）；settings/limiter 由调用方构造后透传（共享实例贯穿所有线程）；
    concurrency=1 且未提供时行为与历史版本完全一致。
    budget_path（票 10）：给定则轮末向该账本 markdown 自动追加一行
    （题数×调用/tokens 实测；估算成本与累计两列留「待填」由人折算）。"""
    questions = load_questions(questions_path, sample=sample, question_ids=question_ids)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if budget_path and not Path(budget_path).is_file():
        # 前置校验：账本不存在直接拒绝开跑——绝不等到烧完整轮钱之后才报路径写错
        raise FileNotFoundError(f"预算账本不存在：{budget_path}（先建表头再跑，避免追加孤儿行）")
    if concurrency > 1:
        return _run_concurrent_path(questions, db_dir, llm, max_rows, out, resume,
                                    concurrency, settings, limiter, skip_respond,
                                    budget_path)

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
    _emit_run_stats(tracer.run_id, settings, out, {q["question_id"] for q in todo},
                    records, budget_path)
    return summary


def _run_concurrent_path(questions, db_dir, llm, max_rows, out, resume,
                         concurrency, settings, limiter, skip_respond,
                         budget_path=None) -> dict:
    """并发分片流程：每题独立分片（评测记录＋traces），主线程单写者收口合并。"""
    run_id = uuid.uuid4().hex[:12]
    todo = [q for q in questions
            if not (resume and _shard_paths(q["question_id"], out)[0].exists())]
    if not resume:
        # 新一轮（非续跑）先清除待跑题的遗留分片：中间态收口合并不得混入上次运行的记录
        for q in todo:
            for p in _shard_paths(q["question_id"], out):
                p.unlink(missing_ok=True)
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(todo)))) as ex:
            futs = {}
            for q in todo:
                shard_rec, shard_trace = _shard_paths(q["question_id"], out)
                tracer = TraceLogger(shard_trace, run_id=run_id)
                tracer.set_context(question_id=str(q["question_id"]))
                fut = ex.submit(_run_one, q, db_dir, llm, max_rows, tracer, settings,
                                limiter, skip_respond)
                futs[fut] = (q, shard_rec, shard_trace, tracer)
            for fut in as_completed(futs):
                q, shard_rec, shard_trace, tracer = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:  # noqa: BLE001 并发兜底：分片线程裸异常不炸整批
                    # 兜底记录也挂运行统计（票 10：成本已烧不能漏计），异常前已落账的调用从分片 tracer 取回
                    rec = {"question_id": q["question_id"], "db_id": q["db_id"],
                           "difficulty": q.get("difficulty"), "question": q["question"],
                           "gold_sql": q["SQL"], "gold_failed": False, "pred_sql": None,
                           "correct": False, "error": f"并发分片异常：{e}",
                           "error_class": "answer_failed", **_FALLBACK_PATH,
                           **_run_stats(tracer, q)}
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
    _emit_run_stats(run_id, settings, out, {q["question_id"] for q in todo},
                    records, budget_path)
    return summary


_STAT_KEYS = ("llm_calls", "input_tokens", "output_tokens", "total_tokens", "latency_s")

# 票 05 路径字段缺省：尚未拿到 Answer 的记录（单题异常/并发分片异常）按纯兜底计
_FALLBACK_PATH = {"path": "fallback", "metric_name": None, "template_fell_back": False}


def _emit_run_stats(run_id, settings, out, processed_ids, records, budget_path=None) -> None:
    """票 10 轮末收口：逐题明细→tokens-<run_id>.json＋控制台汇总行＋账本自动记行。

    合计只计「本次调用处理的题」（--resume 续跑不重复计账）；
    账本行「估算成本/累计」两列留待填——成本折算保持人审（数字诚实）。"""
    proc = [r for r in records if r["question_id"] in processed_ids]
    if not proc:
        return
    totals = {k: round(sum(r.get(k, 0) for r in proc), 2) for k in _STAT_KEYS}
    model = getattr(settings, "model", "") if settings is not None else ""
    doc = {"run_id": run_id, "model": model, "ts": now_beijing(), "n_questions": len(proc),
           "totals": totals,
           "questions": [{k: r.get(k) for k in ("question_id", "correct", "error_class", *_STAT_KEYS)}
                         for r in proc]}
    tokens_file = out.parent / f"tokens-{run_id}.json"
    tokens_file.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    n_correct = sum(1 for r in proc if r["correct"])
    tokens_str = (f"in {totals['input_tokens']} / out {totals['output_tokens']}"
                  f" / total {totals['total_tokens']} tokens；延迟 {totals['latency_s']}s")
    console.print(f"run_id={run_id}｜{len(proc)} 题｜{totals['llm_calls']} 调用｜{tokens_str}｜"
                  f"对 {n_correct}/{len(proc)}｜明细 {tokens_file.name}")
    if budget_path:
        with open(budget_path, "a", encoding="utf-8") as f:
            f.write(f"| {now_beijing()[:10]} | run {run_id}（{model or '未知模型'}，自动记录） | "
                    f"{len(proc)} 题 {totals['llm_calls']} 调用 | "
                    f"{tokens_str} | 待填 | 待填 | "
                    f"{n_correct}/{len(proc)}；明细 tokens-{run_id}.json |\n")


def _run_stats(tracer, q: dict) -> dict:
    """票 10：逐题运行统计（run_id＋调用数/token/延迟求和）；失败题也带（成本已烧）。"""
    if tracer is None:
        return {}
    return {"run_id": tracer.run_id, **tracer.usage_for(str(q["question_id"]))}


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
        return {**base, **_run_stats(tracer, q), "pred_sql": None, "correct": False, "error": str(e),
                "error_class": "answer_failed", **_FALLBACK_PATH}
    # 票 05 路径字段：Answer 是带默认值的 dataclass，三字段恒在（指标层关时 path=fallback）
    path = {"path": answer.path, "metric_name": answer.metric_name,
            "template_fell_back": answer.template_fell_back}
    if answer.clarification:
        # M8 票 03：澄清轮弃答入账（每题 clarified≈判负——判卷读的就是误伤上界）；
        # pred 未生成不执行不判分。默认关态澄清恒 None＝本分支不可达（票面如实注）
        return {**base, **_run_stats(tracer, q), "pred_sql": None, "correct": False,
                "error": f"澄清问：{answer.clarification}",
                "error_class": "clarified", **path}
    if answer.failed or not answer.sql:
        return {**base, **_run_stats(tracer, q), "pred_sql": answer.sql, "correct": False,
                "error": answer.error_summary or "no sql", "error_class": "answer_failed", **path}
    try:
        pred_rows = _exec(str(db_path), answer.sql, max_rows)
    except Exception as e:  # noqa: BLE001 单题隔离
        return {**base, **_run_stats(tracer, q), "pred_sql": answer.sql, "correct": False,
                "error": str(e), "error_class": "pred_exec_failed", **path}
    try:
        gold_rows = _exec(str(db_path), q["SQL"], max_rows)
    except Exception as e:  # noqa: BLE001 gold 失败单独成类：与 pred 失败分标
        return {**base, **_run_stats(tracer, q), "pred_sql": answer.sql, "correct": False,
                "error": f"gold 执行失败：{e}", "gold_failed": True,
                "error_class": "gold_failed", **path}
    correct = results_match(pred_rows, gold_rows)
    return {**base, **_run_stats(tracer, q), "pred_sql": answer.sql, "correct": correct, "error": None,
            "error_class": None if correct else "judge_mismatch", **path}


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