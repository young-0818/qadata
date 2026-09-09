"""M5 票 05：metric_match 节点接线的 run_question 缝端到端用例（spec 缝 A）。

测试语义：ScriptedLLM＋真只读库＋真 metrics 纯函数链路，只从最高缝断言
「输入题＋脚本化回复 → 答案/路径/调用次数」，不 mock 图内部、不断言私有函数。
仿 test_graph.py 的 calls 计数风格；命中省调用即由 calls 数与 ScriptedLLM 超脚本即炸共同钉死。
"""
import json
import sqlite3

import pytest

from qadata import run_question
from qadata.config import Settings
from tests.fakes import ScriptedLLM

# 真库 loan 表：1995 年 3 笔（含 B 违约 1）＋1997 年 1 笔，覆盖时间填槽与违约过滤
_LOAN_DDL = """
CREATE TABLE loan (loan_id INTEGER PRIMARY KEY, account_id INTEGER,
                   date TEXT, amount REAL, status TEXT);
INSERT INTO loan VALUES (1, 1, '1995-03-04', 100.0, 'A');
INSERT INTO loan VALUES (2, 2, '1995-06-15', 200.0, 'B');
INSERT INTO loan VALUES (3, 3, '1995-11-30', 300.0, 'C');
INSERT INTO loan VALUES (4, 4, '1997-05-05', 400.0, 'A');
"""

# 命中友好：别名覆盖「贷款笔数」，时间槽声明 YYYY-MM-DD（库实际形态）
_REGISTRY = """
metrics:
  - name: loan_count
    display_name: 贷款笔数
    aliases: [贷款数, number of loans, loan count]
    meaning: 统计期内批准的贷款合同数量
    definition: 以 loan.date 落入时间范围计条
    sql_template: SELECT COUNT(loan.loan_id) FROM loan WHERE loan.date BETWEEN {time_start} AND {time_end}
    time_slot: {column: loan.date, date_format: YYYY-MM-DD}
    available_dimensions: {}
    source_tables: [loan]
"""


def _understand_json(question, mention=None, filters=None, dims=None):
    intent = {
        "metric_mention": mention,
        "dimensions": dims,
        "filters": filters,
        "output_form": None,
        "format_constraint": None,
        "evidence_terms": None,
    }
    return json.dumps({"question": question, "intent": intent}, ensure_ascii=False)


@pytest.fixture
def fin_env(tmp_path):
    """建 loan 库＋注册表目录；返回 (db_path, settings_kwargs_dir)。"""
    db = tmp_path / "financial" / "financial.sqlite"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(_LOAN_DDL)
    conn.commit()
    conn.close()
    reg_dir = tmp_path / "metrics"
    reg_dir.mkdir()
    (reg_dir / "financial.yaml").write_text(_REGISTRY, encoding="utf-8")
    return str(db), str(reg_dir)


def _S(reg_dir, *, layer=True, budget=3):
    return Settings(api_key="", base_url="", model="", retry_budget=budget,
                    metric_layer=layer, metrics_dir=reg_dir)


def test_hit_answers_via_template_saves_two_calls(fin_env):
    """命中：L1 别名匹配＋填槽成功 → 模板 SQL 直答，generate/explore 未进（calls=2）。"""
    db, reg = fin_env
    llm = ScriptedLLM([
        _understand_json("1995年贷款笔数", mention="贷款笔数", filters=["1995"]),
        "1995年共 3 笔贷款",  # respond
    ])
    ans = run_question(db, "1995年贷款笔数是多少", llm=llm, settings=_S(reg))
    assert ans.failed is False
    assert ans.path == "metric" and ans.metric_name == "loan_count"
    assert ans.template_fell_back is False
    assert ans.result.rows == [(3,)]
    # 关键：命中题只烧 understand+respond 两次；generate/explore 未进（超脚本即炸）
    assert llm.calls == 2
    assert "COUNT(loan.loan_id)" in ans.sql and "'1995-01-01'" in ans.sql


def test_hit_l2_review_recovers_when_l1_misses(fin_env):
    """L1 未命中（mention 无别名）→ L2 复核命中 → 仍走模板（calls=3：understand+review+respond）。"""
    db, reg = fin_env
    llm = ScriptedLLM([
        _understand_json("1995年有多少笔贷款", mention="贷款的合同数量", filters=["1995"]),
        "loan_count",  # L2 复核输出指标名
        "3 笔",  # respond
    ])
    ans = run_question(db, "1995年有多少笔贷款", llm=llm, settings=_S(reg))
    assert ans.path == "metric" and ans.metric_name == "loan_count"
    assert ans.result.rows == [(3,)]
    assert llm.calls == 3  # understand + L2 metric_match + respond（仍省 generate/explore）


def test_l2_none_falls_back(fin_env):
    """L1 未中、L2 判 NONE → 兜底路线（generate 正常跑）。"""
    db, reg = fin_env
    llm = ScriptedLLM([
        _understand_json("客户平均余额", mention="平均余额"),  # mention 不在别名
        "NONE",  # L2 判未命中
        "SELECT COUNT(loan_id) FROM loan",  # generate（兜底）
        "4 笔",  # respond
    ])
    ans = run_question(db, "客户平均余额是多少", llm=llm, settings=_S(reg))
    assert ans.path == "fallback" and ans.metric_name is None
    assert ans.template_fell_back is False
    assert llm.calls == 4


