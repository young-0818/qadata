"""CLI 薄壳：ask 单问 / eval 评测。核心逻辑全部在包内。"""
import argparse
import sys

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

    p_eval = sub.add_parser("eval", help="BIRD 评测（Task 10 接通）")
    p_eval.add_argument("--questions", required=True)
    p_eval.add_argument("--db-dir", required=True)
    p_eval.add_argument("--sample", type=int, default=None)

    args = parser.parse_args(argv)

    if args.cmd == "ask":
        tracer = TraceLogger("runs/traces.jsonl")
        answer = run_question(args.db_path, args.question, tracer=tracer)
        _print_answer(answer)
        return 0
    if args.cmd == "eval":
        from qadata.eval.bird import run_eval  # Task 10 实现

        run_eval(
            questions_path=args.questions,
            db_dir=args.db_dir,
            sample=args.sample,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
