"""M7 票 01/02 契约测试：TestClient＋ScriptedLLM——不碰网、不碰真实数据目录（侦察笔记）。

契约形状由本文件钉死（spec「响应契约」节：/api/ask 12 字段改形状＝跨票改卷；
/api/dbs 自票 02 起为 name/evidence/source 条目）。
happy path 恰好 3 次 LLM 调用（understand/generate/respond），纪律⑤ calls 断言照旧。
"""
import ast
import inspect
import types

from fastapi.testclient import TestClient

from qadata.config import Settings
from qadata.types import Answer
from qadata.web import app as web_app
from qadata.web import dbs as web_dbs
from qadata.web import serve as web_serve
from qadata.web.app import create_app
from qadata.web.dbs import DbEntry, discover_dbs
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="", retry_budget=3)

# 契约字段全集（票 01 冻结）——多一个少一个都算改卷
_CONTRACT_KEYS = {
    "conclusion", "sql", "columns", "rows", "truncated", "elapsed_ms",
    "failed", "error_summary", "path", "metric_name", "template_fell_back",
    "session_id",
}

_HAPPY_SCRIPT = ["改写", "SELECT name FROM students WHERE id = 2", "Bob 的数学 88 分"]


def _client(llm, dbs, **kw) -> TestClient:
    """dbs 传裸路径＝单库 {"school": path}；传 dict 原样入工厂（票 02 多库测试用）。"""
    # static_dir 默认指向不存在目录：API 测试与本机真实 web/dist 构建产物隔离
    kw.setdefault("static_dir", "__no_such_dist_for_tests__")
    if not isinstance(dbs, dict):
        dbs = {"school": dbs}
    return TestClient(create_app(llm=llm, settings=_S, dbs=dbs, **kw))


# ── /api/ask：成功路径 ──────────────────────────────────────────────


