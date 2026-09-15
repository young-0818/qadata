"""M7-rev2 票 02.5 契约测试：TestClient＋ScriptedLLM＋tmp AgentStore——不碰网、不碰真实目录。

/api/ask 响应 12 字段冻结不动＋票 04 新增可选 chart＋M8 票 03 新增可选 clarification
（共 14，spec 响应契约：改形状＝跨票改卷，本票修订段见 spec）；智能体面
CRUD／数据源上传／业务知识双态／模型只读卡由本文件钉死。
happy path 恰好 3 次 LLM 调用（understand/generate/respond），纪律⑤ calls 断言照旧。
"""
import ast
import inspect
import json
import types
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from qadata.config import Settings
from qadata.types import Answer
from qadata.web import agents as web_agents
from qadata.web import app as web_app
from qadata.web import charts as web_charts
from qadata.web import serve as web_serve
from qadata.web import sessions as web_sessions
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from tests.fakes import ScriptedLLM
from tests.web_shared import ONE_METRIC_YAML

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_S_CLAR = replace(_S, clarification=True)  # M8 票 03 开态（web 演示场景＝开闸面）

# 契约字段全集（票 01 冻结 12＋票 04 新增可选 chart＋票 03 新增可选 clarification）
# ——多一个少一个都算改卷
_CONTRACT_KEYS = {
    "conclusion", "sql", "columns", "rows", "truncated", "elapsed_ms",
    "failed", "error_summary", "path", "metric_name", "template_fell_back",
    "session_id", "chart", "clarification",
}

_HAPPY_SCRIPT = ["改写", "SELECT name FROM students WHERE id = 2", "Bob 的数学 88 分"]


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


@pytest.fixture
def store_with_registry(tmp_path):
    mdir = tmp_path / "metrics"
    mdir.mkdir()
    (mdir / "financial.yaml").write_text(ONE_METRIC_YAML, encoding="utf-8")
    return AgentStore(tmp_path / "agents", metrics_dir=mdir)


def _client(llm, store, **kw) -> TestClient:
    # static_dir 默认指向不存在目录：API 测试与本机真实 web/dist 构建产物隔离
    kw.setdefault("static_dir", "__no_such_dist_for_tests__")
    return TestClient(create_app(llm=llm, settings=_S, agents=store, **kw))


def _agent_with_datasource(store, fixture_db, **patch):
    a = store.create("试验智能体", "契约测试用")
    with open(fixture_db, "rb") as f:
        store.store_datasource(a.id, f.read(), "school.sqlite")
    if patch:
        a = store.patch(a.id, **patch)
    return a


# ── 真空启动／模型只读卡／注册表清单 ────────────────────────────────


def test_vacuum_start_empty_list(store):
    res = _client(None, store).get("/api/agents")
    assert res.status_code == 200
    assert res.json() == {"agents": []}  # 零预置：首页初见即空


def test_model_card_is_readonly(store):
    res = _client(None, store).get("/api/model")
    body = res.json()
    assert body["model"] == "test-model" and body["writable"] is False
    assert "QADATA_MODEL" in body["source"]  # 真源指回 .env


def test_metrics_registries_listing(store_with_registry):
    res = _client(None, store_with_registry).get("/api/metrics-registries")
    assert res.json() == {"registries": ["financial"]}


# ── 智能体 CRUD ─────────────────────────────────────────────────────


def test_agent_crud_lifecycle(store):
    client = _client(None, store)
    res = client.post("/api/agents", json={"name": "金融分析师", "description": "银行业务"})
    assert res.status_code == 200
    a = res.json()
    assert a["name"] == "金融分析师" and a["datasource"] == {"has_file": False,
                                                             "table_count": None, "error": None}
    assert client.get("/api/agents").json()["agents"] == [
        {"id": a["id"], "name": "金融分析师", "description": "银行业务", "has_datasource": False}]
    res = client.patch(f"/api/agents/{a['id']}", json={"preset_questions": ["谁欠款了"]})
    assert res.json()["preset_questions"] == ["谁欠款了"]  # 只动传入字段
    assert res.json()["name"] == "金融分析师"  # 未传字段原样
    assert client.delete(f"/api/agents/{a['id']}").json() == {"ok": True}
    assert client.get(f"/api/agents/{a['id']}").status_code == 404  # 删干净了
    assert client.get("/api/agents").json() == {"agents": []}


def test_agent_create_validations(store):
    client = _client(None, store)
    assert client.post("/api/agents", json={"name": "  "}).status_code == 400
    too_many = [f"问{i}" for i in range(11)]
    a = client.post("/api/agents", json={"name": "x"}).json()
    res = client.patch(f"/api/agents/{a['id']}", json={"preset_questions": too_many})
    assert res.status_code == 400 and "10" in res.json()["detail"]
    assert client.patch("/api/agents/deadbeef0000", json={"name": "y"}).status_code == 404
    assert client.get("/api/agents/not-an-id").status_code == 404  # 非法 id 也是查无


