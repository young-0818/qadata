"""M2 起的统一测试假模型。

测试语义修正（§11.1）：FakeListChatModel 耗尽后**循环**而非报错，
导致"模型被多调了一轮"这类 bug 静默通过。ScriptedLLM 超出脚本即抛错，
所有调用次序/次数断言一律走 calls 计数。
"""


class FakeMsg:
    """最小消息对象：.content 与可选 .usage_metadata（与 timed_invoke 契约一致）。"""

    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage


class ScriptedLLM:
    """按调用次序发脚本回复的假模型。
    usage（M8 票 06 起可选）＝每回复附带的 usage_metadata dict（token 进帧的
    非零形钉用）；缺省 None＝无 usage 字段，与今日逐行为一致。"""

    def __init__(self, responses: list[str], usage: dict | None = None):
        self.responses = list(responses)
        self.usage = usage
        self.calls = 0
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(str(prompt))
        i = self.calls
        self.calls += 1
        if i >= len(self.responses):
            raise AssertionError(
                f"ScriptedLLM 第 {i + 1} 次调用超出脚本长度 {len(self.responses)}"
                "——请显式补齐脚本，勿依赖循环"
            )
        return FakeMsg(self.responses[i], self.usage)
