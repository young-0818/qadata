"""M7 票 02 双轨连库——注册表纯函数层测试：不碰网、不碰真实 data/、不建连接。

覆盖：A 轨 YAML 预置注册表（含口径单一来源护栏）；B 轨导入输入面
（引擎串/远程 URL 拒绝、扩展名白名单、别名消毒、web_imports 重扫）。
"""
import pytest

from qadata.web.dbs import (
    DbEntry,
    DbRegistryError,
    discover_imports,
    load_preset_registry,
    metric_evidence_text,
    sanitize_alias,
    store_upload,
    validate_import_path,
)

# 一条合法的最小指标注册表（六要素齐，time_slot 与模板占位符对账通过）
_ONE_METRIC_YAML = """\
metrics:
  - name: loan_count
    display_name: 贷款笔数
    meaning: 统计期内银行批准的贷款合同数量
    definition: |-
      以贷款批准日期（loan.date）落入时间范围计条
      一笔合同计 1
    sql_template: SELECT COUNT(loan.loan_id) FROM loan WHERE loan.date BETWEEN {time_start} AND {time_end}
    aliases: [贷款合同数, loan count]
    time_slot: {column: loan.date, date_format: YYYY-MM-DD}
    available_dimensions: {}
    source_tables: [loan]
"""


def _write_registry(tmp_path, body: str):
    p = tmp_path / "dbs.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ── A 轨：YAML 预置注册表 ───────────────────────────────────────────


def test_load_preset_registry_basic(tmp_path):
    p = _write_registry(tmp_path, """\
databases:
  school:
    path: dbs/school.sqlite
    evidence: 成绩 = scores.score
  other:
    path: dbs/other.sqlite
""")
    dbs = load_preset_registry(p)
    # 路径相对 YAML 所在目录解析（serve 从仓库根起，dbs.yaml 亦在仓库根）
    assert dbs["school"].path == str((tmp_path / "dbs" / "school.sqlite").resolve())
    assert dbs["school"].evidence == "成绩 = scores.score"
    assert dbs["school"].source == "preset"
    assert dbs["other"].evidence == ""  # 缺省诚实空，不编造


def test_load_preset_registry_missing_file(tmp_path):
    with pytest.raises(DbRegistryError):
        load_preset_registry(tmp_path / "nope.yaml")


@pytest.mark.parametrize("body", [
    "databases: []",                     # 形状错：不是映射
    "databases:\n  school: {}\n",        # 缺 path
    "databases:\n  school:\n    path: ''\n",  # 空 path
    "other:\n  x: 1\n",                  # 缺 databases 顶层键
])
def test_load_preset_registry_bad_shape_rejected(tmp_path, body):
    """带病注册表拒绝加载（load_registry 同款纪律，不静默跳过）。"""
    with pytest.raises(DbRegistryError):
        load_preset_registry(_write_registry(tmp_path, body))


def test_metric_registry_derives_evidence_single_source(tmp_path):
    """有指标注册表的库：口径由代码从 metrics/<db>.yaml 确定性派生，YAML 不写 evidence。"""
    mdir = tmp_path / "metrics"
    mdir.mkdir()
    (mdir / "loan.yaml").write_text(_ONE_METRIC_YAML, encoding="utf-8")
    p = _write_registry(tmp_path, "databases:\n  loan:\n    path: dbs/loan.sqlite\n")
    dbs = load_preset_registry(p, metrics_dir=str(mdir))
    ev = dbs["loan"].evidence
    assert "演示摘录，非第二真源" in ev          # 抄写标注（票面原话）
    assert "贷款笔数" in ev and "一笔合同计 1" in ev
    assert "metrics/loan.yaml" in ev            # 指回真源


def test_dual_write_evidence_rejected(tmp_path):
    """禁双写漂移：同库已有指标注册表时，YAML 再写 evidence＝第二真源，加载期拒绝。"""
    mdir = tmp_path / "metrics"
    mdir.mkdir()
    (mdir / "loan.yaml").write_text(_ONE_METRIC_YAML, encoding="utf-8")
    p = _write_registry(tmp_path, (
        "databases:\n  loan:\n    path: dbs/loan.sqlite\n    evidence: 自己抄一份\n"
    ))
    with pytest.raises(DbRegistryError, match="双写"):
        load_preset_registry(p, metrics_dir=str(mdir))