# ── 数据源：上传唯一路＋覆盖＋只读摘要 ─────────────────────────────


def test_datasource_upload_and_schema_summary(store, fixture_db, tmp_path):
    client = _client(None, store)
    a = client.post("/api/agents", json={"name": "x"}).json()
    with open(fixture_db, "rb") as f:
        res = client.post(f"/api/agents/{a['id']}/datasource?name=我的库.sqlite",
                          content=f.read())
    assert res.status_code == 200 and res.json() == {"ok": True}
    body = client.get(f"/api/agents/{a['id']}").json()
    assert body["datasource"]["has_file"] is True
    assert body["datasource"]["table_count"] == 2  # students＋scores
    assert [d["name"] for d in client.get("/api/agents").json()["agents"]] == ["x"]
    # 落盘固定名：原始文件名不进文件系统（恶意名/穿越结构性消失）
    assert (tmp_path / "agents" / a["id"] / "source.sqlite").is_file()


def test_datasource_rejects_and_unknown_agent(store, fixture_db):
    client = _client(None, store)
    a = client.post("/api/agents", json={"name": "x"}).json()
    assert client.post(f"/api/agents/{a['id']}/datasource?name=note.txt",
                       content=b"x").status_code == 400
    assert client.post(f"/api/agents/{a['id']}/datasource?name=ok.sqlite",
                       content=b"").status_code == 400
    with open(fixture_db, "rb") as f:
        assert client.post("/api/agents/deadbeef0000/datasource?name=ok.sqlite",
                           content=f.read()).status_code == 404


def test_upload_opens_no_connection(store, fixture_db, monkeypatch):
    """钉测：上传全程零建连——sqlite3.connect 一旦出手即炸测试（库可打开与否
    交给只读摘要/问答链路经 open_readonly 如实回答，不在导入阶段旁路）。"""
    def _boom(*a, **kw):
        raise AssertionError("上传阶段不得建数据库连接")

    monkeypatch.setattr("qadata.tools.db.sqlite3.connect", _boom)
    client = _client(None, store)
    a = client.post("/api/agents", json={"name": "x"}).json()
    with open(fixture_db, "rb") as f:
        res = client.post(f"/api/agents/{a['id']}/datasource?name=s.sqlite", content=f.read())
    assert res.status_code == 200 and res.json() == {"ok": True}  # 纯文件搬运，零建连


def test_schema_summary_flows_through_open_readonly(store, fixture_db, monkeypatch, tmp_path):
    """钉测：schema 摘要的打开也走唯一入口 open_readonly（记录调用参数）。"""
    seen = {}
    real = web_app.open_readonly

    def spy(path):
        seen["path"] = path
        return real(path)

    monkeypatch.setattr("qadata.web.app.open_readonly", spy)
    a = _agent_with_datasource(store, fixture_db)
    body = _client(None, store).get(f"/api/agents/{a.id}").json()
    assert body["datasource"]["table_count"] == 2
    assert seen["path"] == str(tmp_path / "agents" / a.id / "source.sqlite")


def test_broken_datasource_reports_error_not_lies(store, tmp_path):
    client = _client(None, store)
    a = client.post("/api/agents", json={"name": "x"}).json()
    client.post(f"/api/agents/{a['id']}/datasource?name=s.sqlite",
                content=b"NOT A SQLITE FILE AT ALL")  # 字节字面量只能 ASCII，形态照样非库
    body = client.get(f"/api/agents/{a['id']}").json()
    assert body["datasource"]["has_file"] is True
    assert body["datasource"]["table_count"] is None
    assert body["datasource"]["error"]  # 打不开如实上报，不装库正常


# ── 业务知识：手动态／引用态（读取期派生）＋双写拒绝 ───────────────


def test_business_knowledge_manual_and_derived(store_with_registry):
    client = _client(None, store_with_registry)
    a = client.post("/api/agents", json={"name": "金融分析师"}).json()
    b = client.patch(f"/api/agents/{a['id']}", json={"metrics_ref": "financial"}).json()
    assert "演示摘录，非第二真源" in b["business_knowledge"] and "贷款笔数" in b["business_knowledge"]
    assert b["business_knowledge_error"] is None
    res = client.patch(f"/api/agents/{a['id']}", json={"evidence": "自己抄一份"})
    assert res.status_code == 400 and "双写" in res.json()["detail"]  # 引用态禁手动第二真源
    assert client.patch(f"/api/agents/{a['id']}",
                        json={"metrics_ref": "nope"}).status_code == 400


