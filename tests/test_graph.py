from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata import run_question


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
