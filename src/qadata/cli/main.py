"""CLI 薄壳：ask 单问 / eval 评测。核心逻辑全部在包内。"""
import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from qadata import run_question  # 顶层导入（模块全局），便于测试 monkeypatch
from qadata.llm.tracing import TraceLogger

console = Console()


def _print_answer(answer) -> None:
    console.rule("问数")
    style = "red" if answer.failed else "green"
    console.print(f"[{style}]结论：{answer.conclusion}[/{style}]")
    if answer.result is not None:
        t = Table(show_header=True, header_style="bold")
        for c in answer.result.columns:
            t.add_column(str(c))
        for row in answer.result.rows[:10]:
            t.add_row(*[str(v) for v in row])
        console.print(t)
        if answer.result.truncated:
            console.print("[yellow]结果已截断（仅显示前若干行）[/yellow]")
    if answer.sql:
        console.print(f"SQL：{answer.sql}", style="dim")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qadata", description="对话式数据分析 Agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ask = sub.add_parser("ask", help="单次提问：qadata ask <db_path> <question>")
    p_ask.add_argument("db_path")
    p_ask.add_argument("question")
    p_ask.add_argument("--evidence", default="", help="业务口径说明（BIRD evidence 等价物）")

    p_eval = sub.add_parser("eval", help="BIRD 评测（Task 10 接通）")
    p_eval.add_argument("--questions", required=True)
    p_eval.add_argument("--db-dir", required=True)
    p_eval.add_argument("--sample", type=int, default=None)
    p_eval.add_argument("--ids", default=None, help="题号 JSON 文件（固定题集：冒烟/对比）")
    p_eval.add_argument("--out", default="runs/eval-last.jsonl", help="输出 JSONL 路径")
    p_eval.add_argument("--resume", action="store_true", help="断点续跑：跳过已完成题号")
    p_eval.add_argument("--variants", default=None, help="变体矩阵 YAML（M3 工具链，实验 M4 跑）")
    p_eval.add_argument("--concurrency", type=int, default=5,
                        help="并发路数（默认 5，5-8；1=串行，兼容旧语义）")
    p_eval.add_argument("--qps", type=float, default=None,
                        help="全局限速（次/秒）；不传=不限速")
    p_eval.add_argument("--skip-respond", action="store_true",
                        help="评测模式：跳过结论 LLM 生成（判分不读结论，省 1 次调用/题）")
    p_eval.add_argument("--budget", default=None,
                        help="预算账本 markdown：轮末自动追加一行（题数×调用/tokens 实测；"
                             "估算成本与累计两列留待填由人折算），如 runs/m5-budget.md")

    p_report = sub.add_parser("report", help="两轮评测对比：qadata report --baseline A --current B")
    p_report.add_argument("--baseline", required=True)
    p_report.add_argument("--current", required=True)
    p_report.add_argument("--types", default=None,
                          help="题型标签 JSONL（M4 归因产物，如 runs/m4-attribution.jsonl）")
    p_report.add_argument("--out", default=None, help="报告 markdown 输出路径（默认打印）")

    args = parser.parse_args(argv)

    if args.cmd == "ask":
        tracer = TraceLogger("runs/traces.jsonl")
        answer = run_question(args.db_path, args.question, evidence=args.evidence, tracer=tracer)
        _print_answer(answer)
        return 0
    if args.cmd == "eval":
        question_ids = None
        if args.ids:
            question_ids = json.loads(Path(args.ids).read_text(encoding="utf-8"))
        if args.variants:
            from qadata.eval.variants import load_variants, run_variants

            run_variants(args.questions, args.db_dir, load_variants(args.variants),
                         question_ids=question_ids, sample=args.sample, resume=args.resume,
                         concurrency=args.concurrency, qps=args.qps,
                         skip_respond=args.skip_respond)
            return 0
        from dataclasses import replace as dc_replace

        from qadata.config import load_settings
        from qadata.eval.bird import run_eval
        from qadata.llm.gateway import build_llm
        from qadata.llm.ratelimit import RateLimiter

        settings = load_settings()
        if args.qps is not None:
            settings = dc_replace(settings, max_qps=args.qps)
        limiter = RateLimiter(settings.max_qps) if settings.max_qps > 0 else None
        run_eval(
            questions_path=args.questions,
            db_dir=args.db_dir,
            sample=args.sample,
            question_ids=question_ids,
            out_path=args.out,
            resume=args.resume,
            concurrency=args.concurrency,
            settings=settings,
            limiter=limiter,
            llm=build_llm(settings),  # 共享实例贯穿所有线程（替代逐题自建）
            skip_respond=args.skip_respond,
            budget_path=args.budget,
        )
        return 0
    if args.cmd == "report":
        from qadata.eval.report import build_report, print_report_summary

        print_report_summary(args.baseline, args.current)
        md = build_report(args.baseline, args.current, types_path=args.types)
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
        else:
            console.print(md)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
