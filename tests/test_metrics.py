"""M5 票 03：metrics 纯函数模块（注册表加载校验/一级匹配/填槽/渲染）单测。

测试语义（spec 缝 B）：全部纯函数单测、零 mock LLM——先例 test_precise 纯函数层、
test_intent、match.py 的钉死风格；本票全程用夹具注册表（测试内 dict + tmp_path YAML），
不依赖真实 metrics/financial.yaml（真文件回归是票 04 的验收项）。
"""
import datetime as dt
import inspect

import pytest
import yaml

from qadata.graph import metrics as metrics_mod
from qadata.graph.metrics import (
    DATE_FORMATS,
    FillResult,
    Metric,
    RegistryError,
    fill_slots,
    has_extreme_signal,
    load_registry,
    match_metric,
    normalize,
    parse_registry,
    render_sql,
)
from qadata.tools.sqlguard import validate_sql
from qadata.types import SqlExecutionError

TODAY = dt.date(2024, 6, 15)


# ── 夹具注册表（不依赖真实 YAML）──────────────────────────────────

# 时间槽声明形态 A：文本日期列（loan.date 存 'YYYY-MM-DD'）；
# 模板时间过滤必须原样写声明列（列声明非装饰，code-review Spec(a)2 收紧的契约）
_RATE_SQL = (
    "SELECT district_id, CAST(SUM(CASE WHEN status = 'B' THEN 1 ELSE 0 END) AS REAL)"
    " / COUNT(*) FROM loan"
    " WHERE loan.date BETWEEN {time_start} AND {time_end}"
    " GROUP BY {dimensions}"
)
# 时间槽声明形态 B：整数日期列（account.date 存 YYMMDD）——票面点名的两类坑位
_ACCT_SQL = (
    "SELECT COUNT(*) FROM account"
    " WHERE account.date BETWEEN {time_start} AND {time_end}"
)
# 过滤槽：口径由模板承载，值由意图 filters 经值映射填入
_STATUS_SQL = "SELECT COUNT(*) FROM loan WHERE status = {status}"


def _full_entry(**overrides):
    """完整六要素＋aliases 的夹具条目（缺省即通过校验的形态）。"""
    entry = {
        "name": "loan_default_rate",
        "display_name": "贷款违约率",
        "aliases": ["违约率", "不良率"],
        "meaning": "已结束合同中以违约收尾的占比",
        "definition": "违约＝loan.status='B'；分母＝全部结束合同",
        "sql_template": _RATE_SQL,
        "time_slot": {"column": "loan.date", "date_format": "YYYY-MM-DD"},
        "available_dimensions": {"地区": "district_id", "性别": "gender"},
        "source_tables": ["loan"],
    }
    entry.update(overrides)
    return entry


def _registry(*entries):
    return {"metrics": list(entries)}


def _parse_one(**overrides) -> Metric:
    return parse_registry(_registry(_full_entry(**overrides)))[0]


def _acct_metric() -> Metric:
    return _parse_one(sql_template=_ACCT_SQL, available_dimensions={},
                      time_slot={"column": "account.date", "date_format": "YYMMDD"})


def _intent(**fields) -> dict:
    base = dict.fromkeys(
        ["metric_mention", "dimensions", "filters", "output_form",
         "format_constraint", "evidence_terms"]
    )
    base.update(fields)
    return base


# ── 加载与六要素校验（正反例：缺任一项报错且不静默降级）────────────


def test_parse_registry_accepts_complete_entry():
    m = _parse_one()
    assert isinstance(m, Metric)
    assert m.name == "loan_default_rate"
    assert m.display_name == "贷款违约率"
    assert list(m.aliases) == ["违约率", "不良率"]
    assert m.time_slot.column == "loan.date"
    assert m.time_slot.date_format == "YYYY-MM-DD"
    assert m.available_dimensions == {"地区": "district_id", "性别": "gender"}
    assert list(m.source_tables) == ["loan"]
    # 模板占位符集合在加载期解析并入册（填槽/渲染共用，不二次发明）
    assert m.placeholders == {"time_start", "time_end", "dimensions"}


