from qadata.eval.match import normalize_value, results_match


def test_float_rounding():
    assert normalize_value(95.50000001) == normalize_value(95.5)


def test_int_and_float_equivalent():
    assert normalize_value(3) == normalize_value(3.0)


def test_string_normalized():
    assert normalize_value(" Alice ") == normalize_value("alice")


def test_none_preserved():
    assert normalize_value(None) is None


def test_order_insensitive_multiset():
    a = [(1, "a"), (2, "b")]
    b = [(2, "b"), (1, "a")]
    assert results_match(a, b) is True


def test_duplicates_matter():
    assert results_match([(1,)], [(1,), (1,)]) is False


def test_none_mixed_rows_are_comparable():
    # 回归：裸 sorted() 对 None 与数值/字符串混排抛 TypeError，会被逐题隔离吞成假阴性
    assert results_match([(None,), (3,)], [(3,), (None,)]) is True
    assert results_match([(None,), (3,)], [(3,)]) is False
