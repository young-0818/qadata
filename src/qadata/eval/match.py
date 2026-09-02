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


_TYPE_ORDER = {type(None): 0, int: 1, float: 1, str: 2}


def _sort_key(row: tuple) -> tuple:
    """兼容 None/数值/字符串混排的排序键：先按类型分层（None=0、数值=1、字符串=2）再比值。

    裸 sorted() 遇到 None 与数值/字符串同列混排会抛 TypeError——该错误会被评测器的
    逐题隔离吞掉，把答对的问题误记为失败（假阴性），污染准确率。
    """
    return tuple(
        (
            _TYPE_ORDER.get(type(c), 3),
            "" if c is None else (c if isinstance(c, (int, float, str)) else str(c)),
        )
        for c in row
    )


def results_match(rows_a: list[tuple], rows_b: list[tuple]) -> bool:
    def norm(rows):
        return [tuple(normalize_value(c) for c in r) for r in rows]

    return sorted(norm(rows_a), key=_sort_key) == sorted(norm(rows_b), key=_sort_key)