def test_load_registry_reads_yaml_file(tmp_path):
    p = tmp_path / "fixture.yaml"
    p.write_text(
        yaml.safe_dump(_registry(_full_entry()), allow_unicode=True),
        encoding="utf-8",
    )
    registry = load_registry(p)
    assert [m.name for m in registry] == ["loan_default_rate"]


@pytest.mark.parametrize(
    "field",
    ["name", "display_name", "aliases", "meaning", "definition",
     "sql_template", "source_tables", "available_dimensions"],
)
def test_missing_any_required_field_rejects_load(field):
    entry = _full_entry()
    del entry[field]
    with pytest.raises(RegistryError) as exc:
        parse_registry(_registry(entry))
    assert field in str(exc.value)
    assert "loan_default_rate" in str(exc.value) or "第 1" in str(exc.value)


@pytest.mark.parametrize("field", ["name", "display_name", "meaning",
                                   "definition", "sql_template"])
def test_blank_required_field_rejects_load(field):
    with pytest.raises(RegistryError):
        _parse_one(**{field: "   "})


def test_malformed_alias_entry_rejects_load():
    with pytest.raises(RegistryError):
        _parse_one(aliases="违约率")          # 须为列表不是裸字符串
    with pytest.raises(RegistryError):
        _parse_one(aliases=["违约率", "  "])  # 列表内空串


def test_unsupported_date_format_rejects_load():
    with pytest.raises(RegistryError) as exc:
        _parse_one(time_slot={"column": "loan.date",
                              "date_format": "YYYYMMDDTHHMMSS"})
    assert "date_format" in str(exc.value)
    assert set(DATE_FORMATS) == {"YYYY-MM-DD", "YYMMDD"}


def test_time_slot_declared_without_placeholders_rejects_load():
    """声明了时间槽但模板没用它＝带病运行（时间条件静默丢失），加载期拒绝。"""
    with pytest.raises(RegistryError):
        _parse_one(sql_template="SELECT COUNT(*) FROM loan"
                   " WHERE loan.date IS NOT NULL",
                   available_dimensions={})


def test_filter_slot_declared_without_placeholder_rejects_load():
    with pytest.raises(RegistryError):
        _parse_one(sql_template=_ACCT_SQL, available_dimensions={},
                   time_slot={"column": "account.date", "date_format": "YYMMDD"},
                   filter_slots={"status": {"违约": "B"}})


def test_time_slot_column_must_appear_in_template_rejects_load():
    """声明列与模板过滤列对不上＝列声明是装饰，加载期拒绝（review Spec(a)2）。"""
    with pytest.raises(RegistryError) as exc:
        _parse_one(time_slot={"column": "account.date",
                              "date_format": "YYYY-MM-DD"})
    assert "account.date" in str(exc.value)


def test_empty_aliases_allowed_but_empty_source_tables_rejected():
    """契约：匹配键＝aliases∪展示名（display_name 单独可寻址），aliases 允许为空；
    血缘缺失＝六要素残缺，拒绝。"""
    m = _parse_one(aliases=[])
    assert match_metric("贷款违约率", [m]) is m
    with pytest.raises(RegistryError):
        _parse_one(source_tables=[])


def test_unknown_placeholder_rejects_load():
    with pytest.raises(RegistryError):
        _parse_one(sql_template=_RATE_SQL + " HAVING x > {mystery}")


@pytest.mark.parametrize(
    "bad_template",
    [
        ("SELECT {% if dims %} a {% endif %} FROM loan"
         " WHERE loan.date >= {time_start} AND loan.date <= {time_end}"),
        ("SELECT {time_start|iso} FROM loan WHERE loan.date >= {time_start}"
         " AND loan.date <= {time_end}"),
        "SELECT a FROM loan WHERE t LIKE {time_start}}% AND loan.date <= {time_end}",
    ],
)
def test_conditional_loop_or_malformed_placeholder_rejects_load(bad_template):
    """命名占位符之外的花括号语法（条件/循环/jinja 过滤器/残缺大括号）一律拒载。"""
    with pytest.raises(RegistryError):
        _parse_one(sql_template=bad_template)


