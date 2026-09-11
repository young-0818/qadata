"""M7 票 01 契约测试：TestClient＋ScriptedLLM——不碰网、不碰真实数据目录（侦察笔记）。

契约形状由本文件钉死（spec「响应契约」节：改形状＝跨票改卷）。
happy path 恰好 3 次 LLM 调用（understand/generate/respond），纪律⑤ calls 断言照旧。
"""
import types

from fastapi.testclient import TestClient

from qadata.config import Settings
from qadata.types import Answer
from qadata.web.app import create_app
from qadata.web.dbs import discover_dbs
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="", retry_budget=3)

# 契约字段全集（票 01 冻结）——多一个少一个都算改卷
_CONTRACT_KEYS = {
    "conclusion", "sql", "columns", "rows", "truncated", "elapsed_ms",
    "failed", "error_summary", "path", "metric_name", "template_fell_back",
    "session_id",
}

_HAPPY_SCRIPT = ["改写", "SELECT name FROM students WHERE id = 2", "Bob 的数学 88 分"]


def _client(llm, fixture_db, **kw) -> TestClient:
    # static_dir 默认指向不存在目录：API 测试与本机真实 web/dist 构建产物隔离
    kw.setdefault("static_dir", "__no_such_dist_for_tests__")
    return TestClient(create_app(llm=llm, settings=_S, dbs={"school": fixture_db}, **kw))


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


# ── /api/dbs ────────────────────────────────────────────────────────


def test_list_dbs(fixture_db):
    res = _client(None, fixture_db).get("/api/dbs")
    assert res.status_code == 200
    assert res.json() == {"dbs": ["school"]}


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
    monkeypatch.setattr(serve_mod, "load_settings", lambda: "SETTINGS")
    monkeypatch.setattr(serve_mod, "build_llm", lambda s: f"LLM<{s}>")
    monkeypatch.setattr(serve_mod, "TraceLogger", lambda p: f"TRACER<{p}>")
    monkeypatch.setattr(serve_mod, "create_app",
                        lambda **kw: calls.update(kw) or "APP")
    monkeypatch.setattr(serve_mod, "uvicorn", types.SimpleNamespace(
        run=lambda app, **kw: calls.update(run=(app, kw))))
    root = tmp_path / "bird"
    (root / "financial").mkdir(parents=True)
    (root / "financial" / "financial.sqlite").write_bytes(b"")
    serve_mod.run_server(host="h", port=1, db_dir=str(root))
    assert calls["llm"] == "LLM<SETTINGS>" and calls["settings"] == "SETTINGS"
    assert calls["dbs"] == {"financial": str(root / "financial" / "financial.sqlite")}
    assert calls["tracer"] == "TRACER<runs/traces.jsonl>"
    assert calls["run"] == ("APP", {"host": "h", "port": 1})


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
