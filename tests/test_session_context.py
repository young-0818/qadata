"""M7 票 05 图侧专测：`session_context` 唯一新状态键＋三层记忆注入。

钉四件套（票面）：①仅 understand/generate 消费——姊妹钉测＝test_generate_never_reads_intent
同款条款，nodes.py AST 源扫描钉死其他节点函数不得读取，行为面再钉 respond prompt
带会话与不带会话逐字节一致（同脚本改写句恒定的前提下）；②L2→understand 消解、
L1→generate 尾部草稿，各就各位不串门；③上轮 failed 不给草稿、failed 行只留问题
如实标失败（错误草稿不传染）；④成本条款——多轮与单轮同调用数（ScriptedLLM calls
钉死），每轮 attempts 独立入账（上一轮 3 连败烧光预算，本轮照常满预算重试成功）。
外加关态一致：无 session_context / None / 空载荷三种形态 prompt 逐字节一致，
CLI/eval 调用面源码出现 session_context 即红（on_event 同款纪律）。
L3 永不进 prompt＝K=5 切窗，在 web 层 build_session_context 钉（test_web_sessions.py）。
"""
import ast
import inspect

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.graph.prompts import format_session_draft, format_session_history
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_GOOD = "SELECT name FROM students WHERE id = 2"
_BAD = "SELECT nope FROM students"

_HIST_Q = "2026 年有多少新生"
_HIST_SQL = "SELECT COUNT(*) FROM student WHERE major = '计算机'"
_CTX = {
    "turns": [
        {"question": _HIST_Q, "sql": _HIST_SQL, "row_count": 1,
         "head": "标量值 120", "failed": False},
        {"question": "这些新生的班级分布", "sql": None, "row_count": None,
         "head": None, "failed": True},
    ],
    "draft": {"sql": _HIST_SQL, "head": "标量值 120"},
}
_H2 = "会话历史"  # L2 段头（唯一性锚点）
_H1 = "上一轮 SQL"  # L1 段头


def _run(db, script, **kw):
    llm = ScriptedLLM(script)
    ans = run_question(db, "这些新生男女比例怎么样", llm=llm, settings=_S, **kw)
    return llm, ans


# ── 关态一致：无会话＝与票 04 现状逐字节一致 ────────────────────────


def test_closed_state_prompts_byte_identical(fixture_db):
    """缺参 / None / 空载荷三形态 prompt 全集逐字节一致（understand＋generate＋respond）。"""
    script = ["改写", _GOOD, "结论"]
    runs = []
    for kw in ({}, {"session_context": None}, {"session_context": {"turns": [], "draft": None}}):
        llm = ScriptedLLM(list(script))
        run_question(fixture_db, "有几名学生", llm=llm, settings=_S, **kw)
        runs.append(llm.prompts)
    assert runs[0] == runs[1] == runs[2]
    for p in runs[0]:  # 对照组：关态里确实没有会话块（防空对空的假一致）
        assert _H2 not in p and _H1 not in p


# CLI/eval 调用面出现 session_context 即红——扫描已由 tests/test_on_event.py::
# test_cli_and_eval_call_sites_untouched 对 on_event/session_context 双名钉死，此处不重复。


# ── L2/L1 各就各位 ──────────────────────────────────────────────────


def test_l2_to_understand_l1_to_generate(fixture_db):
    llm, ans = _run(fixture_db, ["改写", _GOOD, "比例 1:1"], session_context=_CTX)
    assert ans.failed is False
    u, g, r = llm.prompts[:3]
    # L2 进 understand：问题＋SQL＋行数头部＋失败行如实标注，全在一块里
    assert _H2 in u
    assert _HIST_Q in u and _HIST_SQL in u and "标量值 120" in u
    assert "这些新生的班级分布" in u and "该轮查询失败" in u
    # L1 进 generate 尾部：草稿 SQL＋段头措辞钉"仅供参考"
    assert _H1 in g and _HIST_SQL in g
    assert _H2 not in g and _HIST_Q not in g  # L2 不串门（generate 只见草稿，不见历史清单）
    # respond 完全不消费（姊妹条款的行为面证据）
    assert _H2 not in r and _H1 not in r


def test_respond_prompt_identical_with_and_without_session(fixture_db):
    """姊妹钉测（test_generate_never_reads_intent 同款）：同脚本下带会话/不带会话
    双跑，respond prompt 逐字节一致——会话记忆永不渗入答案组织。"""
    script = ["改写", _GOOD, "结论"]
    prompts = []
    for kw in ({}, {"session_context": _CTX}):
        llm = ScriptedLLM(list(script))
        run_question(fixture_db, "这些新生男女比例怎么样", llm=llm, settings=_S, **kw)
        prompts.append(llm.prompts[2])
    assert prompts[0] == prompts[1]


