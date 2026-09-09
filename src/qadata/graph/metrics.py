"""指标层纯函数（M5 票 03）：注册表加载校验／一级匹配／填槽／模板渲染。

仿 precise 先例的纯函数模块（spec 缝 B）——不碰 LLM、不碰图，不开图即可钉死单测；
指标层全部裁决逻辑集中在本模块（spec 故事 15）。节点接线与第二级 LLM 复核在票 05。

契约（spec「注册表契约」＋ CONTEXT.md 词汇表）：
- **六要素＋aliases**：名称 name／展示名 display_name／业务含义 meaning／
  指标口径 definition／SQL 模板＋参数槽 sql_template（＋time_slot／filter_slots／
  available_dimensions 声明）／血缘 source_tables，另加检索键 aliases。
  缺任一项＝加载期拒绝并明确报错（RegistryError），不带病运行、不静默降级。
- **第一级匹配＝确定性归一化**（小写／去空白／全半角）后对 aliases∪display_name
  精确或包含匹配；包含路径命中多条＝歧义判未命中（宁漏勿错，交第二级 LLM 复核）。
- **填槽＝宁空勿造**：时间槽按注册表声明的日期列格式转换（YYYY-MM-DD 文本／
  YYMMDD 整数两类）；任一槽填不出（时间解析失败／维度不在可用维度内／过滤条件
  映射不到声明值）→ 判未命中走兜底。`today` 由调用方注入（纯函数不读时钟）。
- **模板渲染＝命名占位符** `{slot}`：不支持条件／循环语法（加载期即拒）；
  渲染产物仍以单语句 SELECT 形态走沙箱四层唯一入口，不绕过 sqlguard 白名单假设。
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# 时间槽可声明的日期格式（两类形态。票 04 实库取证：financial 五个日期列全为文本
# YYYY-MM-DD——描述 CSV 声称的 account.date/card.issued YYMMDD 与库实际不符，YYMMDD 留给真存该形态的库）
DATE_FORMATS = ("YYYY-MM-DD", "YYMMDD")

# 时间槽渲染出的占位符名（声明↔模板对账与填槽共用同一份，不两处各写各的）
TIME_START, TIME_END = "time_start", "time_end"

# 槽名＝合法 Python 标识符样式的小写占位符；模板里除 {slot} 外不得出现花括号
_PLACEHOLDER_FILL = re.compile(r"\{([A-Za-z_]\w*)\}")
_PLACEHOLDER_STRIP = re.compile(r"\{[A-Za-z_]\w*\}")
_SLOT_NAME = re.compile(r"^[a-z_]\w*$")

# 时间表达可接受形态（归一化后）：全日期／年月／年／相对词／区间（到、至、~）
_DATE_FULL = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$")
_DATE_MONTH = re.compile(r"(\d{4})[-/年](\d{1,2})月?$")
_DATE_YEAR = re.compile(r"(\d{4})年?$")
_RELATIVE_YEARS = {"今年": 0, "明年": 1, "去年": -1, "前年": -2}
_RANGE = re.compile(r"^(.+?)\s*(?:到|至|~|—)\s*(.+)$")


class RegistryError(Exception):
    """注册表加载/校验失败：缺要素、槽声明与模板不一致、撞车检索键等——拒绝加载。"""


@dataclass(frozen=True)
class TimeSlot:
    column: str          # 目标日期列（如 loan.date）——票面：时间槽必须声明日期列
    date_format: str     # DATE_FORMATS 之一：决定填入模板的字面量形态


@dataclass(frozen=True)
class FilterSlot:
    name: str  # 占位符名（模板里的 {name}）
    # 值标签→口径值（人工审核）：数值标量渲染为裸字面量，字符串带引号——
    # 与时间槽「按列形态产出字面量」同一条纪律，不把类型裁决甩给 SQLite 亲和性
    values: dict[str, str | float] = field(compare=False)


@dataclass(frozen=True)
class Metric:
    name: str
    display_name: str
    meaning: str
    definition: str
    sql_template: str
    aliases: tuple[str, ...]
    available_dimensions: dict[str, str] = field(compare=False)  # 维度标签→列名
    source_tables: tuple[str, ...] = ()
    time_slot: TimeSlot | None = None
    filter_slots: tuple[FilterSlot, ...] = ()
    # 模板占位符集合：加载期解析一次，填槽/渲染共用（不二次发明）
    placeholders: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FillResult:
    """填槽裁决：ok=False 即判未命中（走兜底），reason 供 metric_note/排障。"""
    ok: bool
    params: dict[str, str] | None = None
    reason: str = ""


# ── 归一化与第一级匹配 ────────────────────────────────────────────


def normalize(text: Any) -> str:
    """确定性归一：全半角（NFKC）→ 小写 → 去所有空白。匹配与撞车检测共用。"""
    s = unicodedata.normalize("NFKC", str(text)).lower()
    return "".join(s.split())


def _search_keys(m: Metric) -> tuple[str, ...]:
    return (m.display_name,) + tuple(m.aliases)


def match_metric(mention: str | None, metrics: list[Metric]) -> Metric | None:
    """两级匹配的第一级（spec「匹配机制」）：归一化后精确或包含命中 aliases∪展示名。

    精确优先；包含路径多候选＝歧义判 None（票 05 的 LLM 复核是兜网，不是本级的遮羞布）。
    """
    if not isinstance(mention, str) or not (n := normalize(mention)):
        return None
    exact, inclusion = [], []
    for m in metrics:
        keys = tuple(normalize(k) for k in _search_keys(m))
        if n in keys:
            exact.append(m)
        elif any(k in n or n in k for k in keys):
            inclusion.append(m)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None  # 加载期已查撞车；理论不可达，出现即判未命中（宁漏勿错）
    return inclusion[0] if len(inclusion) == 1 else None


# ── 加载与六要素校验 ──────────────────────────────────────────────


def load_registry(path: str | Path) -> list[Metric]:
    """读取并校验注册表 YAML（`metrics/*.yaml` 入口；夹具测试走 parse_registry）。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryError(f"注册表文件读取失败 {p}：{e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise RegistryError(f"注册表 YAML 解析失败 {p}：{e}") from e
    return parse_registry(data, source=str(p))


def parse_registry(data: Any, *, source: str = "注册表") -> list[Metric]:
    """从已解析的 YAML 结构构造注册表（契约形态：顶层 'metrics:' 映射）；
    任一条目不过校验即整体拒绝。"""
    entries = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RegistryError(
            f"{source}：注册表须为顶层 'metrics:' 列表，收到 {type(data).__name__}"
        )
    metrics: list[Metric] = []
    names: set[str] = set()
    keys_owner: dict[str, str] = {}
    for entry in entries:
        m = _parse_entry(entry, source, len(metrics) + 1)
        if m.name in names:
            raise RegistryError(f"{source}：指标名 '{m.name}' 重复，注册表必须唯一寻址")
        names.add(m.name)
        for k in _search_keys(m):
            nk = normalize(k)
            if nk in keys_owner:
                raise RegistryError(
                    f"{source}：检索键 '{k}' 在指标 '{keys_owner[nk]}' 与 '{m.name}' 间撞车，"
                    "第一级匹配将产生歧义"
                )
            keys_owner[nk] = m.name
        metrics.append(m)
    return metrics


def _req_text(entry: dict, key: str, where: str) -> str:
    v = entry.get(key)
    if v is None:
        raise RegistryError(f"{where}：缺少六要素字段 '{key}'，拒绝加载（不带病运行）")
    if not isinstance(v, str) or not v.strip():
        raise RegistryError(f"{where}：字段 '{key}' 须为非空字符串")
    return v.strip()


def _parse_entry(entry: Any, source: str, idx: int) -> Metric:
    if not isinstance(entry, dict):
        raise RegistryError(f"{source}：第 {idx} 条指标不是映射，无法校验六要素")
    name = entry.get("name")
    where = f"{source}：指标 {name!r}" if isinstance(name, str) and name.strip() \
        else f"{source}：第 {idx} 条指标"

    name = _req_text(entry, "name", where)
    display = _req_text(entry, "display_name", where)
    meaning = _req_text(entry, "meaning", where)
    definition = _req_text(entry, "definition", where)
    sql_template = _req_text(entry, "sql_template", where)

    # aliases 可空（契约：匹配键＝aliases∪展示名，display_name 单独即可寻址）；血缘表不可空
    aliases = _req_str_list(entry, "aliases", where, non_empty=False)
    source_tables = tuple(_req_str_list(entry, "source_tables", where, non_empty=True))

    if "available_dimensions" not in entry:
        raise RegistryError(f"{where}：缺少六要素字段 'available_dimensions'，"
                            "拒绝加载（可为空映射但必须显式声明）")
    dims_raw = entry["available_dimensions"]
    if not isinstance(dims_raw, dict):
        raise RegistryError(f"{where}：'available_dimensions' 须为 维度标签→列名 映射")
    available_dimensions: dict[str, str] = {}
    for label, col in dims_raw.items():
        if not isinstance(label, str) or not label.strip() \
                or not isinstance(col, str) or not col.strip():
            raise RegistryError(f"{where}：可用维度 '{label}' 的声明形态非法（标签与列名均须非空字符串）")
        available_dimensions[label.strip()] = col.strip()

    time_slot = _parse_time_slot(entry.get("time_slot"), where)
    filter_slots = _parse_filter_slots(entry.get("filter_slots"), where)

    placeholders = frozenset(_PLACEHOLDER_FILL.findall(sql_template))
    _check_template(sql_template, placeholders, time_slot, filter_slots,
                    bool(available_dimensions), where)

    return Metric(
        name=name, display_name=display, meaning=meaning, definition=definition,
        sql_template=sql_template, aliases=tuple(aliases),
        available_dimensions=available_dimensions, source_tables=source_tables,
        time_slot=time_slot, filter_slots=filter_slots,
        placeholders=placeholders,
    )


def _req_str_list(entry: dict, key: str, where: str, *, non_empty: bool) -> list[str]:
    v = entry.get(key)
    if v is None:
        raise RegistryError(f"{where}：缺少字段 '{key}'，拒绝加载（六要素＋aliases 缺一不可）")
    if not isinstance(v, list) or not all(isinstance(i, str) and i.strip() for i in v):
        raise RegistryError(f"{where}：字段 '{key}' 须为字符串列表")
    if non_empty and not v:
        raise RegistryError(f"{where}：字段 '{key}' 不得为空（空注册项没有存在意义）")
    return [i.strip() for i in v]


def _slot_mismatch(placeholders: set[str] | frozenset[str],
                   params: dict[str, str]) -> str:
    """填槽/渲染共用的对账话术（两处裁决同一判据）。通过返回空串。"""
    missing = sorted(placeholders - set(params))
    extra = sorted(set(params) - placeholders)
    bits = [f"缺 {m}" for m in missing] + [f"多 {x}" for x in extra]
    return "；".join(bits)


def _parse_time_slot(ts: Any, where: str) -> TimeSlot | None:
    if ts is None:
        return None
    if not isinstance(ts, dict):
        raise RegistryError(f"{where}：'time_slot' 须为 {{column, date_format}} 映射")
    column = ts.get("column")
    if not isinstance(column, str) or not column.strip():
        raise RegistryError(f"{where}：时间槽必须声明目标日期列（time_slot.column）")
    fmt = ts.get("date_format")
    if not isinstance(fmt, str) or fmt.strip() not in DATE_FORMATS:
        raise RegistryError(
            f"{where}：date_format '{fmt}' 不受支持（可声明形态：{'、'.join(DATE_FORMATS)}）"
        )
    return TimeSlot(column=column.strip(), date_format=fmt.strip())


def _parse_filter_slots(fs: Any, where: str) -> tuple[FilterSlot, ...]:
    if fs is None:
        return ()
    if not isinstance(fs, dict):
        raise RegistryError(f"{where}：'filter_slots' 须为 槽名→{{值标签: 值}} 映射")
    slots: list[FilterSlot] = []
    for slot_name, mapping in fs.items():
        if not isinstance(slot_name, str) or not _SLOT_NAME.match(slot_name):
            raise RegistryError(f"{where}：过滤槽名 '{slot_name}' 须为小写标识符（模板占位符用）")
        if not isinstance(mapping, dict) or not mapping:
            raise RegistryError(f"{where}：过滤槽 '{slot_name}' 须为非空 值标签→值 映射")
        values: dict[str, str | float] = {}
        for label, v in mapping.items():
            if not isinstance(label, str) or not label.strip():
                raise RegistryError(f"{where}：过滤槽 '{slot_name}' 的值标签非法：{label!r}")
            if isinstance(v, bool) or not isinstance(v, (str, int, float)) \
                    or not str(v).strip():
                raise RegistryError(f"{where}：过滤槽 '{slot_name}' 的值缺失：{label!r}")
            values[label.strip()] = v.strip() if isinstance(v, str) else v
        slots.append(FilterSlot(name=slot_name, values=values))
    return tuple(slots)


def _check_template(sql_template: str, placeholders: frozenset[str],
                    time_slot: TimeSlot | None,
                    filter_slots: tuple[FilterSlot, ...],
                    has_dimensions: bool, where: str) -> None:
    """声明↔模板双向对账：静默丢失的口径条件＝带病运行，加载期一律拒绝。"""
    if "{" in _PLACEHOLDER_STRIP.sub("", sql_template) \
            or "}" in _PLACEHOLDER_STRIP.sub("", sql_template):
        raise RegistryError(
            f"{where}：模板含非命名占位符的花括号（条件/循环语法不支持，仅 {{槽名}} 形式）"
        )
    if time_slot is not None and time_slot.column not in sql_template:
        raise RegistryError(
            f"{where}：时间槽声明的目标日期列 '{time_slot.column}' 未原样出现在模板中——"
            "模板里的时间过滤必须写全限定的声明列（列声明不许是装饰）"
        )
    used = set(placeholders)
    declared = {s.name for s in filter_slots}
    if time_slot is not None:
        declared |= {TIME_START, TIME_END}
    if has_dimensions:
        declared.add("dimensions")
    for slot in sorted(used - declared):
        raise RegistryError(
            f"{where}：模板使用未声明参数槽 '{{{slot}}}'（须有对应 time_slot/"
            "filter_slots/available_dimensions 声明）"
        )
    for slot in sorted(declared - used):
        raise RegistryError(
            f"{where}：参数槽 '{{{slot}}}' 已声明但模板未使用——口径条件静默丢失＝带病运行"
        )


# ── 填槽（时间/维度/过滤；任一槽填不出＝判未命中——宁空勿造）───────


_NOT_TIME = object()


class _TimeSlotError(ValueError):
    """时间槽填充期错误（解析失败/非法日期/格式转换不了）＝判未命中素材；
    与加载期的 RegistryError 区分——归属不同（纪律③）。"""


def _parse_point(token: str, today: date) -> tuple[date, date] | object:
    """单点时间表达 → (start, end)；解析不出返回 _NOT_TIME 哨兵。"""
    if token in _RELATIVE_YEARS:
        y = today.year + _RELATIVE_YEARS[token]
        return date(y, 1, 1), date(y, 12, 31)
    if (m := _DATE_FULL.match(token)):
        y, mo, d = (int(g) for g in m.groups())
        try:
            day = date(y, mo, d)
        except ValueError:
            raise _TimeSlotError(f"日期不存在：{token}") from None
        return day, day
    if (m := _DATE_MONTH.match(token)):
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise _TimeSlotError(f"日期不合法（月份越界）：{token}")
        return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    if (m := _DATE_YEAR.match(token)):
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    return _NOT_TIME


def _parse_time_token(token: str, today: date) -> tuple[date, date] | object:
    """filters 元素 → 时间区间 (start, end)；非时间形态返回 _NOT_TIME 哨兵；
    时间范围残缺/倒置 → _TimeSlotError（写了时间就得解得开，不许悄悄丢弃）。"""
    if (rm := _RANGE.match(token)):
        left = _parse_point(rm.group(1).strip(), today)
        right = _parse_point(rm.group(2).strip(), today)
        if left is _NOT_TIME or right is _NOT_TIME:
            raise _TimeSlotError(f"时间范围残缺：{token}")
        if left[0] > right[1]:
            raise _TimeSlotError(f"时间范围倒置：{token}")
        return left[0], right[1]
    return _parse_point(token, today)


def _format_date_literal(d: date, fmt: str) -> str:
    """按注册表声明的日期列格式产出 SQL 字面量——文本带引号，整数（YYMMDD）裸值。"""
    if fmt == "YYYY-MM-DD":
        return f"'{d.isoformat()}'"
    if fmt == "YYMMDD":
        if d.year < 2000:
            raise _TimeSlotError(
                f"日期 {d.isoformat()} 无法映射到 YYMMDD（双位年仅 2000 年后无歧义）"
            )
        return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"
    raise _TimeSlotError(f"不支持的日期格式声明：{fmt}")


def _slot_literal(value: str | float) -> str:
    """过滤槽值 → SQL 字面量：数值标量裸值、字符串带引号转义——与 _format_date_literal
    同一条纪律（列形态决定字面量形态），不把类型裁决甩给 SQLite 亲和性。bool 加载期已拒。"""
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def fill_slots(metric: Metric, intent: dict | None, *,
               today: date) -> FillResult:
    """意图六字段 → 模板参数槽填充（消费契约见票 05：读 metric_mention/dimensions/filters）。

    宁空勿造的裁决形态：任一槽填不出＝整体判未命中——
    - 时间槽：声明了就必须从 filters 里解析出恰好一个时间区间；解析不出、倒置、残缺、
      或转换失败（如 1993 年之于 YYMMDD 声明）→ 未命中；
    - 维度：dimensions 逐项必须在 available_dimensions 内（归一化精确），越界→未命中；
      模板含 {dimensions} 而题面没给维度＝该槽填不出→未命中（不悄悄渲染成空）；
    - 过滤槽：非时间 filters 逐项必须命中某声明槽的值标签；命中 0 个/跨槽歧义/
      同槽多值 → 未命中（= 单值语义承载不了）。
    """
    if not isinstance(intent, dict):
        return FillResult(False, None, "无可用意图（understand 解析失败回退态）")
    dims = [str(d) for d in (intent.get("dimensions") or []) if str(d).strip()]
    filters = [str(f) for f in (intent.get("filters") or []) if str(f).strip()]
    params: dict[str, str] = {}

    # ── 时间槽：filters 里至多一个时间表达（区间/点/相对词）──
    parsed: list[tuple[date, date]] = []
    non_time: list[str] = []
    try:
        for f in filters:
            tok = normalize(f)
            span = _parse_time_token(tok, today)
            if span is _NOT_TIME:
                non_time.append(f)
            else:
                parsed.append(span)
        if metric.time_slot is not None:
            if not parsed:
                return FillResult(False, None,
                                  "指标声明了时间槽，题面却无时间范围可填（宁空勿造）")
            if len(parsed) > 1:
                return FillResult(False, None, "题面含多个时间表达，模板时间槽无法裁决")
            start, end = parsed[0]
            params = {
                TIME_START: _format_date_literal(start, metric.time_slot.date_format),
                TIME_END: _format_date_literal(end, metric.time_slot.date_format),
            }
        elif parsed:
            return FillResult(False, None, "指标无时间槽但题面带时间约束")

        # ── 维度槽 ──
        if "dimensions" in metric.placeholders:
            if not dims:
                return FillResult(False, None, "模板需要分组维度而题面未给出（宁空勿造）")
            norm_map = {normalize(k): v for k, v in metric.available_dimensions.items()}
            cols: list[str] = []
            for d in dims:
                col = norm_map.get(normalize(d))
                if col is None:
                    return FillResult(False, None, f"维度越界：'{d}' 不在可用维度内")
                if col not in cols:
                    cols.append(col)
            params["dimensions"] = ", ".join(cols)
        elif dims:
            return FillResult(False, None, "指标不支持维度分组而题面要求分组")

        # ── 过滤槽 ──
        slots = {s.name: s for s in metric.filter_slots}
        slot_owners: dict[str, str | float] = {}
        for f in non_time:
            tok = normalize(f)
            hits = [(s.name, value) for s in slots.values()
                    for label, value in s.values.items() if normalize(label) == tok]
            if not hits:
                return FillResult(False, None, f"过滤条件 '{f}' 无法映射到任何声明值")
            hit_slots = {name for name, _ in hits}
            if len(hit_slots) > 1:
                return FillResult(False, None, f"过滤条件 '{f}' 跨槽歧义")
            name, value = hits[0]
            if name in slot_owners:
                return FillResult(False, None, f"过滤槽 '{name}' 出现多值，单值语义承载不了")
            slot_owners[name] = value
        for name, value in slot_owners.items():
            params[name] = _slot_literal(value)
    except _TimeSlotError as e:
        return FillResult(False, None, str(e))

    if mismatch := _slot_mismatch(metric.placeholders, params):
        return FillResult(False, None, f"参数槽与模板占位符不齐：{mismatch}")
    return FillResult(True, params)


# ── 模板渲染（命名占位符；产物保持单语句 SELECT 形态交给沙箱）───────


def render_sql(metric: Metric, params: dict[str, str]) -> str:
    """{槽名} → 参数值（值已由填槽产出为字面量/列清单，本函数只做替换）。

    不发明 SQL：缺槽/多槽即报错；渲染产物照常过沙箱四层（票 05 接线），
    不存在绕过 sqlguard 白名单的旁路。
    """
    if mismatch := _slot_mismatch(metric.placeholders, params):
        raise ValueError(f"模板渲染参数不齐：{mismatch}")

    def _sub(m: re.Match) -> str:
        return params[m.group(1)]

    return _PLACEHOLDER_FILL.sub(_sub, metric.sql_template)
