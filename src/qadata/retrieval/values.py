"""M10 票 02 值采集＋票 03 值链查询——预算封顶·逐值离线 embed·intent 搭车贴纸条
（spec §二 Q5 修正条／票 00 探针档）。

采集闸（票 02）：
文本亲和列（SAMPLE_TYPE_KEYS 沿借 M8）→ CHESS 式黑名单列名子串跳过（id/url/email/
phone/date/address 类——保守向＝宁多跳不误采，误伤如 "is_paid" 在册判据文件）→
全库**字符量预算封顶**（VALUE_INDEX_TOTAL_CHARS，条目随预算 ≤ 数千级，判据文件
.scratch/qadata-m10/ticket02-value-budget.md）：超预算**整列如实放弃**（first-fit＝
整列进出逐列试，一列胖子不配饿死全库），放弃数进回执＝播报可见。

embedding 面（票 00 探针裁决，蓝图修正条）：嵌**裸值字符串**＝实测跨文字体系形态
（"北京分行"→'BJ Branch' cos 0.578 居首；带列上下文的向量化探针没测过＝不做，
纸条贴哪由票 03 附注块管）。同值跨列只 embed 一发＋档内一向量（dedupe＝花费纪律）；
重跑未变值零重 embed（值字符串即缓存键——表卡 hash 复用机制的值面兑现）；
**换 embedding 模型＝model_id 不合＝旧向量整档作废**（表卡同语义一钉）。

采集工艺沿借 M8 票 02：有界窗口 DISTINCT＋progress-handler 超时（schema.distinct_values
公开面）；一切库侧失败（超时/坏视图/锁）静默跳列——坏库不连累已采列。超长值
（>64 字）不是 WHERE 字面量料，采时即弃（防描述性长文本一口吃掉预算，与黑名单
同属「不是料」而非「放弃」，不进 dropped 账）。

查询面（票 03，build_value_link）＝值链全程：understand 现成意图**搭车**抽词
（owner 裁 A'＝零新增生成调用；intent=None＝无载体本轮无纸条——spec §二 Q5 成文；
独立 LLM 抽词被否在册勿回锅）→ 关键词一批 embed（每问 ≤1 次向量调用、memo 先例、
不烧生成账）→ numpy 全扫 → **逐列 top-K＋相对边际**（≥0.9×max＝CHESS 先例；绝对
阈值只作第二道闸——探针实测正误 margin 仅 0.043，绝对阈值不可靠）→ 纸条块
（`列 —— 库里实际这么存：…`，节头单源，贴 schema 上下文最末；字符串精确命中＝
免费保底轴排最前，票 06 hybrid 字典序先例、不治跨文）。intent=None/无关键词/
缺档/缺 embedder 四路＝不贴、照常作答、value_link 行入账可见（不烧生成调用数，
recall 行先例）。保险丝「撤值纸条」位挨着撤参考例题（gssc，票 04 淘汰序）。
"""
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import numpy as np

from qadata.retrieval.store import (
    DEFAULT_INDEX_DIR,
    RetrievalError,
    StoredValues,
    load_values,
    write_values,
)
from qadata.tools.db import open_readonly
from qadata.tools.schema import (
    VALUE_QUERY_TIMEOUT_S,
    VALUE_SCAN_WINDOW,
    distinct_values,
    is_text_affinity,
    list_tables,
)

# 值索引全局字符预算（Σ|value| 按列计费、跨列重复照计＝条目数千级封顶；
# 数值推导与保守方向＝ticket02-value-budget.md，放宽＝改判据文件再改此数）
VALUE_INDEX_TOTAL_CHARS = 20_000
# CHESS 式黑名单：小写列名**子串**命中即跳（宁多跳不误采——误杀是如实漏、漏杀是噪声
# 进档；"card"/"tel" 不在列：'GOLD'vs'gold' 活病灶料不能扔、"hotel" 会被 "tel" 冤；
# "issue" 在册＝financial 实拍：card.issued 607 条日期形态值独占 78% 预算，
# 名字不含 date 而值全是 date 类——判据文件同档）
_VALUE_INDEX_BLACKLIST = ("id", "url", "email", "phone", "fax", "address", "zip",
                          "postcode", "date", "issue", "passport", "ssn", "iban")
_VALUE_INDEX_MAX_CHARS = 64  # 单值最长（超长＝描述性文本，非值链料）


class ValueBuild(NamedTuple):
    """build_value_index 回执（CLI 如实打印——放弃与花费都不静默）。

    columns/values＝入档列数/条目数；embedded/reused＝**唯一值串**口径（同值跨列
    只算一发，embedded＋reused＝唯一值数）；dropped＝超预算整列出局数。"""

    dir: Path
    columns: int
    values: int
    embedded: int
    reused: int
    dropped: int


