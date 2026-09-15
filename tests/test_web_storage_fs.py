"""M8 票 01 专测：原子写唯一入口（web/_fs.py::atomic_write）收口三处落盘。

单元面钉：内容正确、无 .tmp 残留、同路径二次写覆盖、str/bytes 双形态；
故障面钉＝本票存在理由：os.replace 抛错（模拟写一半 crash）→ 目标文件原样
可读回旧内容、不留孤儿 tmp。三写点（meta.yaml / source.sqlite / 会话 yaml）
各过一遍真存储对象——测的是「收口生效」，不是 atomic_write 第二遍单测。
"""
import pytest
import yaml

from qadata.types import QueryResult
from qadata.web._fs import atomic_write
from qadata.web.agents import AgentStore
from qadata.web.sessions import SessionStore, append_turn

# ── 单元：atomic_write 本体 ────────────────────────────────────────


def test_atomic_write_str_and_no_tmp_residue(tmp_path):
    f = tmp_path / "a.yaml"
    atomic_write(f, "甲内容")
    assert f.read_text(encoding="utf-8") == "甲内容"
    atomic_write(f, "乙内容")  # 二次写＝覆盖，不追加不报错
    assert f.read_text(encoding="utf-8") == "乙内容"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]  # 无 .tmp 尸体


def test_atomic_write_bytes(tmp_path):
    f = tmp_path / "db.sqlite"
    atomic_write(f, b"\x00\x53q")
    assert f.read_bytes() == b"\x00\x53q"


def test_atomic_write_replace_failure_keeps_original(tmp_path, monkeypatch):
    """崩溃中途模拟：replace 前文件已成型、replace 抛错 → 旧档原样、无残留。"""
    f = tmp_path / "a.yaml"
    atomic_write(f, "旧内容")
    monkeypatch.setattr("os.replace",
                        lambda *a: (_ for _ in ()).throw(OSError("模拟盘断")))
    with pytest.raises(OSError):
        atomic_write(f, "新内容")
    assert f.read_text(encoding="utf-8") == "旧内容"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]


# ── 三写点收口：各过真存储对象一遍 ─────────────────────────────────


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


def test_meta_dump_via_store_atomic(store):
    a = store.create("原子写智能体", "票 01")
    meta = store._dir_of(a.id) / "meta.yaml"
    assert yaml.safe_load(meta.read_text(encoding="utf-8"))["name"] == "原子写智能体"
    b = store.patch(a.id, name="改名了")
    assert b.name == "改名了"
    assert sorted(p.name for p in meta.parent.iterdir()) == ["meta.yaml"]  # 无 tmp


def test_datasource_upload_atomic(store, fixture_db):
    a = store.create("上传智能体")
    with open(fixture_db, "rb") as f:
        blob = f.read()
    dest = store.store_datasource(a.id, blob, "school.sqlite")
    assert dest.read_bytes() == blob
    dest2 = store.store_datasource(a.id, b"\x00fake", "other.db")  # 换库＝覆盖
    assert dest2 == dest and dest.read_bytes() == b"\x00fake"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["meta.yaml", "source.sqlite"]


def test_session_save_roundtrip_atomic(store):
    a = store.create("会话智能体")
    sessions = SessionStore(store)
    sid = "aabbccddeeff"
    s = sessions.load(a.id, sid)  # 懒建档
    res = QueryResult(columns=["n"], rows=[(1,)], row_count=1, truncated=False, elapsed_ms=5)
    s = append_turn(s, "有多少新生", res=res, failed=False, payload={"sql": "SELECT 1"})
    sessions.save(a.id, s)
    f = store._dir_of(a.id) / "sessions" / f"{sid}.yaml"
    assert yaml.safe_load(f.read_text(encoding="utf-8"))["id"] == sid
    again = sessions.load(a.id, sid)
    assert len(again.turns) == 1 and again.turns[0]["question"] == "有多少新生"
    # 会话目录里只许有正式档——.tmp 不收口进 list 也绝不留尸
    assert [p.name for p in f.parent.iterdir()] == [f"{sid}.yaml"]
