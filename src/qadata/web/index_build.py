"""建索引 web 管理门（M11 票 02，owner 批 2026-09-22：web 化前提兑现）。

与 CLI index-build 同逻辑同目录（ADR-0004 库派生料跟库走、ADR-0005 显式管理动作）：
build_index→build_value_index 顺序与「卡面回执先于值面、值面失败不吞卡面」的如实分报
全从 CLI 薄壳语义平移。**住独立模块的理由＝零触钉保结构**：test_value_index 的
「web 运行面源码出现建侧符号即红」不许为门放松——管理门自有文件，app.py/问数链路
照旧零知情（feedback.py 分家先例）。job 态（running/终态）簿记仍在 app.py 闭包，
本模块是无状态纯执行体，可被测试直呼。"""
from pathlib import Path
from typing import Any

from qadata.retrieval import cards, values


def run_index_build(db_path: str, embedder, index_root: str | Path) -> dict[str, Any]:
    """跑一次整档构建（表卡→值索引），返回 {"state","note"} 人话回执。
    一切失败转真话入 note（CLI 薄壳同形）——本函数永不向上抛构建类异常。"""
    notes: list[str] = []
    state = "ok"
    try:
        res = cards.build_index(db_path, embedder, root=index_root)
        notes.append(f"表卡 {res.tables} 张（向量化 {res.embedded} 发、复用 {res.reused}）")
    except Exception as e:  # noqa: BLE001 管理动作面：坏库/坏档/端点挂一律人话入 job，不连累服务
        state = "failed"
        notes.append(f"表卡构建失败：{e}")
    if state == "ok":
        try:
            cres = values.build_value_index(db_path, embedder, root=index_root)
            notes.append(f"值索引 {cres.values} 条 / {cres.columns} 列"
                         f"（复用 {cres.reused}、放弃 {cres.dropped}）")
        except Exception as e:  # noqa: BLE001 值面失败不吞卡面回执——卡档已建成，如实分报
            state = "partial"
            notes.append(f"值索引构建失败（表卡档已建成）：{e}")
    return {"state": state, "note": "；".join(notes)}
