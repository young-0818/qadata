"""M7-rev2 票 02.5：智能体存储层测试——tmp 目录内完成，不碰网、不建数据库连接。

存储形态：data/agents/<uuid12>/{meta.yaml, source.sqlite}。
（M5 引用态 metrics_ref 族测试已随指标层退役删除，ADR-0007。）
"""
import pytest

from qadata.web.agents import MAX_PRESET_QUESTIONS, AgentStore, AgentStoreError


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


# ── 创建／列表／删除（真空启动：初始为空）──────────────────────────


def test_fresh_store_is_empty(store):
    assert store.all() == []


def test_create_list_get_roundtrip(store, tmp_path):
    a = store.create("金融分析师", "擅长银行业务问数")
    assert store.all() == [a]
    got = store.get(a.id)
    assert got.name == "金融分析师" and got.description == "擅长银行业务问数"
    assert (tmp_path / "agents" / a.id / "meta.yaml").is_file()  # 落盘目录制
    assert a.id and len(a.id) == 12  # 短 uuid 键，名称可重复、展示与键分离


def test_names_may_duplicate(store):
    a = store.create("同名", "")
    b = store.create("同名", "")
    assert a.id != b.id and len(store.all()) == 2


def test_create_validates_name(store):
    for bad in ("", "   ", "名" * 60):
        with pytest.raises(AgentStoreError):
            store.create(bad, "")


def test_delete_removes_directory(store, tmp_path):
    a = store.create("x", "")
    (tmp_path / "agents" / a.id / "source.sqlite").write_bytes(b"")
    store.delete(a.id)
    assert store.all() == []
    assert not (tmp_path / "agents" / a.id).exists()  # 删智能体＝删目录，无悬挂


def test_get_unknown_raises(store):
    with pytest.raises(AgentStoreError):
        store.get("deadbeef0000")


# ── 基本信息／业务知识 patch ────────────────────────────────────────


def test_patch_basic_fields(store):
    a = store.create("旧名", "旧描述")
    b = store.patch(a.id, name="新名", description="")
    assert b.name == "新名" and b.description == ""
    assert store.get(a.id).name == "新名"  # 持久化


def test_patch_evidence_manual(store):
    a = store.create("x", "")
    b = store.patch(a.id, evidence="总金额 = sum(loan.amount)")
    assert b.evidence == "总金额 = sum(loan.amount)"
    assert store.get(b.id).evidence == "总金额 = sum(loan.amount)"


# ── 预设问题 ────────────────────────────────────────────────────────


def test_preset_questions_cap_and_content(store):
    a = store.create("x", "")
    qs = [f"问题{i}" for i in range(MAX_PRESET_QUESTIONS)]
    b = store.patch(a.id, preset_questions=qs)
    assert list(b.preset_questions) == qs
    with pytest.raises(AgentStoreError):
        store.patch(a.id, preset_questions=qs + ["超了"])
    with pytest.raises(AgentStoreError):
        store.patch(a.id, preset_questions=["好", "  "])  # 空条目拒


# ── 数据源：上传唯一路、独占、覆盖 ─────────────────────────────────


def test_datasource_lifecycle(store, tmp_path):
    a = store.create("x", "")
    assert store.datasource_path(a) is None  # 未配置如实 None，不假装
    p = store.store_datasource(a.id, b"SQLITE-BYTES", "我的库.sqlite")
    assert p == (tmp_path / "agents" / a.id / "source.sqlite")  # 固定名，原始名不入文件系统
    assert p.read_bytes() == b"SQLITE-BYTES"
    assert store.datasource_path(store.get(a.id)) == p
    q = store.store_datasource(a.id, b"NEW", "换一只库.db")
    assert q == p and p.read_bytes() == b"NEW"  # 换库＝覆盖，无第二文件


def test_datasource_rejects_non_sqlite_and_empty(store):
    a = store.create("x", "")
    with pytest.raises(AgentStoreError):
        store.store_datasource(a.id, b"x", "notes.txt")
    with pytest.raises(AgentStoreError):
        store.store_datasource(a.id, b"", "ok.sqlite")


def test_legacy_metrics_ref_key_ignored_on_load(store, tmp_path):
    """退役键先例（M7 overlay/fresh_topic 同款）：旧 meta.yaml 残留 metrics_ref＝
    读取忽略、无第二真源复活通道；回写档不再含该键。"""
    a = store.create("x", "")
    f = tmp_path / "agents" / a.id / "meta.yaml"
    f.write_text(f.read_text(encoding="utf-8") + "metrics_ref: financial\n", encoding="utf-8")
    assert store.get(a.id).evidence == ""
    store.patch(a.id, description="改写触发回写")
    assert "metrics_ref" not in f.read_text(encoding="utf-8")


def test_corrupted_meta_raises_not_silently_swallowed(store, tmp_path):
    a = store.create("x", "")
    (tmp_path / "agents" / a.id / "meta.yaml").write_text("- 就是\n- 不是映射\n", encoding="utf-8")
    with pytest.raises(AgentStoreError):  # 坏目录不装没看见（不带病运行纪律）
        store.all()