def test_dimensions_placeholder_without_declared_dimensions_rejects_load():
    """模板写 {dimensions} 但可用维度为空＝该槽永远填不出，加载期拒绝。"""
    with pytest.raises(RegistryError):
        _parse_one(available_dimensions={}, sql_template=_RATE_SQL)


def test_duplicate_search_key_rejects_load():
    """别名跨条目撞车＝一级匹配歧义源，加载期拒绝。"""
    with pytest.raises(RegistryError) as exc:
        parse_registry(_registry(
            _full_entry(),
            _full_entry(name="other", display_name="另一指标",
                        aliases=["违约率"]),
        ))
    assert "违约率" in str(exc.value)


def test_duplicate_metric_name_rejects_load():
    with pytest.raises(RegistryError):
        parse_registry(_registry(
            _full_entry(),
            _full_entry(name="loan_default_rate", display_name="另一指标",
                        aliases=["另一别名"]),
        ))


def test_malformed_registry_structure_rejects_load():
    with pytest.raises(RegistryError):
        parse_registry("不是列表也不是字典")
    with pytest.raises(RegistryError):
        parse_registry({"metrics": "nope"})
    with pytest.raises(RegistryError):
        parse_registry([_full_entry()])  # 契约形态只有顶层 'metrics:' 映射


def test_load_registry_missing_file_raises():
    with pytest.raises(RegistryError):
        load_registry("nonexistent/registry.yaml")


# ── 归一化 + 第一级匹配（大小写/空白/全半角；别名命中与不命中）──────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Loan Default Rate", "loandefaultrate"),       # 大小写＋空白
        ("违约 率", "违约率"),                            # 中文空白
        ("ＡＢＣ ２０２３", "abc2023"),                   # 全角字母数字（全半角）
        ("２０２３ 年", "2023年"),                        # 全角数字
    ],
)
def test_normalize_case_space_fullwidth(raw, expected):
    assert normalize(raw) == expected


def _two_metrics():
    return parse_registry(_registry(
        _full_entry(),
        _full_entry(name="acct_count", display_name="账户开户数",
                    aliases=["开户数"], sql_template=_ACCT_SQL,
                    available_dimensions={},
                    time_slot={"column": "account.date",
                               "date_format": "YYMMDD"}),
    ))


@pytest.mark.parametrize(
    "mention",
    ["违约率", "贷款违约率", "  不良率 ", "不良 率"],
)
def test_match_level1_exact_via_alias_or_display(mention):
    metrics = _two_metrics()
    assert match_metric(mention, metrics) is metrics[0]


def test_match_level1_inclusion_hit_and_miss():
    metrics = _two_metrics()
    # 题面提及包含展示名 → 命中
    assert match_metric("去年各区县的贷款违约率是多少", metrics) is metrics[0]
    # 提及包含别名＋无关前后缀 → 命中
    assert match_metric("LOAN违约率", metrics) is metrics[0]
    # 提及被唯一一条指标的别名包含 → 命中
    assert match_metric("率", metrics) is metrics[0]
    # 完全不相干 → 不命中
    assert match_metric("利率", metrics) is None


def test_match_level1_ambiguity_is_no_hit():
    """包含路径命中多条指标 → 歧义判未命中（宁漏勿错，交第二级 LLM 复核＝票 05）。"""
    metrics = parse_registry(_registry(
        _full_entry(),
        _full_entry(name="loan_bad_rate", display_name="不良贷款率",
                    aliases=["坏账率"]),
    ))
    assert match_metric("不良率", metrics) is metrics[0]   # 精确不受歧义影响
    assert match_metric("率", metrics) is None            # 双候选包含 → None


def test_match_level1_empty_or_absent_mention():
    metrics = _two_metrics()
    assert match_metric(None, metrics) is None
    assert match_metric("", metrics) is None
    assert match_metric("   ", metrics) is None
    assert match_metric("违约率", []) is None


# ── ⑨ 闸·机制版（票 06 owner 批准）：具名极值/比较题形判定纯函数 ──────

