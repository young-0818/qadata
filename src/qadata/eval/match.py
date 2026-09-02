"""结果集匹配：BIRD 执行准确率的比较逻辑（顺序无关多重集）。"""


def normalize_value(v):
    """数值统一按 float 保留 4 位；字符串 strip+lower；None 保留。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return str(v).strip().lower()


def results_match(rows_a: list[tuple], rows_b: list[tuple]) -> bool:
    norm = lambda rows: sorted(tuple(normalize_value(c) for c in r) for r in rows)
    return norm(rows_a) == norm(rows_b)
