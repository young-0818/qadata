"""图端到端组件测试（M2：自纠错闭环）。一律 ScriptedLLM＋calls 断言。"""
from qadata import run_question
from qadata.config import Settings
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="", retry_budget=3)


def test_happy_path(fixture_db):
    llm = ScriptedLLM(["改写", "SELECT name FROM students WHERE id = 2", "Bob 的数学 88 分"])
    ans = run_question(fixture_db, "Bob 成绩如何", llm=llm, settings=_S)
    assert ans.failed is False
    assert ans.conclusion.startswith("【结论】Bob 的数学 88 分")  # 票 07：LLM 文本入结论节
    assert "【数据依据】" in ans.conclusion and "所用表：students" in ans.conclusion
    assert "【口径说明】" not in ans.conclusion  # 无口径来源→节省略（不空转）
    assert "【校验标注】" not in ans.conclusion  # 无标注→省略
    assert ans.result.rows == [("Bob",)]
    assert llm.calls == 3  # understand + generate + respond（explore/verify 不烧 token）


def test_execute_error_then_retry_success(fixture_db):
    """条件边①：首轮坏 SQL → 带失败历史重试 → 成功。"""
    llm = ScriptedLLM([
        "改写",
        "SELECT nope FROM students",           # 轮 1：列不存在
        "SELECT name FROM students WHERE id = 1",  # 轮 2：修正
        "Alice 成绩最好",
    ])
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert ans.failed is False and ans.conclusion.startswith("【结论】Alice 成绩最好")
    assert llm.calls == 4
    # 轮 2 的 generate prompt 必须带失败历史（上下文工程真正生效的证据）
    assert "之前的失败尝试" in llm.prompts[2] and "no such column" in llm.prompts[2]


def test_budget_exhausted_reports_all_attempts(fixture_db):
    """预算耗尽：诚实兜底，汇报全部尝试。"""
    llm = ScriptedLLM(["改写"] + ["SELECT nope FROM students"] * 3)
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert ans.failed is True
    assert "共尝试 3 次" in ans.conclusion
    assert "no such column" in ans.conclusion
    assert llm.calls == 4  # understand + 3 次 generate（respond 失败路径不调 LLM）


def test_verify_suspicious_empty_then_retry(fixture_db):
    """条件边②：空结果可疑 → 换思路重试 → 成功。"""
    llm = ScriptedLLM([
        "改写",
        "SELECT name FROM students WHERE id = 999",  # 轮 1：空结果
        "SELECT name FROM students WHERE id = 1",    # 轮 2：修正
        "Alice",
    ])
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert ans.failed is False
    assert llm.calls == 4
    assert "校验未通过" in llm.prompts[2]  # 失败历史标注了校验原因


def test_verify_suspicious_exhausted_annotates(fixture_db):
    """可疑且预算耗尽：数据真实，作答但带校验标注（不是假失败）。"""
    llm = ScriptedLLM(
        ["改写"] + ["SELECT name FROM students WHERE id = 999"] * 3 + ["没有人"]
    )
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert ans.failed is False  # 有真实结果（空集）
    assert "未通过自动校验" in ans.conclusion
    assert llm.calls == 5  # understand + 3×generate + respond


def test_generate_failure_also_consumes_budget(fixture_db):
    """提取不出 SQL 也计入预算（attempts 账本完整性）。"""
    llm = ScriptedLLM(["改写"] + ["我不会写 SQL"] * 3)
    ans = run_question(fixture_db, "q", llm=llm, settings=_S)
    assert ans.failed is True and "共尝试 3 次" in ans.conclusion


def test_extraction_failure_after_success_does_not_reuse_stale_result(fixture_db):
    """终审阻断回归：轮 1 成功但 verify 可疑 → 轮 2 起提取失败，不得复用陈旧 result 路由，
    预算耗尽后必须是诚实失败（failed=True/sql=None/result=None），而非带陈旧数据的假成功。"""
    llm = ScriptedLLM([
        "改写",
        "SELECT name FROM students WHERE id = 999",  # 轮 1：空结果 → verify 可疑 → 重试
        "我不会写 SQL",                              # 轮 2：提取失败
        "我还是不会",                                # 轮 3：提取失败，预算耗尽
    ])
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert ans.failed is True
    assert ans.sql is None and ans.result is None
    assert "共尝试 3 次" in ans.conclusion
    assert llm.calls == 4  # understand + 3×generate（respond 失败路径不调 LLM）


def test_failure_is_honest_no_fabrication(fixture_db):
    llm = ScriptedLLM(["改写", "对不起，我回答不了"])
    ans = run_question(fixture_db, "任意", llm=llm, settings=_S)
    assert ans.failed is True and "未能完成查询" in ans.conclusion


# （M1 钉「evidence 进 SQL prompt」已随 ADR-0008 退役删除——口径通道＝字典检索块，其
#   落位与零注入面钉在 tests/test_knowledge_recall.py / test_gssc.py 防回吹钉。）

def test_run_question_absorbs_unhandled_errors():
    """失败面收敛（M1 钉死）：坏库路径的异常必须兜成诚实失败答案（永不编造）。"""
    llm = ScriptedLLM(["x", "SELECT 1", "y"])
    ans = run_question("no_such/missing.sqlite", "q", llm=llm, settings=_S)
    assert ans.failed is True
    assert ans.conclusion.startswith("未能完成查询")
    assert ans.sql is None and ans.result is None and ans.error_summary


def test_budget_exhausted_exec_fail_is_honest_failure(fixture_db):
    """判死裁决（owner 2026-09-22，预算收紧为 2＝首次＋一次补考）：补考也执行失败＝
    诚实失败——不回滚旧答案救场，失败路径不调 LLM（永不编造原样）。"""
    s = Settings(api_key="", base_url="", model="", retry_budget=2)
    llm = ScriptedLLM([
        "改写",
        "SELECT AVG(score) FROM scores WHERE student_id = 999",  # 份 1：成功但聚合 NULL → 可疑
        "SELECT nope1 FROM students",                            # 份 2：执行失败，预算耗尽
    ])
    ans = run_question(fixture_db, "谁成绩最好", llm=llm, settings=s)
    assert ans.failed is True
    assert ans.result is None
    assert "未能完成查询（共尝试 2 次）" in ans.conclusion
    assert llm.calls == 3  # understand + generate×2；respond 失败路径不调 LLM


def test_list_all_empty_not_retried(fixture_db):
    """M3 靶子回归：列出全部类空结果不判可疑——不触发重试，首轮答案直接保留
    （457/1309/1330「verify 假阳性→重试改坏」形态被治本堵住）。
    注意 understand 改写必须保留列出类措辞——verify 按改写后问题判断。"""
    llm = ScriptedLLM(["列出所有不及格的学生", "SELECT name FROM students WHERE grade = 99", "没有这样的学生"])
    ans = run_question(fixture_db, "列出所有不及格的学生", llm=llm, settings=_S)
    assert ans.failed is False
    assert "没有这样的学生" in ans.conclusion
    assert llm.calls == 3  # understand + generate + respond，无重试