def _ref_then_registry_gone(store_with_registry, fixture_db, tmp_path):
    """公开面诚实构造漂移态：正常设好引用 → 注册表文件被删（不穿存储内部）。"""
    a = _agent_with_datasource(store_with_registry, fixture_db)
    store_with_registry.patch(a.id, metrics_ref="financial")
    (tmp_path / "metrics" / "financial.yaml").unlink()
    return a


def test_missing_registry_fails_honestly(store_with_registry, fixture_db, tmp_path):
    a = _ref_then_registry_gone(store_with_registry, fixture_db, tmp_path)
    res = _client(None, store_with_registry).get(f"/api/agents/{a.id}")
    assert res.status_code == 200  # 详情页还能看（诚实报错而非整页 500）
    body = res.json()
    assert body["business_knowledge"] == ""
    assert "financial" in body["business_knowledge_error"]


def test_ask_ref_broken_400_before_llm(store_with_registry, fixture_db, tmp_path):
    a = _ref_then_registry_gone(store_with_registry, fixture_db, tmp_path)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store_with_registry).post(
        "/api/ask", json={"agent_id": a.id, "question": "题"})
    assert res.status_code == 400 and "financial" in res.json()["detail"]
    assert llm.calls == 0  # 口径真源缺失也拒在调模型之前，不带病问数


# ── /api/ask：契约与口径优先级 ─────────────────────────────────────


