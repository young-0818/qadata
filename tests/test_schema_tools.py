from qadata.tools.schema import build_schema_context, get_schema, list_tables, sample_rows


class FakeLLM:
    """只测接口约定：invoke(str) 返回带 .content 的对象；记录调用次数以证实分支被执行。"""

    def __init__(self, content):
        self.content = content
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        return self


def test_list_tables(fixture_conn):
    assert list_tables(fixture_conn) == ["scores", "students"]  # sqlite_master 按名字序


def test_get_schema_contains_ddl_and_columns(fixture_conn):
    ddl = get_schema(fixture_conn, "students")
    assert "CREATE TABLE students" in ddl
    for col in ("id", "name", "grade"):
        assert col in ddl


def test_sample_rows_readable(fixture_conn):
    text = sample_rows(fixture_conn, "students", n=2)
    assert "Alice" in text and "Bob" in text


def test_build_schema_context_small_db_full(fixture_conn):
    ctx = build_schema_context(fixture_conn, "成绩最好的学生是谁", llm=None)
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_build_schema_context_large_db_uses_llm(fixture_conn):
    # 显式传 max_chars 触发 LLM 分支（不 monkeypatch 常量：函数默认值在 def 时绑定，patch 不生效）
    fake = FakeLLM("students, scores")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1  # LLM 确实被调用
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_build_schema_context_llm_garbage_falls_back(fixture_conn):
    fake = FakeLLM("这些表不存在的回答")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1
    # 解析失败 → 回退全量：两张表都在
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_llm_selection_result_is_used(fixture_conn):
    """LLM 只选 students 时，scores 不得出现在上下文中——证明选表结果真正被使用。"""
    fake = FakeLLM("students")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" not in ctx