def test_ask_success_contract(fixture_db):
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, fixture_db).post("/api/ask",
                                        json={"db": "school", "question": "Bob 成绩如何"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == _CONTRACT_KEYS  # 形状即契约，不扩不缩
    assert body["failed"] is False
    assert body["conclusion"].startswith("【结论】")  # 票 07 三节文本原样透出
    assert body["sql"] == "SELECT name FROM students WHERE id = 2"
    assert body["columns"] == ["name"]
    assert body["rows"] == [["Bob"]]  # 元组 → JSON 数组
    assert body["truncated"] is False
    assert isinstance(body["elapsed_ms"], int)
    assert body["error_summary"] is None
    assert body["path"] == "fallback"  # 指标层默认关
    assert body["metric_name"] is None
    assert body["template_fell_back"] is False
    assert body["session_id"] is None  # 票 01 恒 null（多轮在票 04）
    assert llm.calls == 3  # understand + generate + respond


def test_ask_evidence_passthrough(fixture_db, monkeypatch):
    """唯一入口不破：/api/ask 只经 run_question（沙箱四层无旁路），注册表解析路径＋evidence 透传。"""
    seen = {}

    def fake_run(db_path, question, evidence="", **kw):
        seen.update(db_path=db_path, question=question, evidence=evidence, kw=kw)
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    client = _client(ScriptedLLM([]), fixture_db)
    res = client.post("/api/ask",
                      json={"db": "school", "question": "题", "evidence": "口径"})
    assert res.status_code == 200
    assert seen["db_path"] == fixture_db
    assert seen["question"] == "题" and seen["evidence"] == "口径"
    assert seen["kw"]["llm"] is not None and seen["kw"]["settings"] is _S


# ── /api/ask：失败路径与拒绝语义 ────────────────────────────────────


def test_ask_failure_path_has_error_summary(fixture_db):
    """预算耗尽：HTTP 200（失败是领域结果非传输错误），failed＋error_summary 在场，行列全 null。"""
    llm = ScriptedLLM(["改写"] + ["SELECT nope FROM students"] * 3)
    res = _client(llm, fixture_db).post("/api/ask",
                                        json={"db": "school", "question": "谁成绩最好"})
    assert res.status_code == 200
    body = res.json()
    assert body["failed"] is True
    assert body["error_summary"]  # last_error 透传（"no such column" 形态）
    assert "no such column" in body["conclusion"]
    for k in ("columns", "rows", "truncated", "elapsed_ms"):
        assert body[k] is None  # 无结果集时如实 null，不编造空表
    assert llm.calls == 4  # understand + 3 次 generate（respond 失败路径不调 LLM）


def test_ask_unknown_db_404(fixture_db):
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, fixture_db).post("/api/ask",
                                        json={"db": "nope", "question": "题"})
    assert res.status_code == 404
    assert llm.calls == 0  # 注册表外拒在调模型之前


def test_ask_empty_question_422(fixture_db):
    res = _client(ScriptedLLM([]), fixture_db).post(
        "/api/ask", json={"db": "school", "question": ""})
    assert res.status_code == 422
    res = _client(ScriptedLLM([]), fixture_db).post("/api/ask", json={"db": "school"})
    assert res.status_code == 422


# ── /api/dbs（票 02 起条目含默认口径与来源轨）──────────────────────


def test_list_dbs(fixture_db):
    res = _client(None, fixture_db).get("/api/dbs")
    assert res.status_code == 200
    assert res.json() == {"dbs": [{"name": "school", "evidence": "", "source": "preset"}]}


def test_list_dbs_exposes_default_evidence(fixture_db):
    entry = DbEntry(name="loan", path=fixture_db, evidence="口径摘录…非第二真源")
    res = _client(None, {"loan": entry}).get("/api/dbs")
    assert res.json()["dbs"] == [{"name": "loan", "evidence": "口径摘录…非第二真源",
                                  "source": "preset"}]


# ── 票 02：默认口径应用（请求显式 > 库默认 > 空）────────────────────


def test_ask_uses_db_default_evidence_when_empty(fixture_db, monkeypatch):
    seen = {}

    def fake_run(db_path, question, evidence="", **kw):
        seen["evidence"] = evidence
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    entry = DbEntry(name="school", path=fixture_db, evidence="库默认口径")
    client = _client(ScriptedLLM([]), {"school": entry})
    client.post("/api/ask", json={"db": "school", "question": "题"})
    assert seen["evidence"] == "库默认口径"
    client.post("/api/ask", json={"db": "school", "question": "题", "evidence": "会话叠加"})
    assert seen["evidence"] == "会话叠加"  # 显式输入优先，不被默认吞掉


# ── 票 02：B 轨导入（上传＋路径直连）───────────────────────────────


def _import_client(fixture_db, tmp_path, llm=None, **kw) -> TestClient:
    return _client(llm or ScriptedLLM([]), fixture_db,
                   import_dir=tmp_path / "imports", **kw)


def test_upload_imports_and_asks_by_alias(fixture_db, tmp_path):
    """上传 .sqlite → 进列表（source=import、evidence 空）→ 同一 app 内按别名问答成功。"""
    with open(fixture_db, "rb") as f:
        data = f.read()
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    client = _import_client(fixture_db, tmp_path, llm=llm)
    res = client.post("/api/dbs/upload?name=sales.sqlite&alias=我的销售库", content=data)
    assert res.status_code == 200
    assert res.json() == {"name": "我的销售库", "evidence": "", "source": "import"}
    assert (tmp_path / "imports" / "我的销售库.sqlite").is_file()  # 落盘 web_imports
    names = [d["name"] for d in client.get("/api/dbs").json()["dbs"]]
    assert "我的销售库" in names
    # 闭环：走上传端点写进内存注册表的那条目问答，不是另起炉灶手工注入路径
    res = client.post("/api/ask", json={"db": "我的销售库", "question": "Bob 成绩如何"})
    assert res.status_code == 200 and res.json()["failed"] is False
    assert res.json()["rows"] == [["Bob"]]
    assert llm.calls == 3  # 上传/列表零调用，问答恰好 3 次


def test_upload_rejects_fs_hostile_alias(fixture_db, tmp_path):
    """评审收紧：保留字符别名（会参与落盘命名）拒成诚实 400，不漂成 OSError 500。"""
    res = _import_client(fixture_db, tmp_path).post(
        "/api/dbs/upload?name=s.sqlite&alias=a:b", content=b"x")
    assert res.status_code == 400 and "别名" in res.json()["detail"]


def test_local_path_import_and_ask(fixture_db, tmp_path):
    """本地路径直连：登记不建连接、进列表，并在同一 app 内当场问出一题（owner 裁核心体验）。"""
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    client = _import_client(fixture_db, tmp_path, llm=llm)
    res = client.post("/api/dbs/local", json={"path": fixture_db, "alias": "别名库"})
    assert res.status_code == 200
    assert {"name": "别名库", "evidence": "", "source": "import"} in \
        client.get("/api/dbs").json()["dbs"]
    res = client.post("/api/ask", json={"db": "别名库", "question": "Bob 成绩如何"})
    assert res.status_code == 200 and res.json()["rows"] == [["Bob"]]
    assert llm.calls == 3


def test_local_path_alias_defaults_to_stem(fixture_db, tmp_path):
    """别名缺省取文件名主干；与既有库重名时 409 如实拒绝（不静默改名）。"""
    res = _client(ScriptedLLM([]), {}, import_dir=tmp_path / "imports") \
        .post("/api/dbs/local", json={"path": fixture_db})
    assert res.json()["name"] == "school"
    res = _client(ScriptedLLM([]), fixture_db, import_dir=tmp_path / "imports") \
        .post("/api/dbs/local", json={"path": fixture_db})
    assert res.status_code == 409  # school 已在册


def test_import_rejects_engine_and_url_forms(fixture_db, tmp_path):
    client = _import_client(fixture_db, tmp_path)
    res = client.post("/api/dbs/local", json={"path": "mysql://u:p@host/db"})
    assert res.status_code == 400 and "引擎串" in res.json()["detail"]
    res = client.post("/api/dbs/local", json={"path": "http://evil/x.sqlite"})
    assert res.status_code == 400
    res = client.post("/api/dbs/local", json={"path": str(tmp_path / "nope.sqlite")})
    assert res.status_code == 400  # 不存在的文件拒在登记之前


def test_upload_rejects_non_sqlite_and_duplicates(fixture_db, tmp_path):
    client = _import_client(fixture_db, tmp_path)
    res = client.post("/api/dbs/upload?name=notes.txt", content=b"x")
    assert res.status_code == 400
    with open(fixture_db, "rb") as f:
        data = f.read()
    assert client.post("/api/dbs/upload?name=s.sqlite", content=data).status_code == 200
    res = client.post("/api/dbs/upload?name=s.sqlite", content=data)
    assert res.status_code == 409  # 同名导入库已存在（宁缺勿错，不静默覆盖）
    res = client.post("/api/dbs/local", json={"path": fixture_db, "alias": "s"})
    assert res.status_code == 409  # 别名与既有库冲突同样拒绝


def test_upload_disabled_without_import_dir(fixture_db):
    res = _client(ScriptedLLM([]), fixture_db).post("/api/dbs/upload?name=x.sqlite",
                                                    content=b"x")
    assert res.status_code == 400 and "未开启上传" in res.json()["detail"]


# ── 票 02：唯一入口钉测（沙箱四层无旁路，导入路径同样受限）──────────


def test_web_package_never_imports_sqlite3():
    """AST 级 import 纪律（metrics 同款先例）：web 三包模块禁 sqlite3/引擎驱动——
    连接唯一入口是 tools/db.open_readonly，上传/直连只搬文件与登记。"""
    for mod in (web_app, web_dbs, web_serve):
        parsed = ast.parse(inspect.getsource(mod))
        imported: set[str] = set()
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module)
        offenders = {m for m in imported
                     if m.split(".")[0] in {"sqlite3", "sqlalchemy", "psycopg2", "pymysql"}}
        assert not offenders, f"{mod.__name__} 旁路连接层：{offenders}"


