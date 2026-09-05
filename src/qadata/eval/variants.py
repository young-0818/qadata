"""变体矩阵驱动（#9 最小版，M3）：配置文件化 model 变体，一条命令跑一组实验。
M3 只交付工具链，实际实验 M4 跑（预算纪律）。"""
from pathlib import Path

import yaml
from rich.console import Console

from qadata.config import Settings, load_settings
from qadata.eval.bird import run_eval
from qadata.eval.report import build_multi_report
from qadata.llm.gateway import build_llm

console = Console()


def load_variants(path: str) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    variants = data["variants"] if isinstance(data, dict) else data
    if not variants:
        raise ValueError("variants 配置为空")
    for v in variants:
        if not v.get("name") or not v.get("model"):
            raise ValueError(f"变体必须含 name 与 model：{v}")
    return variants


def run_variants(questions_path: str, db_dir: str, variants: list[dict],
                 question_ids: list[int] | None = None, sample: int | None = None,
                 resume: bool = False) -> list[dict]:
    base = load_settings()
    summaries = []
    for v in variants:
        s = Settings(api_key=base.api_key, base_url=v.get("base_url") or base.base_url,
                     model=v["model"], max_rows=base.max_rows,
                     retry_budget=base.retry_budget, sql_timeout_s=base.sql_timeout_s,
                     llm_timeout_s=base.llm_timeout_s, max_qps=base.max_qps)
        summary = run_eval(questions_path, db_dir, sample=sample, question_ids=question_ids,
                           llm=build_llm(s), out_path=f"runs/eval-{v['name']}.jsonl",
                           resume=resume)
        summaries.append({"name": v["name"], **summary})
    paths = [f"runs/eval-{v['name']}.jsonl" for v in variants]
    console.print(build_multi_report(paths, [v["name"] for v in variants]))
    return summaries