def test_ask_success_contract(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post("/api/ask",
                                   json={"agent_id": a.id, "question": "Bob 成绩如何"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == _CONTRACT_KEYS  # 形状即契约，不扩不缩
    assert body["failed"] is False
    assert body["conclusion"].startswith("【结论】")  # 票 07 三节文本原样透出
    assert body["sql"] == "SELECT name FROM students WHERE id = 2"
    assert body["columns"] == ["name"]
    assert body["rows"] == [["Bob"]]
    assert body["truncated"] is False and isinstance(body["elapsed_ms"], int)
    assert body["path"] == "fallback" and body["session_id"] is None
    assert body["chart"] is None  # 单格文本非标量数值→其余→表格（null）
    assert body["clarification"] is None  # 票 03：关态/非澄清轮第 14 字段恒 null
    assert llm.calls == 3


def test_ask_clarification_round_contract(store, fixture_db):
    """M8 票 03：澄清轮＝第 14 字段出真值、failed=False（不是失败）、
    1 次 LLM 调用直达 END；澄清语即 conclusion（原样一句，非三节组装）。"""
    a = _agent_with_datasource(store, fixture_db)
    ask = "「表现」指成绩还是违约率？"
    blob = json.dumps({"question": "学生的表现如何", "intent": {}, "clarification": ask},
                      ensure_ascii=False)
    llm = ScriptedLLM([blob])
    client = TestClient(create_app(llm=llm, settings=_S_CLAR, agents=store,
                                   static_dir="__no_such_dist_for_tests__"))
    body = client.post("/api/ask",
                       json={"agent_id": a.id, "question": "学生表现如何"}).json()
    assert set(body) == _CONTRACT_KEYS  # 14 字段形状不扩不缩（一判双达同经本收口）
    assert body["clarification"] == ask and body["conclusion"] == ask
    assert body["failed"] is False and body["sql"] is None
    assert body["columns"] is None and body["chart"] is None
    assert llm.calls == 1  # 澄清＝最省动路：不进 explore 不进沙箱


def test_ask_evidence_precedence(store, fixture_db, monkeypatch):
    """口径优先级＝请求显式 > 智能体业务知识（手动或派生）> 空。"""
    seen = []

    def fake_run(db_path, question, evidence="", **kw):
        seen.append(evidence)
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    a = _agent_with_datasource(store, fixture_db, evidence="库口径")
    client = _client(ScriptedLLM([]), store)
    client.post("/api/ask", json={"agent_id": a.id, "question": "题"})
    client.post("/api/ask", json={"agent_id": a.id, "question": "题", "evidence": "会话输入"})
    assert seen == ["库口径", "会话输入"]
    b = store.create("空口径", "")
    store.store_datasource(b.id, b"x", "s.sqlite")
    client.post("/api/ask", json={"agent_id": b.id, "question": "题"})
    assert seen[2] == ""  # 两头都空→如实空（导入库可正常问答）


def test_ask_without_datasource_400(store):
    a = store.create("空壳", "")
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post("/api/ask", json={"agent_id": a.id, "question": "题"})
    assert res.status_code == 400 and "未配置数据源" in res.json()["detail"]
    assert llm.calls == 0  # 拒在调模型之前


def test_ask_unknown_agent_404(store):
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post("/api/ask",
                                   json={"agent_id": "deadbeef0000", "question": "题"})
    assert res.status_code == 404
    assert llm.calls == 0


def test_ask_failure_path_has_error_summary(store, fixture_db):
    """预算耗尽：HTTP 200（失败是领域结果非传输错误），failed＋error_summary 在场。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(["改写"] + ["SELECT nope FROM students"] * 3)
    res = _client(llm, store).post("/api/ask",
                                   json={"agent_id": a.id, "question": "谁成绩最好"})
    assert res.status_code == 200
    body = res.json()
    assert body["failed"] is True and body["error_summary"]
    assert "no such column" in body["conclusion"]
    for k in ("columns", "rows", "truncated", "elapsed_ms", "chart"):
        assert body[k] is None  # 无结果集时如实 null，不编造空表/图型
    assert llm.calls == 4


# ── 票 04：chart 可选字段经 /api/ask 的三形态（判定矩阵归 test_web_charts）──


def _ask(store, agent, sql, concl="结论"):
    llm = ScriptedLLM(["改写", sql, concl])
    body = _client(llm, store).post(
        "/api/ask", json={"agent_id": agent.id, "question": "问"}).json()
    assert body["failed"] is False and llm.calls == 3
    return body


def test_ask_chart_number_card(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    body = _ask(store, a, "SELECT COUNT(*) AS n FROM students")
    assert body["chart"] == {"type": "number", "x": None, "series": [0]}


def test_ask_chart_bar_category_numeric(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    body = _ask(store, a, "SELECT subject, score FROM scores")
    assert body["rows"] == [["math", 95.5], ["math", 88.0], ["english", 90.0]]
    assert body["chart"] == {"type": "bar", "x": 0, "series": [1]}


def test_ask_chart_line_time_series(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    sql = ("SELECT '1993-02' AS month, 5.5 AS v "
           "UNION ALL SELECT '1993-03', 6.5")
    body = _ask(store, a, sql)
    assert body["chart"] == {"type": "line", "x": 0, "series": [1]}


def test_ask_empty_question_422(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM([]), store)
    assert client.post("/api/ask",
                       json={"agent_id": a.id, "question": ""}).status_code == 422
    assert client.post("/api/ask", json={"agent_id": a.id}).status_code == 422


# ── 唯一入口钉测：AST 级禁旁路（web 包不碰连接层）───────────────────


def test_web_package_never_imports_sqlite3():
    """AST 级 import 纪律（metrics 先例同款）：web 五包禁 sqlite3/引擎驱动——
    连接唯一入口是 tools/db.open_readonly，存储层只搬文件，图型判定只算纯函数。"""
    for mod in (web_app, web_agents, web_charts, web_serve, web_sessions):
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


# ── serve 装配：依赖注入在包内收口（load_settings/build_llm/uvicorn 全替）──


def test_run_server_wires_dependencies(monkeypatch, tmp_path):
    import qadata.web.serve as serve_mod

    calls = {}
    fake_settings = Settings(api_key="", base_url="", model="m1",
                             metrics_dir=str(tmp_path / "no_metrics"))
    monkeypatch.setattr(serve_mod, "load_settings", lambda: fake_settings)
    monkeypatch.setattr(serve_mod, "build_llm", lambda s: f"LLM<{s.model}>")
    monkeypatch.setattr(serve_mod, "TraceLogger", lambda p: f"TRACER<{p}>")
    monkeypatch.setattr(serve_mod, "create_app",
                        lambda **kw: calls.update(kw) or "APP")
    monkeypatch.setattr(serve_mod, "uvicorn", types.SimpleNamespace(
        run=lambda app, **kw: calls.update(run=(app, kw))))
    agents_dir = tmp_path / "agents"
    serve_mod.run_server(host="h", port=1, agents_dir=str(agents_dir))
    assert calls["llm"] == "LLM<m1>" and calls["settings"] is fake_settings
    assert isinstance(calls["agents"], AgentStore)
    assert calls["agents"].all() == []  # 真空启动：serve 不注入任何预置
    assert calls["tracer"] == "TRACER<runs/traces.jsonl>"
    assert calls["run"] == ("APP", {"host": "h", "port": 1})


# ── 静态服务：构建产物同源；未构建时诚实占位页 ──────────────────────


def test_placeholder_when_not_built(tmp_path, store):
    client = _client(None, store, static_dir=tmp_path / "dist")
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "npm run build" in res.text  # 占位页写明构建命令，别白屏


def test_serves_built_index(tmp_path, store):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>built-app</html>", encoding="utf-8")
    (dist / "assets" / "x.js").write_text("//js", encoding="utf-8")
    client = _client(None, store, static_dir=dist)
    assert "built-app" in client.get("/").text
    assert "//js" in client.get("/assets/x.js").text
    assert client.get("/api/agents").status_code == 200  # 挂载不吃 API 路由