def test_import_phase_opens_no_connection(fixture_db, tmp_path, monkeypatch):
    """行为侧钉测：上传与直连登记全程零建连——sqlite3.connect 一旦出手即炸测试。"""
    def _boom(*a, **kw):
        raise AssertionError("导入阶段不得建数据库连接")

    monkeypatch.setattr("qadata.tools.db.sqlite3.connect", _boom)
    client = _import_client(fixture_db, tmp_path)
    with open(fixture_db, "rb") as f:
        data = f.read()
    assert client.post("/api/dbs/upload?name=s.sqlite", content=data).status_code == 200
    assert client.post("/api/dbs/local", json={"path": fixture_db, "alias": "b"}).status_code == 200


def test_import_path_still_flows_through_readonly_uri_escape(fixture_db, tmp_path):
    """端到端：文件名含空格/井号/中文的导入库，问答链路仍经 open_readonly＋as_uri
    转义打开（物理只读＋特殊字符安全），结果如实返回——导入不构成旁路。"""
    weird = tmp_path / "我的 库#1 (副本).sqlite"
    with open(fixture_db, "rb") as f:
        weird.write_bytes(f.read())
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, {"x": str(weird)}).post(
        "/api/ask", json={"db": "x", "question": "Bob 成绩如何"})
    assert res.status_code == 200
    assert res.json()["rows"] == [["Bob"]] and res.json()["failed"] is False
    assert llm.calls == 3