# ── 错误草稿不传染 ──────────────────────────────────────────────────


def test_failed_prev_turn_gives_no_draft(fixture_db):
    """上轮 failed＝web 层不给 draft；图侧契约：draft 缺失/None → generate 无草稿块，
    L2 仍带该行问题供消解。"""
    ctx = {"turns": [{"question": _HIST_Q, "sql": _HIST_SQL, "row_count": None,
                       "head": None, "failed": True}],
           "draft": None}
    llm, ans = _run(fixture_db, ["改写", _GOOD, "结论"], session_context=ctx)
    assert ans.failed is False
    u, g = llm.prompts[0], llm.prompts[1]
    assert _HIST_Q in u and "该轮查询失败" in u and _HIST_SQL not in u
    assert _H1 not in g and _HIST_SQL not in g


def test_draft_none_variants_render_empty():
    for bad in (None, {}, {"draft": None}, {"draft": {}}, {"draft": {"sql": ""}},
                "not-a-dict", {"turns": None}):
        assert format_session_draft(bad) == ""
    assert format_session_history(None) == ""
    assert format_session_history({"turns": []}) == ""


# ── 成本与账本 ──────────────────────────────────────────────────────


def test_multi_turn_same_call_count(fixture_db):
    """成本条款：多轮零新增 LLM 调用——单轮/多轮同脚本同 calls（3＝understand+generate+respond）。"""
    plain, _ = _run(fixture_db, ["改写", _GOOD, "结论"])
    multi, _ = _run(fixture_db, ["改写", _GOOD, "结论"], session_context=_CTX)
    assert multi.calls == plain.calls == 3


def test_attempts_ledger_independent_per_turn(fixture_db):
    """每轮 attempts 独立入账：上一轮 3 连败烧光预算，本轮仍满预算——失败一次后重试成功。"""
    _, first_ans = _run(fixture_db, ["改写", _BAD, _BAD, _BAD])
    assert first_ans.failed is True  # 预算耗尽的诚实失败轮
    ctx = {"turns": [{"question": _HIST_Q, "sql": None, "row_count": None,
                      "head": None, "failed": True}], "draft": None}
    llm, ans = _run(fixture_db, ["改写", _BAD, _GOOD, "结论"], session_context=ctx)
    assert ans.failed is False and llm.calls == 4  # 本轮自有 3 格预算，重试环未被上轮饿死


# ── AST 纪律：仅 understand/generate 读取 session_context ───────────


def _own_ast_nodes(fn):
    """函数自身的 AST 节点（不下钻嵌套函数——嵌套体另测）。"""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _reads_session_context(fn) -> bool:
    for n in _own_ast_nodes(fn):
        if getattr(n, "id", "") == "session_context" or getattr(n, "attr", "") == "session_context":
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and "session_context" in n.value:
            return True
    return False


def test_only_understand_and_generate_read_session_context():
    import qadata.graph.nodes as nodes_mod

    parsed = ast.parse(inspect.getsource(nodes_mod))
    allowed = {"understand", "generate"}
    found = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.FunctionDef) and node.name in {
                "understand", "metric_match", "explore", "generate", "execute", "verify", "respond"}:
            assert not _reads_session_context(node) or node.name in allowed, \
                f"节点 {node.name} 读取了 session_context——越权消费"
            if _reads_session_context(node):
                found.add(node.name)
    assert found == allowed  # 两个合法消费者都确实在读（防全空转的假通过）


# ── 纯函数单测（渲染层）────────────────────────────────────────────


def test_history_renders_chronological_lines():
    block = format_session_history(_CTX)
    assert block.index(_HIST_Q) < block.index("这些新生的班级分布")  # 时间升序，上轮最贴近当前问题
    assert "1 行；标量值 120" in block


def test_draft_block_carries_head_summary():
    block = format_session_draft(_CTX)
    assert _HIST_SQL in block and "标量值 120" in block
    assert "仅供参考" in block or "草稿" in block  # 措辞钉"草稿仅参考"纪律


# ── 票 09 显式授权（话题连续性模型隐式判，Cortex 同构）──────────────


def test_history_block_carries_ignore_authorization():
    """L2 节头带授权句：与历史无关→忽略历史独立改写（人肉闸撤除后的连续性防线）。"""
    block = format_session_history(_CTX)
    assert "与历史无关" in block and "忽略这段历史" in block


def test_draft_block_carries_ignore_authorization():
    """L1 节头带授权句：与上问无关→忽略草稿独立完整生成（宁多带勿错切，判错方向
    由沙箱/verify 兜底——见 prompts.py 票 09 注释）。"""
    block = format_session_draft(_CTX)
    assert "与上一问无关" in block and "忽略这段草稿" in block
