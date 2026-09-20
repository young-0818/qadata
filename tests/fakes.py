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


class FakeChunk:
    """流式最小 chunk：.content／.additional_kwargs（含 reasoning_content）／.usage_metadata
    （与 ReasoningChatOpenAI + timed_stream 消费的 real chunk 契约一致）。"""

    def __init__(self, content="", reasoning=None, usage=None):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.usage_metadata = usage


def _slice(text: str, size: int = 40):
    """把整段文本切成固定宽度的 chunk（有损为 0：拼接还原即原文）——让流式路径
    真正经过逐 chunk 累积与截断逻辑，而非退化成单次全量。"""
    for i in range(0, len(text), size):
        yield text[i:i + size]


class ScriptedLLM:
    """按调用次序发脚本回复的假模型。
    usage（M8 票 06 起可选）＝每回复附带的 usage_metadata dict（token 进帧的
    非零形钉用）；缺省 None＝无 usage 字段，与今日逐行为一致。
    reasonings（M8 票 08 起可选）＝与 responses 等长的思考文本 list[str|None]：
    仅 stream() 按 chunk 吐出成 thinking 增量（invoke() 不看它＝思考只在流式路活）；
    缺省 None＝无思考，stream 与 invoke 内容一致（既有 on_event 测试全序列零扰动）。"""

    def __init__(self, responses: list[str], usage: dict | None = None,
                 reasonings: list[str | None] | None = None):
        self.responses = list(responses)
        self.usage = usage
        self.reasonings = (list(reasonings) if reasonings is not None
                           else [None] * len(self.responses))
        self.calls = 0
        self.prompts: list[str] = []
        self.stream_used = 0  # 关态姊妹钉：on_event=None 时必须恒 0（流式旁路未被触发）

    def _next(self, prompt) -> int:
        """invoke/stream 共用的记账：收 prompt、推进 calls、越界显式报错（纪律 #5）。"""
        self.prompts.append(str(prompt))
        i = self.calls
        self.calls += 1
        if i >= len(self.responses):
            raise AssertionError(
                f"ScriptedLLM 第 {i + 1} 次调用超出脚本长度 {len(self.responses)}"
                "——请显式补齐脚本，勿依赖循环"
            )
        return i

    def invoke(self, prompt):
        return FakeMsg(self.responses[self._next(prompt)], self.usage)

    def stream(self, prompt):
        self.stream_used += 1
        i = self._next(prompt)
        think = self.reasonings[i] if i < len(self.reasonings) else None
        if think:
            for piece in _slice(think):
                yield FakeChunk(reasoning=piece)
        for piece in _slice(self.responses[i]):
            yield FakeChunk(content=piece, usage=self.usage)


class BoomOnceLLM(ScriptedLLM):
    """票 05 懒补摘要压手故障注入——prompt 含 boom_marker 的第一发抛不可重试异常
    （不消费脚本、不计数），其余照 ScriptedLLM 回放。与 FlakyStreamLLM 同理＝纪律 #5
    的有意例外（表达"这一发真炸"，非回放成功内容），职责正交。"""

    def __init__(self, responses, *, boom_marker: str):
        super().__init__(responses)
        self.boom_marker = boom_marker
        self._boom = True

    def invoke(self, prompt):
        if self._boom and self.boom_marker in str(prompt):
            self._boom = False
            raise ValueError("摘要端点炸了")
        return super().invoke(prompt)


class FakeEmbedder:
    """M9 票 06 向量化离线假通道：脚本表 {文本: 向量}，未脚本化文本显式报错
    （ScriptedLLM 同纪律——"召回多向量化了一次"这类 bug 不许静默通过）。
    calls＝embed 调用次数（批量＝一次，与生产协议同语义：签库一批＋每问一发）。"""

    model = "fake-embed"  # 与 EmbeddingsClient 同面（账本 model 字段有真值可记）

    def __init__(self, table: dict[str, list[float]]):
        self.table = dict(table)
        self.calls = 0
        self.batches: list[list[str]] = []

    def embed(self, texts):
        self.calls += 1
        self.batches.append(list(texts))
        missing = [t for t in texts if t not in self.table]
        if missing:
            raise AssertionError(f"FakeEmbedder 收到未脚本化文本：{missing}")
        return [list(self.table[t]) for t in texts]


class BoomEmbedder:
    """向量化端点故障注入（纪律 #5 的有意例外，BoomOnceLLM 同理＝表达"这一发真炸"，
    非回放成功内容；职责正交）。"""

    model = "boom-embed"

    def __init__(self, exc: Exception | None = None):
        self.exc = exc or ValueError("embedding 端点炸了")
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise self.exc


class FlakyStreamLLM:
    """流式退避窄化专测的故障注入假模型（M8 票 08）——`attempts` 计建流次数。

    `pre_fail`＝建流即抛（首 token 前，一帧未发＝可重试重开）；`mid_fail`＝吐出一个
    chunk 后抛（首 token 后，帧不可回收＝如实炸）；`exc` 选异常类型（ConnectionError＝
    可重试，ValueError＝不可重试）。

    为何不塞进 ScriptedLLM（纪律 #5 的有意例外）：ScriptedLLM 把 calls↔脚本下标焊死，
    重连会二次消费脚本，表达不了「同题重开流成功」——本类职责正交（注入流式故障而非
    回放成功内容），非复制其角色。"""

    def __init__(self, *, pre_fail=0, mid_fail=0, text="hello", think=None,
                 usage=None, exc=ConnectionError):
        self.pre_fail = pre_fail
        self.mid_fail = mid_fail
        self.text = text
        self.think = think
        self.usage = usage
        self.exc = exc
        self.attempts = 0

    def stream(self, prompt):
        self.attempts += 1
        if self.pre_fail > 0:
            self.pre_fail -= 1
            raise self.exc("连接抖动")  # 首 token 前
        if self.think:
            yield FakeChunk(reasoning=self.think)
        yield FakeChunk(content=self.text, usage=self.usage)
        if self.mid_fail > 0:
            self.mid_fail -= 1
            raise self.exc("流中途断")  # 首 token 后