def test_metric_evidence_text_flattens_multiline_definition(tmp_path):
    """definition 含换行（YAML 字面块）时拍平为单行，行内以全角分号承接。"""
    mdir = tmp_path / "metrics"
    mdir.mkdir()
    (mdir / "loan.yaml").write_text(_ONE_METRIC_YAML, encoding="utf-8")
    from qadata.graph.metrics import load_registry

    metrics = load_registry(mdir / "loan.yaml")
    assert "\n" in metrics[0].definition  # fixture 自证：源真是多行
    text = metric_evidence_text("loan", metrics)
    body_lines = [l for l in text.splitlines() if l.startswith("- ")]
    assert len(body_lines) == 1  # 1 指标 1 行
    assert "计条；一笔合同计 1" in body_lines[0]  # 换行已被分号承接


# ── B 轨：导入输入面 ────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    "mysql://user:pw@host:3306/db",       # 非 sqlite 引擎串
    "postgresql+psycopg2://localhost/x",
    "sqlite:///data/x.sqlite",            # 也拒：URI 形态不进输入面（只收本地路径）
    "file:///data/x.sqlite?mode=ro",
    "http://example.com/x.sqlite",        # 远程 URL
    "ftp://h/x.db",
])
def test_validate_import_path_rejects_engine_and_url_forms(raw):
    """票面红线：绝不接受非 sqlite 引擎串/远程 URL 形态的输入面扩张。"""
    with pytest.raises(DbRegistryError):
        validate_import_path(raw)


def test_validate_import_path_requires_existing_sqlite(tmp_path):
    with pytest.raises(DbRegistryError):
        validate_import_path(str(tmp_path / "nope.sqlite"))  # 不存在
    txt = tmp_path / "x.txt"
    txt.write_text("not a db", encoding="utf-8")
    with pytest.raises(DbRegistryError):
        validate_import_path(str(txt))  # 扩展名不在白名单
    good = tmp_path / "我的 库#1.sqlite"  # 空格/井号/中文是合法本地路径字符
    good.write_bytes(b"")
    assert validate_import_path(str(good)) == str(good.resolve())


def test_validate_import_path_accepts_forward_slash_drive_path(tmp_path):
    """评审收紧：单字母盘符＋正斜杠（前端占位符示例 D:/…形态）不得误杀。"""
    good = tmp_path / "my.db"
    good.write_bytes(b"")
    forward = str(good).replace("\\", "/")
    assert ":/" in forward  # 形态自证：这正是旧一刀切 ':/' 会拒的输入
    assert validate_import_path(forward) == str(good.resolve())


@pytest.mark.parametrize("raw", ["", "  ", "a/b", "..\\evil", "x/../y", "a\\b", "." * 3,
                                 "a:b", "x*y", "q?z", "a<b", 'a"b', "a|b"])
def test_sanitize_alias_rejects_unusable_forms(raw):
    """路径分隔符/'..'/文件系统保留字符全拒——上传落盘按别名命名，漏网＝OSError 变 500。"""
    with pytest.raises(DbRegistryError):
        sanitize_alias(raw)


def test_sanitize_alias_accepts_plain_and_strips():
    assert sanitize_alias(" 我的库 ") == "我的库"


def test_store_upload_writes_under_import_dir(tmp_path):
    dest = store_upload(tmp_path / "imports", b"BYTES", "sales.sqlite3")
    assert dest.is_file() and dest.read_bytes() == b"BYTES"
    assert dest.parent == (tmp_path / "imports").resolve()


def test_store_upload_rejects_bad_name_and_traversal(tmp_path):
    with pytest.raises(DbRegistryError):
        store_upload(tmp_path / "i", b"", "notes.txt")   # 非 sqlite 后缀
    with pytest.raises(DbRegistryError):
        store_upload(tmp_path / "i", b"", r"..\..\evil.sqlite")  # 带目录分隔符→宁缺勿错直接拒
    with pytest.raises(DbRegistryError):
        store_upload(tmp_path / "i", b"", "sqlite")      # 无扩展名


def test_store_upload_rejects_empty_body(tmp_path):
    with pytest.raises(DbRegistryError):
        store_upload(tmp_path / "i", b"", "ok.sqlite")


def test_discover_imports_scans_flat_dir(tmp_path):
    (tmp_path / "sales.sqlite").write_bytes(b"")
    (tmp_path / "people.db").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    found = discover_imports(tmp_path)
    assert set(found) == {"sales", "people"}
    assert found["sales"].source == "import"
    assert found["sales"].evidence == ""  # 导入库口径默认空，会话叠加框补位（票 04）
    assert found["people"].path == str(tmp_path / "people.db")


def test_discover_imports_missing_dir_is_empty(tmp_path):
    assert discover_imports(tmp_path / "nope") == {}


def test_db_entry_is_immutable_snapshot():
    e = DbEntry(name="a", path="p")
    assert e.evidence == "" and e.source == "preset"
