"""M5 票 04：真实注册表 `metrics/financial.yaml` 的回归测试（夹具之外真文件回归一次）。

测试语义：纯函数＋零网络零真实 API——只走 load_registry→fill_slots→render_sql→sqlguard
的确定性链路，不碰 financial.sqlite（`data/` 不入库，CI 上不存在）。
口径内容的裁决归用户逐条拍板（票 04 护栏①），本文件只钉结构性契约：
可加载、规模在 12-18、卫生不变量（时间槽列归属血缘表）、渲染产物保持单语句 SELECT 形态。
"""
import datetime as dt
from pathlib import Path

import pytest

from qadata.graph.metrics import fill_slots, load_registry, render_sql
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
