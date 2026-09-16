"""M8 票 04：反馈闭环——旁挂票档（答案可评价、错题可攒卷）。

一票＝一行 append-only JSON，住在会话档**旁边**：
`data/agents/<agent>/sessions/<sid>.feedback.jsonl`，行形
`{ts, vote, question, sql, at}`（ts＝被评轮的落盘时刻，回查锚点）。
不重开会话 PATCH 面：票 09 的 405 钉与「会话面无运行时写入口」裁决在主档
原样存活——反馈是独立端点写独立文件，不进 prompt、不进记忆、不进路由、
不进 AskResponse 契约（边界写进 spec 修订段）。

锁域纪律：模块级 threading.Lock 收口并发 append——与 ask 在途锁**零交叠**
（那把管问答互斥，这把管票行完整；不同文件不同锁）。单进程自托管约定
（在途锁/MemorySaver 同款，docstring 如实写：跨进程写同档会 interleave 行）。
append 走不了票 01 的原子写（那是覆盖语义）：crash 可能留末行半截——
读取时**只宽容末行**（预期尸体），中段坏行整档报错不静默吞（会话档同款纪律）。

导出 `export_feedback` 供**人审手工成卷**：gold 必须人签——「永不编造」延伸
到题集，不自动进 tests/*_ids.json。不做 few-shot 自动注入（载体 A 软注入
判负墓地在册，留 v2）。
"""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from qadata.llm.tracing import BEIJING
from qadata.web._fs import atomic_write
from qadata.web.agents import AgentStore, is_hex12
from qadata.web.sessions import SESSIONS_SUBDIR, SessionStore

VOTES = ("up", "down")
_FEEDBACK_SUFFIX = ".feedback.jsonl"

# 并发 append 唯一收口（模块级＝跨实例一把锁；单进程约定见模块 docstring）
_append_lock = threading.Lock()


class FeedbackError(ValueError):
    """反馈面的诚实拒绝（非法票值/坏票档），API 面映射 400。"""


def feedback_file(store: AgentStore, agent_id: str, sid: str) -> Path:
    return Path(store.agent_dir(agent_id)) / SESSIONS_SUBDIR / f"{sid}{_FEEDBACK_SUFFIX}"


def append_vote(store: AgentStore, agent_id: str, sid: str, *, ts: str, vote: str,
                question: str, sql: str | None) -> None:
    """记一票（调用方已验会话档存在＋ts 对得上轮——本函数只管票的形态与落盘）。"""
    if vote not in VOTES:
        raise FeedbackError(f"vote 必须是 {'/'.join(VOTES)} 之一")
    row = {"ts": ts, "vote": vote, "question": question, "sql": sql,
           "at": datetime.now(BEIJING).isoformat(timespec="seconds")}
    f = feedback_file(store, agent_id, sid)
    with _append_lock:
        f.parent.mkdir(parents=True, exist_ok=True)  # replay 已保证目录在，mkdir 是防御性幂等
        with f.open("a", encoding="utf-8") as h:
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
            h.flush()


def read_votes(store: AgentStore, agent_id: str, sid: str) -> list[dict[str, Any]]:
    f = feedback_file(store, agent_id, sid)
    if not f.is_file():
        return []
    with _append_lock:  # 读侧同锁：不撞 append 的半写窗口（单进程内自洽）
        lines = f.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError as e:
            if i == len(lines) - 1:
                break  # crash 尾：append 的既定代价，只丢最后半行
            raise FeedbackError(f"反馈票档读不动：{f.name} 第 {i + 1} 行") from e
        if not isinstance(row, dict) or "ts" not in row or "vote" not in row:
            raise FeedbackError(f"反馈票档形状不正：{f.name} 第 {i + 1} 行")
        rows.append(row)
    return rows


def latest_votes(store: AgentStore, agent_id: str, sid: str) -> dict[str, str]:
    """ts → 末票（回放合并用；同 ts 多票取末＝文件序后写覆盖先写）。"""
    return {r["ts"]: r["vote"] for r in read_votes(store, agent_id, sid)}


def export_feedback(store: AgentStore, out_path: Path) -> tuple[int, int]:
    """扫全体智能体旁挂票档 × 会话档（按 ts 回查轮，题面/SQL 以轮 payload 为真源）
    → 一份 JSONL 供人审手工成卷。返回 (导出行数, 回查不中跳过数)。

    跳过如实计数上报（不静默吞）：票还在、轮已不在＝档案被动过，人审该知道。"""
    sessions = SessionStore(store)
    rows: list[dict[str, Any]] = []
    skipped = 0
    for meta in store.all():
        root = Path(store.agent_dir(meta.id)) / SESSIONS_SUBDIR
        if not root.is_dir():
            continue
        for f in sorted(root.glob(f"*{_FEEDBACK_SUFFIX}")):
            sid = f.name[: -len(_FEEDBACK_SUFFIX)]
            if not is_hex12(sid):
                continue  # 不是我们的旁挂档（目录里的手放文件不猜）
            turns = {t.get("ts"): t for t in sessions.load(meta.id, sid).turns}
            by_ts: dict[str, dict[str, Any]] = {}
            for r in read_votes(store, meta.id, sid):
                by_ts[r["ts"]] = r  # 同 ts 末票
            for ts, r in by_ts.items():
                t = turns.get(ts)
                if t is None:
                    skipped += 1
                    continue
                rows.append({"agent": meta.id, "agent_name": meta.name,
                             "session": sid, "ts": ts, "vote": r["vote"],
                             "question": t["question"], "sql": t["answer"].get("sql"),
                             "failed": t["failed"]})
    atomic_write(out_path, "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return len(rows), skipped