def _blacklisted(col: str) -> bool:
    c = col.lower()
    return any(k in c for k in _VALUE_INDEX_BLACKLIST)


def collect_value_candidates(db_path: str | Path, *,
                             total_chars: int = VALUE_INDEX_TOTAL_CHARS,
                             scan_window: int = VALUE_SCAN_WINDOW,
                             budget_s: float = VALUE_QUERY_TIMEOUT_S,
                             ) -> tuple[list[tuple[str, str, list[str]]], int]:
    """采集候选（确定性＝表序[list_tables 字典序]×PRAGMA 列序×ORDER BY 1，双跑同档）：
    文本亲和→黑名单→有界 DISTINCT→整列进出吃字符预算。返回 ([(表,列,[值])], 放弃列数)。
    只读连接唯一入口（沙箱①层）；库侧失败静默跳列（值采样同纪律）。"""
    conn = open_readonly(str(db_path))
    try:
        out, dropped, used = [], 0, 0
        for t in list_tables(conn):
            try:
                info = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
            except sqlite3.Error:
                continue  # 坏表/坏视图连列清单都拿不到＝整表跳，不连累别的表
            for col, decl in ((str(r[1]), str(r[2] or "").upper()) for r in info):
                if not is_text_affinity(decl) or _blacklisted(col):
                    continue
                try:
                    vals = distinct_values(conn, t, col, max_rows=scan_window,
                                           scan_window=scan_window, budget_s=budget_s)
                except sqlite3.Error:
                    continue  # 超时/视图背后炸＝静默跳该列（坏库不连累已采列）
                strs = [s for s in dict.fromkeys(str(v) for v in vals)
                        if s.strip() and len(s) <= _VALUE_INDEX_MAX_CHARS]
                cost = sum(map(len, strs))
                if not strs:
                    continue
                if used + cost > total_chars:
                    dropped += 1  # 整列如实出局（first-fit：后面的窄列照进）
                    continue
                used += cost
                out.append((t, col, strs))
        return out, dropped
    finally:
        conn.close()


def build_value_index(db_path: str | Path, embedder, *,
                      root: str | Path = DEFAULT_INDEX_DIR,
                      total_chars: int = VALUE_INDEX_TOTAL_CHARS,
                      scan_window: int = VALUE_SCAN_WINDOW,
                      budget_s: float = VALUE_QUERY_TIMEOUT_S) -> ValueBuild:
    """离线建值档（index-build 的值面，与 build_index 并列、各管各档）：收料→
    旧档按值串复用（model_id 不合＝缓存清空＝整档重 embed）→新值一批 embed→
    原子写档。返回 ValueBuild——CLI 如实打印，花费与放弃不静默。"""
    cols, dropped = collect_value_candidates(
        db_path, total_chars=total_chars, scan_window=scan_window, budget_s=budget_s)
    cache: dict[str, tuple[float, ...]] = {}
    try:
        stored = load_values(db_path, root=root)
    except RetrievalError:
        stored = None  # 坏档＝无料可复用（整档重建覆盖，embedded 计数自证）
    if stored is not None and stored.model_id == embedder.model:
        cache = dict(stored.vecs)  # 值字符串即缓存键（跨列同值天然共档）
    todo = sorted({v for _, _, vals in cols for v in vals} - set(cache))
    if todo:
        cache.update(dict(zip(todo, embedder.embed(todo), strict=True)))
    vecs = {v: list(cache[v]) for _, _, vals in cols for v in vals}
    d = write_values(db_path, embedder.model, cols, vecs, root=root)
    uniq = len({v for _, _, vals in cols for v in vals})
    return ValueBuild(dir=d, columns=len(cols), values=sum(len(v) for _, _, v in cols),
                      embedded=len(todo), reused=uniq - len(todo), dropped=dropped)


# ── 票 03 查询面：intent 搭车抽词 → 一批 embed → numpy 全扫 → 逐列 top-K＋纸条 ──

# 关键词料＝引号字面量＋拉丁标识符（表名/列名形态）＋≥2 位数字（取值字面量形态）
# ＝例题库票 06 同闸（单源在此，examples 反向共读）；cjk＝票 03 值链扩项：CJK 连续
# 串**不分词**（"北京分行"整串一个词——跨文链上靠向量面，探针档实证形态）。
_KEYWORD_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*)|(\d{2,})")
_CJK_RUN_RE = re.compile(r"[一-鿿]{2,}")