@pytest.mark.parametrize("text", [
    "Which account has the lowest approved amount",   # lowest
    "top nine districts by highest",                   # top/highest
    "the owner of the largest loan",                   # largest
    "rank the districts",                              # rank
    "oldest client",                                   # oldest
    "户均贷款额最高的地区",                             # 最…高
    "排名前 5 的分区",                                  # 排名
    "第 3 高的账户",                                    # 第…高
])
def test_extreme_signal_positive(text):
    assert has_extreme_signal(text)


@pytest.mark.parametrize("text", [
    "average approved amount",                         # 无最高级：户均条合法命中
    "number of loans in 1997",
    "贷款违约率是多少",
    "almost everyone",                                 # 词边界：最…不含极值词形
    "second district",                                 # second 非触发词（序数仅 第N/前N）
    "",                                                # 空串
])
def test_extreme_signal_negative(text):
    assert not has_extreme_signal(text)


def test_extreme_signal_ignores_non_string_and_or_across_args():
    assert not has_extreme_signal(None, 123, ["most"])   # 非字符串忽略
    assert has_extreme_signal("average loan amount", None, "the highest")  # 任一命中即 True


# ── 填槽（时间/维度/过滤；任一槽填不出＝判未命中——宁空勿造）──────────


def test_fill_ok_time_and_dimensions_text_date_format():
    """filters/dimensions 齐备 → 参数化槽值（YYYY-MM-DD 声明：带引号文本日期区间）。"""
    m = _parse_one()
    res = fill_slots(m, _intent(metric_mention="违约率",
                                filters=["2023年"], dimensions=["地区"]),
                     today=TODAY)
    assert res.ok and m.placeholders <= set(res.params)
    assert res.params["time_start"] == "'2023-01-01'"
    assert res.params["time_end"] == "'2023-12-31'"
    assert res.params["dimensions"] == "district_id"


def test_fill_ok_integer_yymmdd_format():
    """YYMMDD 声明（整数列）：填无引号 6 位整数区间——与文本形态各一例，两类声明都验。"""
    m = _acct_metric()
    res = fill_slots(m, _intent(filters=["2023-05-01到2023-05-31"]), today=TODAY)
    assert res.ok
    assert res.params["time_start"] == "230501"
    assert res.params["time_end"] == "230531"
    # 年粒度展开同样落到 YYMMDD 边界
    res2 = fill_slots(m, _intent(filters=["2023年"]), today=TODAY)
    assert (res2.params["time_start"], res2.params["time_end"]) == ("230101", "231231")


def test_fill_date_unparseable_rejects_for_yymmdd_declaration():
    """YYMMDD 声明下日期解析不出（2月30日不存在）→ 判未命中（票面两类声明各一例之一）。"""
    m = _acct_metric()
    res = fill_slots(m, _intent(filters=["2023年2月30日"]), today=TODAY)
    assert not res.ok
    assert res.params is None
    assert "时间" in res.reason or "日期" in res.reason


def test_fill_date_unparseable_rejects_for_text_declaration():
    """YYYY-MM-DD 声明下月份越界 → 判未命中（另一例）。"""
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年13月"], dimensions=["地区"]),
                     today=TODAY)
    assert not res.ok
    assert "时间" in res.reason or "日期" in res.reason


def test_fill_yymmdd_century_ambiguity_is_format_specific_miss():
    """票面「日期格式对不上」的真正形态：1993 年解析完全成功，但 YYMMDD 双位年无歧义
    表示 → 未命中；同一年份在 YYYY-MM-DD 声明下正常命中——失败源于格式声明而非解析，
    这才算两类声明各一例（review Spec(a)1）。"""
    m = _acct_metric()
    res = fill_slots(m, _intent(filters=["1993年"]), today=TODAY)
    assert not res.ok and "YYMMDD" in res.reason
    m2 = _parse_one()  # YYYY-MM-DD 声明
    res2 = fill_slots(m2, _intent(filters=["1993年"], dimensions=["地区"]),
                      today=TODAY)
    assert res2.ok and res2.params["time_start"] == "'1993-01-01'"


def test_fill_time_slot_missing_when_declared():
    """指标声明了时间槽而题面没给任何时间 → 宁空勿造，判未命中。"""
    m = _parse_one()
    res = fill_slots(m, _intent(dimensions=["地区"]), today=TODAY)
    assert not res.ok
    assert "时间" in res.reason


