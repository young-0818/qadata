"""LangGraph Pregel 语义最小复刻（教学模块，M3 面试防御；里程碑收尾删除）。

走读 langgraph 源码后的复刻要点：
- channels：状态键的更新语义——LastValue 整值覆盖（qadata AgentState 全部键默认）；
  Topic 追加合并（LangGraph 的 add_messages 用 BinaryOperatorAggregate 归约）；
- 节点返回 dict，按各键对应 channel 的 update 规则并入状态；
- 条件边：路由函数读状态返回下一节点名（qadata 的 _route_after_execute 同构）；
- 主循环：从 start 出发逐节点执行直至 END；max_steps 防条件边死循环。
"""
from collections.abc import Callable

END = "__end__"


class Channel:
    def update(self, current, value):
        raise NotImplementedError


class LastValue(Channel):
    """整值覆盖：默认 channel（每步只接受一个值，存最后一个）。"""

    def update(self, current, value):
        return value


class Topic(Channel):
    """追加合并：每次更新把新元素并入列表（LangGraph Topic.accumulate=True 的简化）。"""

    def update(self, current, value):
        cur = current if current is not None else []
        return cur + list(value)


class MiniGraph:
    def __init__(self):
        self.nodes: dict[str, Callable] = {}
        self.edges: dict[str, str] = {}
        self.cond_edges: dict[str, tuple[Callable, dict[str, str]]] = {}
        self.channels: dict[str, Channel] = {}

    def set_channel(self, name: str, channel: Channel) -> None:
        self.channels[name] = channel

    def add_node(self, name: str, fn: Callable) -> None:
        self.nodes[name] = fn

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def add_conditional_edges(self, source: str, router: Callable,
                              mapping: dict[str, str]) -> None:
        self.cond_edges[source] = (router, mapping)

    def invoke(self, initial: dict, start: str, max_steps: int = 50) -> dict:
        state = dict(initial)
        current = start
        for _ in range(max_steps):
            if current == END:
                return state
            update = self.nodes[current](state) or {}
            for key, value in update.items():
                channel = self.channels.get(key, LastValue())
                state[key] = channel.update(state.get(key), value)
            if current in self.cond_edges:
                router, mapping = self.cond_edges[current]
                current = mapping[router(state)]
            else:
                current = self.edges.get(current, END)
        raise RuntimeError(f"超过 max_steps={max_steps}，疑似条件边死循环")