def extract_keywords(text: str, *, cjk: bool = False) -> set[str]:
    """题面关键词抽取（值链与例题库共读单源）。cjk 仅值链开——例题面行为不动。"""
    out = {t.lower() for m in _KEYWORD_RE.finditer(text)
           if (t := next((g for g in m.groups() if g), ""))
           and (m.lastindex <= 2 or len(t) >= 2)}
    if cjk:
        out |= set(_CJK_RUN_RE.findall(text))
    return out


# CJK 边际线＝本波唯一校准旋钮（业界无先例照抄——英文考面没这病灶，spec §二 Q5）：
# 两值皆保守起步、**待票 07 定标**（探针实测正误 margin 仅 0.043，绝对阈值不可靠，
# 只作第二道闸；相对边际 ≥0.9×max 照 CHESS 先例）。
VALUE_LINK_TOP_K = 3        # 逐列贴值上限（精确命中先、向量分降序截尾）
VALUE_LINK_MARGIN = 0.9     # 相对边际系数（≥系数×列内最高分才贴）——待票 07 定标
VALUE_LINK_MIN_SCORE = 0.5  # 绝对阈值第二道闸（探针正解 0.578 下方保守取）——待票 07 定标

# 纸条节头＝渲染（本模块）与保险丝淘汰（gssc._cut_value_stickers）共读的单源字面
# （VALUE_SAMPLE_HEADER／EXAMPLES_HEADER 先例）。块贴 schema 上下文**最末**（值采样
# 块之后）——淘汰序撤值纸条在砍值采样之前，split 切尾各不连累前料。
VALUE_STICKER_HEADER = ("值纸条（题面关键词在本库的实际存储取值，"
                        "WHERE 字面量以此为准；与本题无关则忽略）：")


class ValueMatch(NamedTuple):
    """一列的纸条命中：hits＝(值, 向量分, 同文精确命中) 已按 精确→分数→值 排好。"""

    table: str
    column: str
    hits: tuple[tuple[str, float, bool], ...]


def _scan_values(stored: StoredValues, kws: list[str], kvecs: list[list[float]],
                 top_k: int) -> list[ValueMatch]:
    """numpy 全扫（owner 裁预装的点积引擎，cards._top_tables 同姿势）：逐值取对
    各关键词的最大余弦 → 逐列 相对边际＋绝对第二道闸 → 精确命中无条件进＋排最前
    （免费保底轴）→ top-K 截尾。零向量得分记 0（宁不误排不假高分）。"""
    k = np.asarray(kvecs, dtype=float)
    if k.ndim != 2 or k.shape[0] != len(kws):  # 端点少发/形状异常＝降级不猜（宁缺勿错贴）
        raise RetrievalError(f"查询向量形状不正：期望 {len(kws)}×n，实得 {k.shape}")
    names = sorted(stored.vecs)  # 行序确定（不依赖档装载序）
    m = np.asarray([stored.vecs[v] for v in names], dtype=float)
    if m.ndim != 2 or m.shape[1] != k.shape[1]:  # 维度不合＝宁降级不误打分（cards 同语义）
        raise RetrievalError(f"向量维度不合：档内 {m.shape[1:]} ≠ 查询 {k.shape[1]}")
    denom = np.linalg.norm(m, axis=1)[:, None] * np.linalg.norm(k, axis=1)[None, :]
    sims = np.divide(m @ k.T, denom, out=np.zeros((len(names), len(kws))), where=denom > 0)
    best = sims.max(axis=1)
    kw_lower = {w.lower() for w in kws}
    out: list[ValueMatch] = []
    idx = {v: i for i, v in enumerate(names)}
    for col in stored.columns:
        scored = [(v, float(best[idx[v]]), v.lower() in kw_lower) for v in col.values]
        if not scored:
            continue
        # 边际线以列内**全部**得分的最高者为参照（精确命中也计分入列）；
        # 精确轴无条件进——同文命中＝字面量就在库里，向量端点漂了也必修得对。
        floor = max(VALUE_LINK_MIN_SCORE, VALUE_LINK_MARGIN * max(s for _, s, _ in scored))
        kept = sorted((x for x in scored if x[2] or x[1] >= floor),
                      key=lambda x: (not x[2], -x[1], x[0]))
        if kept:
            out.append(ValueMatch(col.table, col.column, tuple(kept[:top_k])))
    return out


def format_value_sticker(lines: list[tuple[str, str, list[str]]]) -> str:
    """纸条块渲染（纯函数，零 LLM 确定性拼装）；空列表＝""＝不贴逐字节现状。"""
    if not lines:
        return ""
    return (VALUE_STICKER_HEADER + "\n"
            + "\n".join(f"- {t}.{c} —— 库里实际这么存：{'｜'.join(repr(v) for v in vals)}"
                        for t, c, vals in lines))