# ── 预置库自动发现（BIRD 风格目录，tmp 树验证，不碰真实 data/）──────


def test_discover_dbs(tmp_path):
    (tmp_path / "financial" / "sub").mkdir(parents=True)
    (tmp_path / "financial" / "financial.sqlite").write_bytes(b"")
    (tmp_path / "student_club").mkdir()
    (tmp_path / "student_club" / "student_club.sqlite").write_bytes(b"")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "other.sqlite").write_bytes(b"")  # 名不匹配→不收
    (tmp_path / "empty").mkdir()  # 无 sqlite→不收
    (tmp_path / "stray.sqlite").touch()  # 根下散文件→不收
    found = discover_dbs(tmp_path)
    assert found == {
        "financial": str(tmp_path / "financial" / "financial.sqlite"),
        "student_club": str(tmp_path / "student_club" / "student_club.sqlite"),
    }


def test_discover_dbs_missing_root(tmp_path):
    assert discover_dbs(tmp_path / "nope") == {}  # 缺目录诚实空列表，不炸


# ── serve 装配：依赖注入在包内收口（load_settings/build_llm/uvicorn 全替）──


def test_run_server_wires_dependencies(monkeypatch, tmp_path):
    import qadata.web.serve as serve_mod

    calls = {}
    fake_settings = types.SimpleNamespace(metrics_dir=str(tmp_path / "no_metrics"))
    monkeypatch.setattr(serve_mod, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(serve_mod, "build_llm", lambda s: f"LLM<{s}>")
    monkeypatch.setattr(serve_mod, "TraceLogger", lambda p: f"TRACER<{p}>")
    monkeypatch.setattr(serve_mod, "create_app",
                        lambda **kw: calls.update(kw) or "APP")
    monkeypatch.setattr(serve_mod, "uvicorn", types.SimpleNamespace(
        run=lambda app, **kw: calls.update(run=(app, kw))))
    root = tmp_path / "bird"
    (root / "financial").mkdir(parents=True)
    (root / "financial" / "financial.sqlite").write_bytes(b"")
    # registry/import_dir 显式指向 tmp——测试不吃仓库根真实 dbs.yaml/metrics/
    serve_mod.run_server(host="h", port=1, db_dir=str(root),
                         registry=str(tmp_path / "none.yaml"),
                         import_dir=str(tmp_path / "imports"))
    assert calls["llm"] == f"LLM<{fake_settings}>" and calls["settings"] is fake_settings
    assert calls["dbs"]["financial"].path == str(root / "financial" / "financial.sqlite")
    assert calls["tracer"] == "TRACER<runs/traces.jsonl>"
    assert calls["import_dir"] == str(tmp_path / "imports")
    assert calls["run"] == ("APP", {"host": "h", "port": 1})


def test_assemble_dbs_yaml_takes_over(tmp_path):
    """票 02：YAML 预置注册表接管目录发现——有 dbs.yaml 时不再扫 BIRD 目录。"""
    from qadata.web.serve import assemble_dbs

    mdir = tmp_path / "metrics"
    mdir.mkdir()
    (mdir / "loan.yaml").write_text(
        "metrics:\n"
        "  - name: loan_count\n    display_name: 贷款笔数\n"
        "    meaning: 批准的贷款合同数量\n"
        "    definition: 以 loan.date 计条；一笔合同计 1\n"
        "    sql_template: SELECT COUNT(*) FROM loan\n"
        "    aliases: [贷款合同数]\n    available_dimensions: {}\n"
        "    source_tables: [loan]\n", encoding="utf-8")
    reg = tmp_path / "dbs.yaml"
    reg.write_text("databases:\n  loan:\n    path: dbs/loan.sqlite\n", encoding="utf-8")
    # db_dir 存在但不该被看
    bird = tmp_path / "bird" / "ignored"
    bird.mkdir(parents=True)
    (bird / "ignored.sqlite").write_bytes(b"")
    dbs = assemble_dbs(reg, db_dir=str(tmp_path / "bird"),
                       import_dir=tmp_path / "imports", metrics_dir=str(mdir))
    assert set(dbs) == {"loan"}
    assert "非第二真源" in dbs["loan"].evidence  # 口径派生自指标注册表
    assert dbs["loan"].path == str((tmp_path / "dbs" / "loan.sqlite").resolve())


def test_assemble_dbs_merges_web_imports_and_name_wins_preset(tmp_path):
    """重启恢复：web_imports 平铺扫描并轨（source=import）；撞名预置轨优先、如实跳过。"""
    from qadata.web.serve import assemble_dbs

    reg = tmp_path / "dbs.yaml"
    reg.write_text("databases:\n  school:\n    path: dbs/school.sqlite\n", encoding="utf-8")
    imp = tmp_path / "imports"
    imp.mkdir()
    (imp / "school.sqlite").write_bytes(b"")  # 与预置撞名
    (imp / "sales.db").write_bytes(b"")
    dbs = assemble_dbs(reg, db_dir=None, import_dir=imp,
                       metrics_dir=str(tmp_path / "no_metrics"))
    assert set(dbs) == {"school", "sales"}
    assert dbs["sales"].source == "import"
    # 撞名时预置轨占名（导入侧 school 被跳过，不静默改名）
    assert dbs["school"].path == str((tmp_path / "dbs" / "school.sqlite").resolve())


# ── 静态服务：构建产物同源；未构建时诚实占位页 ──────────────────────


def test_placeholder_when_not_built(tmp_path, fixture_db):
    client = _client(None, fixture_db, static_dir=tmp_path / "dist")
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "npm run build" in res.text  # 占位页写明构建命令，别白屏


def test_serves_built_index(tmp_path, fixture_db):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>built-app</html>", encoding="utf-8")
    (dist / "assets" / "x.js").write_text("//js", encoding="utf-8")
    client = _client(None, fixture_db, static_dir=dist)
    assert "built-app" in client.get("/").text
    assert "//js" in client.get("/assets/x.js").text
    assert client.get("/api/dbs").status_code == 200  # 挂载不吃 API 路由