def test_fill_month_and_iso_date_and_relative_year():
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年5月"], dimensions=["地区"]),
                     today=TODAY)
    assert (res.params["time_start"], res.params["time_end"]) == \
        ("'2023-05-01'", "'2023-05-31'")
    res = fill_slots(m, _intent(filters=["去年"], dimensions=["地区"]), today=TODAY)
    assert (res.params["time_start"], res.params["time_end"]) == \
        ("'2023-01-01'", "'2023-12-31'")


def test_fill_dimension_out_of_available_dimensions_rejects():
    """维度越界（不在可用维度声明内）→ 判未命中。"""
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年"], dimensions=["职业"]),
                     today=TODAY)
    assert not res.ok
    assert "维度" in res.reason


def test_fill_dimensions_required_by_template_but_absent():
    """模板含 {dimensions} 槽而题面没给维度＝该槽填不出 → 判未命中（不悄悄渲染成空）。"""
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年"]), today=TODAY)
    assert not res.ok
    assert "维度" in res.reason


def test_fill_dimensions_not_supported_by_metric():
    """指标不支持分组（模板无 {dimensions}）而题面给了维度 → 判未命中。"""
    m = _acct_metric()
    res = fill_slots(m, _intent(filters=["2023年"], dimensions=["地区"]),
                     today=TODAY)
    assert not res.ok


def test_fill_filter_slot_mapping():
    """非时间 filter 命中过滤槽值映射（标签归一化后）→ 填 SQL 字面量；映射不到 → 未命中。"""
    m = _parse_one(sql_template=_STATUS_SQL, available_dimensions={},
                   time_slot=None,
                   filter_slots={"status": {"违约": "B", "已结清": "D"}})
    res = fill_slots(m, _intent(filters=[" 违约 "]), today=TODAY)
    assert res.ok
    assert res.params["status"] == "'B'"
    res2 = fill_slots(m, _intent(filters=["逾期"]), today=TODAY)
    assert not res2.ok and res2.params is None
    assert "过滤" in res2.reason


def test_fill_filter_slot_numeric_value_renders_bare():
    """数值口径值渲染裸字面量（= 3 而非 '3'）——类型裁决在模块内，不留给亲和性
    （review Spec(c)6）。"""
    m = _parse_one(sql_template="SELECT COUNT(*) FROM loan WHERE period = {period}",
                   available_dimensions={}, time_slot=None,
                   filter_slots={"period": {"季度": 3}})
    res = fill_slots(m, _intent(filters=["季度"]), today=TODAY)
    assert res.ok and res.params["period"] == "3"


def test_fill_multiple_time_expressions_reject():
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年", "去年"], dimensions=["地区"]),
                     today=TODAY)
    assert not res.ok
    assert "时间" in res.reason


def test_fill_relative_without_today_reference_is_deterministic():
    """today 由调用方注入：纯函数不读系统时钟，同一输入永远同一输出。"""
    m = _parse_one()
    a = fill_slots(m, _intent(filters=["今年"], dimensions=["地区"]),
                   today=dt.date(2024, 1, 1))
    b = fill_slots(m, _intent(filters=["今年"], dimensions=["地区"]),
                   today=dt.date(2024, 1, 1))
    assert a.params == b.params == {
        "time_start": "'2024-01-01'", "time_end": "'2024-12-31'",
        "dimensions": "district_id",
    }


def test_fill_missing_intent_dict_returns_miss_not_crash():
    m = _parse_one()
    res = fill_slots(m, None, today=TODAY)
    assert isinstance(res, FillResult) and not res.ok


# ── 渲染（命名占位符；输出单语句 SELECT，供下游 sqlguard 校验）──────


def test_render_substitutes_named_placeholders_only():
    m = _parse_one()
    sql = render_sql(m, {"time_start": "'2023-01-01'", "time_end": "'2023-12-31'",
                         "dimensions": "district_id"})
    assert "{" not in sql
    assert "BETWEEN '2023-01-01' AND '2023-12-31'" in sql
    assert "GROUP BY district_id" in sql


