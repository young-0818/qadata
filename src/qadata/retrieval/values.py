"""M10 票 02 值采集路——预算封顶·逐值离线 embed（spec §二 Q5 修正条／票 00 探针档）。

**本票只建档不读档**——值链查询＋贴纸条＝票 03，「建了没人读」是预期状态。采集闸：
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
"""
import sqlite3
from pathlib import Path
from typing import NamedTuple

from qadata.retrieval.store import (
    DEFAULT_INDEX_DIR,
    RetrievalError,
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