def build_value_link(db_path: str | Path, embedder, *, tracer=None,
                     root: str | Path = DEFAULT_INDEX_DIR,
                     top_k: int = VALUE_LINK_TOP_K) -> Callable[..., str]:
    """装配值链查询回调 `(question, intent, 入选表)→纸条块`（""＝不贴）。

    恒返回回调（表卡缺 embedder 静默 None 的形态在此分道——值链**无开关、缺料必须
    入账可见**，spec §五「产物即开关」行）。每问 ≤1 次向量调用（题面 memo＝重试环/
    精准模式不翻倍，recall 先例账形）；一切失败＝降级不贴＋value_link 行入账＋照常
    作答（ADR-0005 永不因检索挂拒答）。intent=None＝搭车载体缺席本轮无纸条（spec
    §二 Q5 成文，连题面抽词都不发——宁漏不猜）；filters＝题面原样摘录（M5 载体 A
    白送料）＋题面 extract_keywords(cjk=True)。
    扫描与入选表无关（每问一次），**入选表过滤在渲染侧**（窄出每次进不同表集，
    memo 的重扫描不白做）；纸条只贴最终入选表的命中列（票面「相关列旁挂」）。"""
    memo: dict[str, list[ValueMatch]] = {}

    def _ledger(outcome: str, *, kws: int = 0, hits: int = 0, pool: int = 0,
                reason: str = "", t0: float = 0.0) -> None:
        if tracer is not None:
            tracer.log("value_link", outcome=outcome, kws=kws, hits=hits, pool=pool,
                       model=str(getattr(embedder, "model", "")),
                       **({"latency_s": round(time.perf_counter() - t0, 2)}
                          if outcome == "ok" else {"reason": reason[:160]}))

    def _query(question: str, intent: dict | None) -> list[ValueMatch]:
        if not isinstance(intent, dict):
            _ledger("skipped", reason="意图解析失败（intent=None）——搭车载体缺席，本轮无纸条")
            return []
        kws = sorted(extract_keywords(question, cjk=True)
                     | {f.strip() for f in (intent.get("filters") or [])
                        if isinstance(f, str) and f.strip()})
        if not kws:
            _ledger("skipped", reason="题面与 filters 均无关键词料")
            return []
        try:
            stored = load_values(db_path, root=root)
        except RetrievalError as e:
            _ledger("failed", kws=len(kws), reason=str(e))  # 前缀单源不套双份（examples 纪律）
            return []
        if stored is None or not stored.columns:
            # 缺档闸在缺 embedder 之前：缺料根因＝未建，一行给出可操作指令；
            # 缺 embedder 只在有档可建链时才点名（账目归因不套双份）
            _ledger("skipped", kws=len(kws),
                    reason="缺值索引档（现读降级＝现状；构建＝qadata index-build <库文件>）")
            return []
        if embedder is None:
            _ledger("failed", kws=len(kws), pool=len(stored.vecs),
                    reason="向量化未配置（QADATA_EMBED_MODEL 为空）")
            return []
        if stored.model_id != embedder.model:
            _ledger("failed", kws=len(kws), pool=len(stored.vecs),
                    reason=f"值档过期（建档模型 {stored.model_id} ≠ 当前 {embedder.model}）"
                           "——整档作废，重建靠 index-build")
            return []
        t0 = time.perf_counter()
        try:
            kvecs = embedder.embed(kws)  # 一批一发＝每问唯一向量调用（memo 之后不再碰）
        except Exception as e:  # noqa: BLE001 端点挂＝降级不贴（失败纪律：入账、照常、不重试）
            _ledger("failed", kws=len(kws), pool=len(stored.vecs),
                    reason=f"向量化调用失败：{e}", t0=t0)
            return []
        try:
            matches = _scan_values(stored, kws, kvecs, top_k)
        except RetrievalError as e:  # 维度不合/形状异常＝同路降级，绝不拦答题
            _ledger("failed", kws=len(kws), pool=len(stored.vecs), reason=str(e), t0=t0)
            return []
        _ledger("ok", kws=len(kws), hits=len(matches), pool=len(stored.vecs), t0=t0)
        return matches

    def _link(question: str, intent: dict | None, tables: list[str]) -> str:
        if question not in memo:
            memo[question] = _query(question, intent)
        sel = set(tables)
        rows = [(not any(e for _, _, e in m.hits), m.table, m.column,
                 [v for v, _, _ in m.hits])
                for m in memo[question] if m.table in sel]
        rows.sort(key=lambda r: r[0])  # 精确命中列靠前（stable＝其余保持档内构建序）
        return format_value_sticker([(t, c, vals) for _, t, c, vals in rows])

    return _link
