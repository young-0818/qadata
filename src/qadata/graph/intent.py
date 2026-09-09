"""意图结构化（载体 A，M5 票 02）：understand 同调输出的解析与 generate 尾段格式化。

纯函数模块（先例 graph/precise.py），不开图即可单测钉死。契约（CONTEXT.md「意图」）：
- 六字段：metric_mention / dimensions / filters / output_form / format_constraint / evidence_terms；
- 宁空勿造：字段仅当题面或 evidence 明示时填写，否则 null——M4-D 让步纪律在抽取层的推广；
- 解析失败回退（主设计 §3.7 既定降级语义）：纯原文当改写问题、intent 判 null，
  不写 attempts、不烧重试预算（调用方 nodes.understand 保证）。
"""
import json
import re

INTENT_FIELDS = (
    "metric_mention",
    "dimensions",
    "filters",
    "output_form",
    "format_constraint",
    "evidence_terms",
)
_LIST_FIELDS = frozenset({"dimensions", "filters", "evidence_terms"})

# 贪婪匹配最外层大括号：容忍 Markdown 围栏与前后寒暄（提取纪律同 extract_sql 的宽容风格，
# 但解析失败不抛错——意图是增强件，失败只回退，不进重试环）
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# 三约束字段进 generate 尾段（轨道②配对对象）；metric_mention/dimensions/filters
# 无 generate 消费者（归 metric_match 填槽），宁缺勿滥不注入。
_CONSTRAINT_LABELS = (
    ("output_form", "输出形态"),
    ("format_constraint", "格式约束"),
    ("evidence_terms", "背景信息语义规定"),
)


def _clean_str(value):
    """非空字符串→strip 后返回，否则 None（宁空勿造的解析层形态）。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_list(value):
    """字符串列表清洗：标量收编为单元素、丢弃空白与非标量元素；全空→None。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    items = []
    for v in value:
        if isinstance(v, str) and v.strip():
            items.append(v.strip())
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            items.append(str(v))  # 数字过滤器（2023）无损收编，非发明
    return items or None


def parse_understand_response(text):
    """understand 回复 → (改写问题, 六字段意图 dict 或 None)。

    成功判定：能提取出带非空 question 的 JSON 对象。意图契约坏掉（intent 缺失/非 dict）
    但改写拿到了时六字段全 null——回退成 JSON 原文反而污染题面，不如留空；
    question 拿不到＝整体失败：原文当改写问题、意图判 None（行为与接线前逐字节一致）。
    """
    raw = str(text).strip()
    m = _JSON_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            question = _clean_str(data.get("question"))
            if question:
                src = data.get("intent")
                src = src if isinstance(src, dict) else {}
                intent = {
                    f: (_clean_list(src.get(f)) if f in _LIST_FIELDS
                        else _clean_str(src.get(f)))
                    for f in INTENT_FIELDS
                }
                return question, intent
    return raw, None


def format_intent_constraints(intent):
    """意图三约束字段 → generate prompt 动态尾段；None/全空返回 ""（逐字节一致保障）。

    尾段只放题面/evidence 明示过的内容——不改变 SYSTEM_RULES 前端，不新增推断。
    """
    if not intent:
        return ""
    lines = []
    for field, label in _CONSTRAINT_LABELS:
        value = intent.get(field)
        if not value:
            continue
        rendered = "；".join(value) if isinstance(value, list) else value
        lines.append(f"- {label}：{rendered}")
    if not lines:
        return ""
    return ("\n## 题面明示约束（从题面/背景信息抽取，必须遵守；未明示的项不要自行添加）\n"
            + "\n".join(lines) + "\n")
