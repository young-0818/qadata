"""M5 票 04：真实注册表 `metrics/financial.yaml` 的回归测试（夹具之外真文件回归一次）。

测试语义：纯函数＋零网络零真实 API——只走 load_registry→fill_slots→render_sql→sqlguard
的确定性链路，不碰 financial.sqlite（`data/` 不入库，CI 上不存在）。
口径内容的裁决归用户逐条拍板（票 04 护栏①），本文件只钉结构性契约：
可加载、规模在 12-18、卫生不变量（时间槽列归属血缘表）、渲染产物保持单语句 SELECT 形态。
"""
import datetime as dt
from pathlib import Path

import pytest

from qadata.graph.metrics import fill_slots, load_registry, match_metric, render_sql
from qadata.tools.sqlguard import validate_sql

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "metrics" / "financial.yaml"

TODAY = dt.date(2024, 6, 15)


@pytest.fixture(scope="module")
def registry():
    return load_registry(REGISTRY_PATH)


def test_financial_registry_loads_with_expected_scale(registry):
    """票面契约：12-18 条原子指标，六要素缺失＝加载期即拒绝（load_registry 抛则测试红）。"""
    assert 12 <= len(registry) <= 18
    names = [m.name for m in registry]
    assert len(set(names)) == len(names)


def test_time_slot_columns_belong_to_source_tables(registry):
    """卫生不变量：时间槽声明列的全限定前缀必须是该指标血缘表之一（防指错库表）。"""
    for m in registry:
        if m.time_slot is not None:
            table = m.time_slot.column.split(".", 1)[0]
            assert table in m.source_tables, (
                f"{m.name}：时间槽列 {m.time_slot.column} 不属于血缘表 {m.source_tables}"
            )
        for col in m.available_dimensions.values():
            assert col.split(".", 1)[0] in m.source_tables, (
                f"{m.name}：维度列 {col} 不属于血缘表 {m.source_tables}"
            )


def test_every_metric_fills_renders_and_passes_sqlguard(registry):
    """逐条冒烟：按声明构造完备意图→填槽→渲染→sqlguard。

    钉死两件事：① 真文件每条都可被完备意图填满（声明与模板不互斥，票 03 对账规则的
    真文件侧印证）；② 渲染产物仍是单语句、根为 SELECT、表全部在血缘内——不绕过白名单假设。
    """
    known_tables = {t for m in registry for t in m.source_tables}
    for m in registry:
        filters = ["1996"] if m.time_slot is not None else []
        filters += [next(iter(slot.values)) for slot in m.filter_slots]
        dims = [next(iter(m.available_dimensions))] if "dimensions" in m.placeholders else []
        intent = {"metric_mention": m.display_name, "dimensions": dims, "filters": filters}
        result = fill_slots(m, intent, today=TODAY)
        assert result.ok, f"{m.name}：完备意图下填槽仍判未命中（{result.reason}）"
        sql = render_sql(m, result.params)
        assert "{" not in sql, f"{m.name}：渲染后仍有未填占位符"
        # 不过关抛 SqlExecutionError＝本测试红（表全集取各条血缘表并集，不碰 data/）
        validate_sql(sql, allowed_tables=sorted(known_tables))


# ── M5 票 06：⑨ 闸靶例（真文件回归侧＝判域，真跑侧＝在体观测）──────────
# 票 04 §F.4 捕捉自轨道①冻结题集的真实形态：Q98/99（approved amount 极值）、
# Q156（largest loan amount）、Q94/189（lowest average salary）。


def test_gate9_superlative_mentions_reach_l2(registry):
    """极值形态 mention（含最高级词/题面措辞整摘）L1 确定性别名判未中——
    ⑨「具名极值→NONE」L2 指令确为这类题的看门人（本测试钉闸的生效域）。"""
    for mention in ("lowest approved amount", "largest loan amount", "loan amount",
                    "highest approved amount", "most accounts with loan"):
        assert match_metric(mention, registry) is None, f"{mention!r} 竟被 L1 命中"


def test_gate9_residual_bare_mention_passes_l1(registry):
    """残留风险成文（票 04 最重风险的当前状态钉，非「正确行为」背书）：
    裸「approved amount」经包含单命中户均条（"average approved amount" ⊃ "approved amount"）、
    裸「average salary」精确命中——此时 L2 极值指令**触不到**（L1 已命中直填槽）。
    防线实为 understand 的 mention 抽取质量＋填槽宁空勿造，在体胜负看票 06 真跑观测；
    若要机制化（output_form 含最高/最低即拒填）属裁决变更，随本测试一起改（owner 拍板）。"""
    m = match_metric("approved amount", registry)
    assert m is not None and m.name == "loan_approved_amount_avg"
    # 时间窗可填＝误命中活性域（Q98 型带 1997）；纯骨架无时间被宁空勿造兜住（Q156 型同理）
    assert fill_slots(m, {"metric_mention": "approved amount", "dimensions": [],
                          "filters": ["1997"]}, today=TODAY).ok
    assert not fill_slots(m, {"metric_mention": "approved amount", "dimensions": [],
                              "filters": []}, today=TODAY).ok
    m2 = match_metric("lowest average salary", registry)  # Q94/189 族：包含穿透
    assert m2 is not None and m2.name == "avg_salary"
