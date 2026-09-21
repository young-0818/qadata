"""M10 票 01 表卡路——大库选表的"宽进窄出"检索段（spec §二 Q6／ADR-0004/0005）。

卡片粒度＝**表级**（表名＋列名行＋database_description 列注释料）；**列永不单独
进召回**（选中表整表给列——DataAgent 共识，防列被 top-K 截断；《Death of Schema
Linking?》：硬筛丢必需列的伤害＞噪声）。构建＝`qadata index-build` 显式 CLI、
纯离线、逐卡 embedding 是索引构建期唯一花钱处（每表一向量、整批一次协议调用、
几百发封顶、CLI 打印计数不静默）；增量＝条目文本哈希没变不重 embed（manifest 承载，LlamaIndex
IngestionCache 先例）；换模型＝整档作废。

构建＝逐卡一次性 embedding 是索引构建期唯一花钱处（每表一**向量**、一批一次调用、
几百发封顶——FakeEmbedder 计数钉同形）。

查询＝粗召回调（question→候选表名 top-10，numpy 全扫点积）：由 explore 在大库
分支（schema 超 FULL_SCHEMA_LIMIT）消费——粗召宽进防漏表，既有 `_pick_tables_with_llm`
窄出防错用，最后 `foreign_key_closure` 确定性补外键亲戚表（三段不合并＝漏料与
错选分开记账，判卷分开归因）。**小库根本不读索引文件**（全量路在粗召闸之前）。

失败纪律（recall 先例）：坏档/过期（model_id 不合）/端点挂＝**降级现状路径＋
账本 table_recall 行入账不静默**（outcome 行无 token 标记＝不烧生成调用数；ok 行
＝一次向量化调用本体）；缺档＝默认关＝逐字节现状零行。题面 memo：同问重装配
（explore 重试环二次进入）不翻倍向量化。永不因检索挂而拒答（ADR-0005）。
"""
import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import numpy as np

from qadata.retrieval.store import (
    DEFAULT_INDEX_DIR,
    RetrievalError,
    StoredCards,
    load_cards,
    write_cards,
)
from qadata.tools.db import open_readonly
from qadata.tools.schema import list_tables, load_description

COARSE_TOP_K = 120  # 宽进阈值——**票 07 已定标（2026-09-21）**：spec 起步值 top-10 在 391 表
# 合成大库（runs/m10-round3-rates.json 免费全科）正确表逐表入率仅 63.2%（族口径 68.8%）——
# 跨域近义词表＋同名族分数带整体压过高弱族基表，10 个格位装不全一道题的 2-4 张必需表。
# 定标＝逐表族口径 ≥95% 线取最小 K：120（逐表 96.0%/族 96%，全题齐 47/50＝94%，残余 3 题
# 为弱名多族题＝结构上限，分层召回升级判据在册 spec §五）。不设分数下限（保守向）。


class IndexBuild(NamedTuple):
    """build_index 回执（CLI 如实打印的料——计数不静默）。"""

    dir: Path
    tables: int
    embedded: int
    reused: int


def card_text(table: str, columns: list[str], description: str = "") -> str:
    """卡片文本单源（构建与查询共用同一形状，哈希稳定性靠它）。"""
    text = f"{table}\n列：{'、'.join(columns)}"
    return f"{text}\n{description}" if description else text


def collect_card_texts(db_path: str | Path) -> list[tuple[str, str]]:
    """逐表出卡（表序＝list_tables 字典序，确定性）；只读连接唯一入口（沙箱①层）。"""
    conn = open_readonly(str(db_path))
    try:
        out = []
        for t in list_tables(conn):
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
            out.append((t, card_text(t, cols, load_description(str(db_path), t))))
        return out
    finally:
        conn.close()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_index(db_path: str | Path, embedder, *,
                root: str | Path = DEFAULT_INDEX_DIR) -> IndexBuild:
    """离线建库（index-build 的唯一逻辑）：收卡→逐卡哈希比对→只 embed 变化条目→
    原子写档。model_id 不合＝旧向量全部作废（整档重 embed）。
    返回 IndexBuild(dir, tables, embedded, reused)——CLI 如实打印，花费不静默。"""
    entries = collect_card_texts(db_path)
    old: dict[str, tuple[str, tuple[float, ...]]] = {}
    try:
        stored = load_cards(db_path, root=root)
    except RetrievalError:
        stored = None  # 坏档＝无料可复用（整档重建覆盖，如实由 embedded 计数自证）
    if stored is not None and stored.model_id == embedder.model:
        old = {c.table: (c.hash, c.vec) for c in stored.cards}
    cards, to_embed = [], []
    for t, text in entries:
        h = _hash(text)
        if (prev := old.get(t)) is not None and prev[0] == h:
            cards.append((t, h, list(prev[1])))
            continue
        cards.append((t, h, None))  # 占位保构建序
        to_embed.append((len(cards) - 1, text))
    if to_embed:
        for (slot, text), vec in zip(to_embed, embedder.embed([t for _, t in to_embed]),
                                      strict=True):
            cards[slot] = (cards[slot][0], cards[slot][1], list(vec))
    d = write_cards(db_path, embedder.model, cards, root=root)
    return IndexBuild(dir=d, tables=len(cards), embedded=len(to_embed),
                      reused=len(cards) - len(to_embed))


