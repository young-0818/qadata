"""M9 票 06 例题库与 embedding 召回——GSSC Select 格的上岗料池（spec §二 Q6／ADR-0002）。

例题库＝人签题对（问题→已验证 SQL），住智能体目录 `examples.yaml`（文件即数据库，
AgentStore/sessions 同款；删智能体连带清）。**唯一进料口＝`qadata examples-sign` 人签**
——每条必带 signed_by，装载时无签条目整档拒收；会话成功轮永不自动吸收（spec §五
「错误自我强化」被否在案——本模块不存在第二条写入口，机制钉见
tests/test_embedding_recall.py 的零触扫描＋无签拒收）。

召回＝hybrid（纯向量漏精确表名列名/取值字面量，业界已修正——AskTable 先例）：
排序＝**关键词精确命中作第一排序键（保底＝字典序硬保证，不是加法权重）**、向量点积
次键（引号字面量／拉丁标识符／≥2 位数字算关键词）；top-K=3 先命中后向量降序取、
**升序注入**（最像的贴问题最近，DB-GPT 论文形态，渲染归 prompts.format_examples_block）。
索引＝文件＋内存点积，**不上向量库**。

ponytail: 条目破 ~5 万或多进程共享 → 本地 ANN（usearch，仍是文件）；几万表级真瓶颈
在分层召回＋选表预算（RASL 形状），非索引——判据登记 spec §五（重提服务级向量库先读它）。
不设分数下限（授权句＋票 04 保险丝兜住无关料）；换 embedding 模型＝旧向量作废，
维度不合的条目向量面自动出局、关键词保底仍活（宁降级不误打分）。

失败纪律（值采样先例的改进版——**不静默**）：坏档／向量化未配置／端点挂＝降级
不召回＋审计账本 recall 行入账（outcome 行无 token 标记＝不烧调用数，budget_fuse
先例；真发生的向量化调用以 outcome=ok 行记「一次向量化调用」本体），本轮照常作答。
入账面照 digest 先例（web 侧旁路调用＝账本单出口）：不开 tool 胶囊帧——节点外的
回调够不到 obs.mirror 包装后的帧流，为补一条帧开第二出口不值；审计真值在账本。
空池（无档/零条）或未挂接（embedder 缺位、CLI/eval）＝Select 恒等＝逐字节现状。
"""
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from qadata.graph.prompts import format_examples_block
from qadata.retrieval.values import extract_keywords
from qadata.web._fs import atomic_write

EXAMPLES_FILENAME = "examples.yaml"  # 智能体目录内唯一档名（写入口单源＝sign_examples）
RECALL_TOP_K = 3  # 注入题对上限（ponytail: 拍脑袋小常数——判卷要调只动这里）
# 关键词料（引号字面量/拉丁标识符/≥2 位数字；CJK 词无边界、例题面由向量管不开
# cjk 旗）＝retrieval.values.extract_keywords 共读单源（M10 票 03 值链扩 CJK 同闸）


class ExampleError(ValueError):
    """例题库面的诚实拒绝（坏档/无签条目/坏向量——不带病召回）。"""


@dataclass(frozen=True)
class Example:
    q: str
    sql: str
    signed_by: str
    vec: tuple[float, ...]


def _bank_file(agent_dir: str | Path) -> Path:
    return Path(agent_dir) / EXAMPLES_FILENAME


def _validated_entry(e: Any, f: Path, i: int) -> Example:
    if (not isinstance(e, dict) or not str(e.get("q") or "").strip()
            or not str(e.get("sql") or "").strip()
            or not str(e.get("signed_by") or "").strip()):
        # signed_by 缺失＝自动吸收无门可走的机制闸：机器生成的条目永远不会带人签
        raise ExampleError(f"例题库条目形状不正（q/sql/signed_by 须非空）：{f.name} 第 {i + 1} 条")
    vec = e.get("vec")
    if (not isinstance(vec, list) or not vec
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in vec)):
        raise ExampleError(f"例题库向量列不正：{f.name} 第 {i + 1} 条")
    return Example(q=str(e["q"]).strip(), sql=str(e["sql"]).strip(),
                   signed_by=str(e["signed_by"]).strip(), vec=tuple(float(x) for x in vec))