def test_render_output_passes_sqlguard_single_select():
    """渲染产物必须仍以单语句 SELECT 形态过沙箱第②层——不绕过白名单假设（票面验收）。"""
    m = _parse_one()
    res = fill_slots(m, _intent(filters=["2023年"], dimensions=["地区"]),
                     today=TODAY)
    sql = render_sql(m, res.params)
    assert validate_sql(sql, allowed_tables=["loan"]) == sql.strip()
    with pytest.raises(SqlExecutionError):  # 表不在白名单即拦：模板不享特权
        validate_sql(sql, allowed_tables=["account"])


def test_render_rejects_missing_or_extra_slots():
    m = _parse_one()
    with pytest.raises(ValueError):
        render_sql(m, {"time_start": "'x'"})
    with pytest.raises(ValueError):
        render_sql(m, {"time_start": "'x'", "time_end": "'y'",
                       "dimensions": "d", "mystery": "1"})


def test_render_escapes_injected_quotes():
    """槽值经填充转义而来：恶意值劈不裂语法，渲染只做替换不做拼接发明。"""
    m = _parse_one(sql_template=_STATUS_SQL, available_dimensions={},
                   time_slot=None,
                   filter_slots={"status": {"违约": "B' or 1=1 --"}})
    res = fill_slots(m, _intent(filters=["违约"]), today=TODAY)
    assert res.ok
    sql = render_sql(m, res.params)
    assert "''" in sql  # 单引号翻倍转义
    assert validate_sql(sql, allowed_tables=["loan"]) == sql.strip()


# ── 零污染哨兵：本票不碰图/LLM/配置/沙箱入口，208 基线行为面不迁移 ────


def test_metrics_module_touches_no_graph_config_or_llm():
    """纯函数模块纪律（仿 precise 先例，票 03「不碰 LLM 不碰图」）：
    AST 级钉死 import 面——只允许标准库＋yaml；图/LLM/配置/沙箱执行入口一律禁。

    用 ast 解析 import 语句而非读源码正则（防注释/docstring 提及式假阳性）。
    sqlguard 也不列禁：裁决逻辑不做执行校验兜底，那是票 05 接沙箱入口的事。
    """
    import ast

    tree = ast.parse(inspect.getsource(metrics_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)  # 绝对导入才计（相对导入本模块不存在）
    stdlib_or_yaml = {"__future__", "re", "unicodedata", "datetime", "dataclasses",
                      "calendar", "yaml", "pathlib", "typing"}
    offenders = {m for m in imported if m.split(".")[0] not in stdlib_or_yaml}
    assert not offenders, f"metrics 模块违规导入：{offenders}"
    assert not any(m.startswith("qadata.") for m in imported)


# ── 第二级 LLM 复核输出解析（票 05：纯解析归缝 B，调用归节点）────────

def _two_metrics():
    return parse_registry(_registry(
        _full_entry(),
        _full_entry(name="loan_count", display_name="贷款笔数",
                    aliases=["贷款合同数"],
                    sql_template="SELECT COUNT(*) FROM loan"
                                 " WHERE loan.date BETWEEN {time_start} AND {time_end}",
                    available_dimensions={}),
    ))


def test_review_accepts_exact_name():
    ms = _two_metrics()
    assert metrics_mod.parse_metric_review("loan_default_rate", ms) is ms[0]


def test_review_tolerates_whitespace_and_case_only():
    """只剥空白与大小写——不加别的宽容（宁漏勿错）。"""
    ms = _two_metrics()
    assert metrics_mod.parse_metric_review("  LOAN_DEFAULT_RATE\n", ms) is ms[0]


@pytest.mark.parametrize("text", ["NONE", "none", " None ", ""])
def test_review_none_and_empty_are_miss(text):
    assert metrics_mod.parse_metric_review(text, _two_metrics()) is None


def test_review_rejects_explanation_and_display_name():
    """契约「输出只接受指标名或 NONE」：夹解释、报展示名、报不存在的名字一律判未命中。"""
    ms = _two_metrics()
    assert metrics_mod.parse_metric_review("loan_default_rate 最贴合题意", ms) is None
    assert metrics_mod.parse_metric_review("贷款违约率", ms) is None
    assert metrics_mod.parse_metric_review("some_other_metric", ms) is None