def build_table_recall(db_path: str | Path, embedder, *, tracer=None,
                       root: str | Path = DEFAULT_INDEX_DIR,
                       top_k: int = COARSE_TOP_K) -> Callable[[str], list[str] | None] | None:
    """装配大库粗召回调（question→候选表名，None＝缺位/降级走现状路径）。

    embedder 缺位＝None（未配置＝默认关，与例题库召回同语义）；回调**懒载档**——
    小库根本不会调用它（全量路在闸前返回），"全量路根本不读索引文件"的机制本体。
    降级三路（坏档/过期/端点挂）＝入账 table_recall 行＋返回 None，本轮照常走现状。"""
    if embedder is None:
        return None
    memo: dict[str, list[str] | None] = {}

    def _ledger(outcome: str, *, cands: int = 0, reason: str = "",
                t0: float = 0.0) -> None:
        if tracer is not None:
            tracer.log("table_recall", outcome=outcome, cands=cands,
                       model=str(getattr(embedder, "model", "")),
                       **({"latency_s": round(time.perf_counter() - t0, 2)}
                          if outcome == "ok" else {"reason": reason[:160]}))

    def _degrade(question: str, reason: str) -> None:
        memo[question] = None
        if reason:
            _ledger("failed", reason=reason)

    def recall(question: str) -> list[str] | None:
        if question in memo:
            return memo[question]
        try:
            stored = load_cards(db_path, root=root)
        except RetrievalError as e:
            _degrade(question, f"表卡档坏：{e}")  # 前缀单源（examples 双轴评审同纪律）
            return None
        if stored is None or not stored.cards:
            memo[question] = None
            return None  # 缺档＝默认关＝逐字节现状，零行（空池先例）
        if stored.model_id != embedder.model:
            _degrade(question, f"表卡过期（建档模型 {stored.model_id} ≠ 当前 "
                               f"{embedder.model}）——整档作废，重建靠 index-build")
            return None
        t0 = time.perf_counter()
        try:
            qv = embedder.embed([question])[0]
        except Exception as e:  # noqa: BLE001 端点挂＝降级现状（recall 失败纪律：入账、照常、不重试）
            _degrade(question, f"向量化调用失败：{e}")
            return None
        try:
            names = _top_tables(stored, qv, top_k)
        except RetrievalError as e:  # 维度不合等打分异常＝同路降级，绝不拦答题（ADR-0005）
            _degrade(question, str(e))
            return None
        memo[question] = names
        _ledger("ok", cands=len(names), t0=t0)
        return names

    return recall


def cosine_scores(m: np.ndarray, q: np.ndarray) -> np.ndarray:
    """检索域共读单源的余弦全扫（extract_keywords 共读先例同纪律）：每行与查询向的
    余弦点积，零向量行得分记 0（宁不误排不假高分）。维度校验归调用方——
    各家异常型与降级入账文案不同（RetrievalError/KnowledgeError 分域）。"""
    denom = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
    return np.divide(m @ q, denom, out=np.zeros(len(m), dtype=float), where=denom > 0)


def _top_tables(stored: StoredCards, qv: list[float], top_k: int) -> list[str]:
    """numpy 全扫余弦点积（owner 裁预装的点积引擎；几万条内正解——usearch README 口径）。
    同分按档内构建序（stable argsort＝确定性）。"""
    m = np.asarray([c.vec for c in stored.cards], dtype=float)
    q = np.asarray(qv, dtype=float)
    if m.shape[1] != q.shape[0]:  # 维度不合＝换模型残留/端点异常，宁降级不误打分
        raise RetrievalError(f"向量维度不合：档内 {m.shape[1]} ≠ 查询 {q.shape[0]}")
    scores = cosine_scores(m, q)
    order = np.argsort(-scores, kind="stable")[:top_k]
    return [stored.cards[i].table for i in order]
