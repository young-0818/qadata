"""M7-rev2 票 02.5：智能体存储层测试——tmp 目录内完成，不碰网、不建数据库连接。

存储形态：data/agents/<uuid12>/{meta.yaml, source.sqlite}；
业务知识双态（手动 evidence ／ metrics_ref 读取期派生＝真单一来源）。
"""
import pytest

from qadata.web.agents import MAX_PRESET_QUESTIONS, AgentStore, AgentStoreError
from tests.web_shared import ONE_METRIC_YAML


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


@pytest.fixture
def with_registry(tmp_path):
    mdir = tmp_path / "metrics"
    mdir.mkdir(exist_ok=True)
    (mdir / "financial.yaml").write_text(ONE_METRIC_YAML, encoding="utf-8")
    return AgentStore(tmp_path / "agents", metrics_dir=mdir)


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
    assert store.effective_evidence(b) == "总金额 = sum(loan.amount)"


# ── 业务知识引用态：读取期派生＝真单一来源 ─────────────────────────


def test_metrics_ref_derives_at_read_time(with_registry, tmp_path):
    a = with_registry.create("金融分析师", "")
    a = with_registry.patch(a.id, metrics_ref="financial")
    ev = with_registry.effective_evidence(a)
    assert "演示摘录，非第二真源" in ev and "贷款笔数" in ev
    # 注册表改→口径随动（不是抄写快照）；路径经 fixture 已知布局，不穿存储私有方法
    (tmp_path / "metrics" / "financial.yaml").write_text(
        ONE_METRIC_YAML.replace("一笔合同计 1", "一笔合同计 1（修订版）"), encoding="utf-8")
    assert "修订版" in with_registry.effective_evidence(with_registry.get(a.id))


def test_dual_write_rejected_while_ref_active(with_registry):
    a = with_registry.patch(with_registry.create("x", "").id, metrics_ref="financial")
    with pytest.raises(AgentStoreError, match="双写"):
        with_registry.patch(a.id, evidence="自己抄一份")
    assert with_registry.get(a.id).evidence == ""  # 拒了就是没写进去


def test_setting_ref_rejects_nonexistent_registry_and_manual_evidence(with_registry):
    a = with_registry.create("x", "")
    with pytest.raises(AgentStoreError):
        with_registry.patch(a.id, metrics_ref="nope")
    with pytest.raises(AgentStoreError, match="双写"):
        with_registry.patch(a.id, metrics_ref="financial", evidence="同时抄")


def test_ref_and_manual_evidence_mutually_exclusive(with_registry):
    """引用态与手动文本不共存：先清一头才能换另一头（双写护栏的形态迁移）。"""
    a = with_registry.patch(with_registry.create("x", "").id, evidence="手动口径")
    with pytest.raises(AgentStoreError, match="双写"):
        with_registry.patch(a.id, metrics_ref="financial")  # 手动文本还在
    b = with_registry.patch(a.id, evidence="")  # 先清
    b = with_registry.patch(b.id, metrics_ref="financial")
    assert b.evidence == "" and b.metrics_ref == "financial"
    c = with_registry.patch(b.id, metrics_ref="")  # 解除引用
    c = with_registry.patch(c.id, evidence="回到手动")
    assert with_registry.effective_evidence(c) == "回到手动"


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


def test_registries_listing(with_registry):
    assert with_registry.registries() == ["financial"]


def test_corrupted_meta_raises_not_silently_swallowed(store, tmp_path):
    a = store.create("x", "")
    (tmp_path / "agents" / a.id / "meta.yaml").write_text("- 就是\n- 不是映射\n", encoding="utf-8")
    with pytest.raises(AgentStoreError):  # 坏目录不装没看见（带病运行违 load_registry 纪律）
        store.all()