def test_no_registry_file_skips_node_zero_calls(tmp_path):
    """该库无注册表文件 → 整节点跳过、零调用 → 行为等同开关关。"""
    db = tmp_path / "other.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1);")
    conn.commit()
    conn.close()
    reg = tmp_path / "empty_metrics"
    reg.mkdir()
    llm = ScriptedLLM([
        _understand_json("有多少行", mention="行数", filters=["1995"]),
        "SELECT COUNT(*) FROM t",  # generate 兜底
        "1 行",  # respond
    ])
    ans = run_question(str(db), "有多少行", llm=llm, settings=_S(str(reg)))
    assert ans.path == "fallback" and ans.metric_name is None
    assert llm.calls == 3  # 无注册表：不额外烧 L2 复核


def test_template_exec_failure_downgrades_once(fin_env):
    """模板执行失败 → 记一条模板 attempt → 降级兜底恰好一次；generate prompt 可见降级原因。"""
    db, reg = fin_env
    # 用坏列模板触发执行失败（填槽成功但 SQL 跑不通）
    bad_reg = _REGISTRY.replace(
        "COUNT(loan.loan_id)", "COUNT(loan.nope_col)")
    from pathlib import Path
    Path(reg, "financial.yaml").write_text(bad_reg, encoding="utf-8")
    llm = ScriptedLLM([
        _understand_json("1995年贷款笔数", mention="贷款笔数", filters=["1995"]),
        "SELECT COUNT(loan.loan_id) FROM loan WHERE loan.date BETWEEN '1995-01-01' AND '1995-12-31'",  # generate 兜底（正确列）
        "3 笔",  # respond
    ])
    ans = run_question(db, "1995年贷款笔数是多少", llm=llm, settings=_S(reg))
    assert ans.failed is False
    assert ans.path == "fallback"
    assert ans.template_fell_back is True  # 降级旗标
    assert "最终答案由兜底路径生成" in ans.conclusion
    assert llm.calls == 3  # understand + 1 次兜底 generate + respond（降级恰好一次）
    # generate prompt 必须带模板 SQL 失败账（attempts 含模板记录）＋降级说明段
    gen_prompt = llm.prompts[1]
    assert "nope_col" in gen_prompt and "指标模板降级" in gen_prompt


def test_metric_layer_off_full_parity(fin_env):
    """总开关关：命中形态的题也走纯 SQL 现状（metric_match 不进，calls=3，path=fallback）。"""
    db, reg = fin_env
    # 关：命中形态的 understand 脚本，但 generate 照常调用（省不掉），path=fallback
    off = ScriptedLLM([
        _understand_json("1995年贷款笔数", mention="贷款笔数", filters=["1995"]),
        "SELECT COUNT(loan.loan_id) FROM loan WHERE loan.date BETWEEN '1995-01-01' AND '1995-12-31'",
        "3 笔",
    ])
    ans = run_question(db, "1995年贷款笔数是多少", llm=off, settings=_S(reg, layer=False))
    assert ans.path == "fallback" and ans.metric_name is None
    assert ans.template_fell_back is False
    assert off.calls == 3  # understand + generate + respond（未省，metric_match 不进）


# ── 真库真注册表集成（data/ 不入库，CI 缺文件即跳）──────────────────

import os

_REAL_DB = "data/bird/dev/dev_databases/financial/financial.sqlite"
_REAL_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_has_real = pytest.mark.skipif(
    not os.path.isfile(os.path.join(_REAL_REPO_ROOT, _REAL_DB)),
    reason="真 financial.sqlite 不入库（CI 缺文件）")


@_has_real
def test_real_financial_registry_hit_end_to_end(monkeypatch):
    """真 18 条注册表＋真 financial 库端到端：违约率题命中模板、直答省 generate。"""
    monkeypatch.chdir(_REAL_REPO_ROOT)  # metrics_dir="metrics" 相对仓库根
    llm = ScriptedLLM([
        _understand_json("1995年批准的贷款的违约率", mention="违约率", filters=["1995"]),
        "1995年违约率约为某个百分比",  # respond
    ])
    ans = run_question(_REAL_DB, "1995年批准的贷款的违约率是多少", llm=llm,
                       settings=Settings(api_key="", base_url="", model="",
                                         metric_layer=True, metrics_dir="metrics"))
    assert ans.path == "metric" and ans.metric_name == "loan_default_rate"
    assert ans.result is not None and not ans.failed
    assert llm.calls == 2  # understand + respond（generate/explore 未进）


@_has_real
def test_gate9_named_extreme_question_l2_none_falls_back(monkeypatch):
    """⑨ 闸靶例端到端（题形捕捉自冻结集 Q156「largest loan amount」）：
    具名极值题 L1 未中→L2 复核判 NONE→兜底，绝不得把户均/合计模板当答案直出。
    （L2 指令对真模型的有效性＝票 06 探针在体验证；本例钉接线形态。）"""
    monkeypatch.chdir(_REAL_REPO_ROOT)
    q = "Who is the owner of the account with the largest loan amount?"
    llm = ScriptedLLM([
        _understand_json(q, mention="largest loan amount"),  # 最高级随题面摘进 mention
        "NONE",  # L2 依⑨指令判负
        "account, loan, client, disp",  # explore 选表（真 financial 8 表超全量上限，兜底路径要 1 调）
        ("SELECT c.owner FROM loan l JOIN account a ON l.account_id = a.account_id "
         "JOIN disp d ON d.account_id = a.account_id JOIN client c ON c.client_id = d.client_id "
         "ORDER BY l.amount DESC LIMIT 1"),  # generate 兜底
        "最大贷款账户的持有人",  # respond
    ])
    ans = run_question(_REAL_DB, q, llm=llm,
                       settings=Settings(api_key="", base_url="", model="",
                                         metric_layer=True, metrics_dir="metrics"))
    assert ans.path == "fallback" and ans.metric_name is None
    assert ans.template_fell_back is False  # 未进过模板，不算降级
    assert llm.calls == 5  # understand + L2 + explore 选表 + generate + respond
