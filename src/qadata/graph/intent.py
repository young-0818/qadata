"""意图结构化（载体 A，M5 票 02）：understand 同调输出的解析。

纯函数模块（先例 graph/precise.py），不开图即可单测钉死。契约（CONTEXT.md「意图」）：
- 四字段：dimensions / filters / output_form / format_constraint
  （metric_mention 随 M5 退役 ADR-0007；evidence_terms 随 evidence 层退役 ADR-0008）；
- 宁空勿造：字段仅当题面明示时填写，否则 null——M4-D 让步纪律在抽取层的推广；
- 解析失败回退（主设计 §3.7 既定降级语义）：纯原文当改写问题、intent 判 null，
  不写 attempts、不烧重试预算（调用方 nodes.understand 保证）。

2026-09-09 票 02 条款④判负裁决（owner 签字）：三约束字段「注入 generate 尾段」的
软用途验效不过（翻转不可复现、注入信号 ≤ 载体 A 改写漂移噪声带，见 02 票判卷结论），
该注入线已拆除不再恢复。意图消费者现状＝值链搭车抽词 filters（M10 票 03）；
metric_match 填槽消费者已随指标层退役（ADR-0007）；evidence_terms 展示消费者
已随 evidence 层退役（ADR-0008，respond 口径说明改读字典召回块）。「勿再喂 prompt」
纪律不破（test_generate_never_reads_intent 仍钉死）。

M8 票 03：返回值扩为三元组（＋clarification），默认关的保险丝——仅当口径缺失到
「任何 SQL 都是猜」时模型才产澄清问，宁空勿造；消费闸在节点侧（flag＋防循环标记），
本解析层只负责「收不收」的形状判定。
"""
import json
import re

INTENT_FIELDS = (
    "dimensions",
    "filters",
    "output_form",
    "format_constraint",
)
_LIST_FIELDS = frozenset({"dimensions", "filters"})

# 贪婪匹配最外层大括号：容忍 Markdown 围栏与前后寒暄（提取纪律同 extract_sql 的宽容风格，
# 但解析失败不抛错——意图是增强件，失败只回退，不进重试环）
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


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
    """understand 回复 → (改写问题, 四字段意图 dict 或 None, 澄清问句或 None)。

    成功判定：能提取出带非空 question 的 JSON 对象。意图契约坏掉（intent 缺失/非 dict）
    但改写拿到了时四字段全 null——回退成 JSON 原文反而污染题面，不如留空；
    question 拿不到＝整体失败：原文当改写问题、意图判 None（行为与接线前逐字节一致）。

    M8 票 03 第三元（澄清保险丝，宁空勿造在解析层的形态）：仅当 JSON 对象里
    clarification 为非空字符串才收——键缺失/空串/空白/非字符串一律 None，
    纯文本与坏 JSON 回退态同样 None（半坏契约不采信）；与 question/intent 判定
    正交（模型丢改写只留澄清问也收，宁回问不拿 JSON 原文喂 generate）。
    """
    raw = str(text).strip()
    m = _JSON_RE.search(raw)
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            clarification = _clean_str(data.get("clarification"))
            question = _clean_str(data.get("question"))
            if question:
                src = data.get("intent")
                src = src if isinstance(src, dict) else {}
                intent = {
                    f: (_clean_list(src.get(f)) if f in _LIST_FIELDS
                        else _clean_str(src.get(f)))
                    for f in INTENT_FIELDS
                }
                return question, intent, clarification
            return raw, None, clarification
    return raw, None, None
