"""CLI 薄壳：ask 单问 / eval 评测。核心逻辑全部在包内。"""
import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from qadata import run_question  # 顶层导入（模块全局），便于测试 monkeypatch
from qadata.llm.tracing import TRACE_PATH, TraceLogger

console = Console()


def _print_answer(answer) -> None:
    console.rule("问数")
    # M8 票 03：澄清轮不是失败（黄字提示形态），ask 打印后照旧 exit 0——
    # CLI 无前端合成通道，续问靠用户把「补充说明：…」带进下一问的题面
    if answer.failed:
        style = "red"
    elif answer.clarification:
        style = "yellow"
    else:
        style = "green"
    # 票 07：conclusion 即三节组装成品（【结论】【数据依据】【口径说明】【校验标注】），原样输出；
    # 截断标注已并入校验节，不再单列黄字（避免同一事实两处表述漂移）
    console.print(answer.conclusion, style=style, markup=False)
    if answer.result is not None:
        t = Table(show_header=True, header_style="bold")
        for c in answer.result.columns:
            t.add_column(str(c))
        for row in answer.result.rows[:10]:
            t.add_row(*[str(v) for v in row])
        console.print(t)
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

    p_serve = sub.add_parser("serve", help="本地起问数 web demo（M7 票 02.5）：API＋前端同源＋智能体管理")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    from qadata.web.agents import (
        DEFAULT_AGENTS_DIR,  # 默认值单一真源（TRACE_PATH 同款先例）
    )

    p_serve.add_argument("--agents-dir", default=DEFAULT_AGENTS_DIR,
                         help=f"智能体数据目录（每智能体一子目录 meta.yaml＋source.sqlite；默认 {DEFAULT_AGENTS_DIR}）")

    p_fb = sub.add_parser("feedback-export",
                          help="导出反馈票（M8 票 04）：扫旁挂票档×会话档出 JSONL，供人审手工成卷")
    p_fb.add_argument("--agents-dir", default=DEFAULT_AGENTS_DIR,
                      help=f"智能体数据目录（默认 {DEFAULT_AGENTS_DIR}）")
    p_fb.add_argument("--out", default=None,
                      help="输出 JSONL（默认 runs/feedback-<YYYYMMDD>.jsonl）")

    p_sign = sub.add_parser("examples-sign",
                            help="人签题对入例题库（M9 票 06 唯一进料口：JSONL 每行 "
                                 "{q, sql, signed_by}；成功轮永不自动吸收）")
    p_sign.add_argument("--agents-dir", default=DEFAULT_AGENTS_DIR,
                        help=f"智能体数据目录（默认 {DEFAULT_AGENTS_DIR}）")
    p_sign.add_argument("--agent", required=True, help="目标智能体 id（hex12）")
    p_sign.add_argument("--file", required=True,
                        help="人审题对 JSONL（feedback-export 产物人工筛选后改写为每行 "
                             "{q, sql, signed_by}——question 改名 q、逐行补签名）")

    p_report = sub.add_parser("report", help="两轮评测对比：qadata report --baseline A --current B")
    p_report.add_argument("--baseline", required=True)
    p_report.add_argument("--current", required=True)
    p_report.add_argument("--types", default=None,
                          help="题型标签 JSONL（M4 归因产物，如 runs/m4-attribution.jsonl）")
    p_report.add_argument("--paths", action="store_true",
                          help="轨道①分路径模式（M5 票 06）：baseline＝对照轮（关），"
                               "current＝指标轮（开），出三个数＋判卷表＋题型×路径切片")
    p_report.add_argument("--out", default=None, help="报告 markdown 输出路径（默认打印）")

    args = parser.parse_args(argv)

    if args.cmd == "ask":
        tracer = TraceLogger(TRACE_PATH)
        answer = run_question(args.db_path, args.question, evidence=args.evidence, tracer=tracer)
        from qadata.obs import (
            shutdown,  # M9 票 01：出口开时冲刷批缓冲（关态＝跳过，惰性 import 同 serve 姿势）
        )
        shutdown()
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
    if args.cmd == "serve":
        from qadata.web.serve import run_server  # 薄壳：逻辑全在包内

        run_server(host=args.host, port=args.port, agents_dir=args.agents_dir)
        return 0
    if args.cmd == "feedback-export":
        from datetime import datetime

        from qadata.llm.tracing import BEIJING
        from qadata.web.agents import AgentStore
        from qadata.web.feedback import export_feedback  # 逻辑全在包内，CLI 薄壳

        out = args.out or f"runs/feedback-{datetime.now(BEIJING):%Y%m%d}.jsonl"
        n, skipped = export_feedback(AgentStore(args.agents_dir), Path(out))
        console.print(f"导出 {n} 行票 → {out}"
                      + (f"（跳过回查不中 {skipped} 行——票在轮不在，如实上报）" if skipped else ""))
        console.print("提示：产物供人审手工成卷，gold 必须人签——不自动进 tests/*_ids.json")
        return 0
    if args.cmd == "examples-sign":
        from qadata.config import load_settings
        from qadata.llm.gateway import build_embedder  # 唯一生产位向量化通道
        from qadata.web.agents import AgentStore
        from qadata.web.examples import (
            sign_examples,  # 逻辑全在包内，CLI 薄壳；唯一进料口
        )

        settings = load_settings()
        if not settings.embed_model:
            console.print("[red]未配置 QADATA_EMBED_MODEL（.env）——例题库向量化无从谈起[/red]")
            return 1
        store = AgentStore(args.agents_dir)
        try:
            meta = store.get(args.agent)  # 不存在/非法 id → 诚实报错，不建孤儿档
        except Exception as e:  # noqa: BLE001 CLI 薄壳：存储面一切拒绝转人话
            console.print(f"[red]{e}[/red]")
            return 1
        try:
            pairs = [json.loads(ln) for ln in
                     Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
        except (OSError, ValueError) as e:
            console.print(f"[red]题对文件读不动：{e}[/red]")
            return 1
        n, skipped = sign_examples(store.agent_dir(meta.id), pairs,
                                   build_embedder(settings).embed)
        console.print(f"人签 {n} 对题 → {store.agent_dir(meta.id)}"
                      + (f"（拒收无签/坏行 {skipped} 条——人签纪律，不静默）" if skipped else ""))
        console.print("提示：进料口唯一——会话成功轮永不自动吸收（错误自我强化，spec §五 被否在案）")
        return 0
    if args.cmd == "report":
        from qadata.eval.report import (
            build_path_report,
            build_report,
            print_report_summary,
        )

        print_report_summary(args.baseline, args.current)
        md = (build_path_report(args.baseline, args.current, types_path=args.types)
              if args.paths else
              build_report(args.baseline, args.current, types_path=args.types))
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
        else:
            console.print(md)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
