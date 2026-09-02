from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata import run_question
from tests.conftest import RecorderLLM


def test_run_question_end_to_end(fixture_db):
    llm = FakeListChatModel(
        responses=["改写", "SELECT name FROM students WHERE id = 2", "Bob 的数学 88 分"]
    )
    ans = run_question(fixture_db, "Bob 成绩如何", llm=llm)
    assert ans.failed is False
    assert ans.conclusion == "Bob 的数学 88 分"
    assert ans.result is not None and ans.result.rows == [("Bob",)]


def test_run_question_failure_is_honest(fixture_db):
    llm = FakeListChatModel(responses=["改写", "我不会 SQL"])
    ans = run_question(fixture_db, "任意", llm=llm)
    assert ans.failed is True and "未能" in ans.conclusion


def test_evidence_reaches_sql_prompt(fixture_db):
    """证据链路：非空 evidence（业务口径）必须出现在 SQL 生成 prompt 里（prompts[1]）。

    链路为 understand→explore→generate→execute→respond，小库不触发 explore 的 LLM 选表，
    故 prompts[0]=understand、prompts[1]=generate。evidence 一旦从链路上消失，此测试必红。
    """
    recorder = RecorderLLM(["改写", "SELECT name FROM students WHERE id = 1", "结论"])
    ans = run_question(fixture_db, "q", evidence="口径：人均", llm=recorder)
    assert ans.failed is False
    assert "口径：人均" in recorder.prompts[1]


def test_run_question_absorbs_unhandled_errors():
    """失败面收敛：坏库路径在图内抛裸 OperationalError，run_question 必须兜成诚实失败答案（永不编造）。"""
    llm = FakeListChatModel(responses=["x", "SELECT 1", "y"])
    ans = run_question("no_such/missing.sqlite", "q", llm=llm)
    assert ans.failed is True
    assert ans.conclusion.startswith("未能完成查询")
    assert ans.sql is None and ans.result is None and ans.error_summary