def load_examples(agent_dir: str | Path) -> list[Example]:
    """装载＋形状校验（缺档＝[]＝默认关；坏档如实炸，调用方负责降级入账不静默）。"""
    f = _bank_file(agent_dir)
    if not f.is_file():
        return []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ExampleError(f"例题库读不动：{f.name}（{e}）") from e
    if data is None:
        return []  # 空文件＝空池（懒建档的同款宽容）
    if not isinstance(data, list):
        raise ExampleError(f"例题库形状不正（须为题对列表）：{f.name}")
    return [_validated_entry(e, f, i) for i, e in enumerate(data)]


def sign_examples(agent_dir: str | Path, pairs: Sequence[dict],
                  embed: Callable[[list[str]], list[list[float]]]) -> tuple[int, int]:
    """人签进料（唯一写入口）：{q, sql, signed_by} 全非空才收，同题重签＝覆盖（后签为真）；
    坏行如实计数拒收不静默。批量向量化＝**一次** embed 调用。返回 (签入数, 拒收数)。"""
    rows, skipped = [], 0
    for p in pairs:
        if (not isinstance(p, dict)
                or not all(str(p.get(k) or "").strip() for k in ("q", "sql", "signed_by"))):
            skipped += 1
            continue
        rows.append({"q": str(p["q"]).strip(), "sql": str(p["sql"]).strip(),
                     "signed_by": str(p["signed_by"]).strip()})
    if not rows:
        return 0, skipped
    f = _bank_file(agent_dir)
    if not f.parent.is_dir():
        raise ExampleError(f"智能体目录不存在：{f.parent}（例题库不建孤儿档）")
    bank = {e.q: e for e in load_examples(agent_dir)}  # 坏档＝如实炸，不带病覆盖人签料
    for p, vec in zip(rows, embed([p["q"] for p in rows]), strict=True):
        bank[p["q"]] = Example(vec=tuple(vec), **p)
    body = [{"q": e.q, "sql": e.sql, "signed_by": e.signed_by, "vec": list(e.vec)}
            for e in bank.values()]
    atomic_write(f, yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
    return len(rows), skipped


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False)) if len(a) == len(b) else 0.0


def build_recall(examples: Sequence[Example], embedder, *, top_k: int = RECALL_TOP_K,
                 tracer=None) -> Callable[[str], str]:
    """装配 Select 格的召回回调（web 收口构造；question→注入块字符串，空串＝不注入）。

    每问一次向量化调用（题面 memo＝同问重装配只计一次：generate 重试环/精准模式
    的 question 不变，欠账不逐次翻倍——「零新增生成调用＋一次向量化调用」的账形）；
    一切失败＝降级不召回＋入账，绝不拦本轮答题。"""
    memo: dict[str, str] = {}

    def _ledger(outcome: str, *, hits: int = 0, reason: str = "",
                t0: float = 0.0) -> None:
        if tracer is not None:
            tracer.log("recall", outcome=outcome, hits=hits, pool=len(examples),
                       model=str(getattr(embedder, "model", "")),
                       **({"latency_s": round(time.perf_counter() - t0, 2)}
                          if outcome == "ok" else {"reason": reason[:160]}))

    def recall(question: str) -> str:
        if question in memo:
            return memo[question]
        t0 = time.perf_counter()
        try:
            qv = embedder.embed([question])[0]
        except Exception as e:  # noqa: BLE001 端点挂＝降级不召回（票 06 失败纪律）：入账、照常、不重试
            memo[question] = ""
            _ledger("failed", reason=f"向量化调用失败：{e}", t0=t0)
            return ""
        kws = extract_keywords(question)
        # 保底闸语义（双轴评审 Spec c3 追补）：命中标志＝字典序第一键——加法权重在
        # dot∈[-1,1] 全幅下不是严格下界（命中者自身 dot 为负仍可落榜），硬保证在此；
        # 命中层内/未命中层内各按向量点积降序，同分按库序（人签先签者靠前）——确定性。
        scored = [(bool(kws) and any(t in e.q.lower() or t in e.sql.lower()
                                     for t in kws), _dot(qv, e.vec), i, e)
                  for i, e in enumerate(examples)]
        top = sorted(scored, key=lambda x: (not x[0], -x[1], x[2]))[:top_k]
        block = format_examples_block([(e.q, e.sql) for _, _, _, e in reversed(top)])
        memo[question] = block
        _ledger("ok", hits=len(top), t0=t0)
        return block

    return recall
