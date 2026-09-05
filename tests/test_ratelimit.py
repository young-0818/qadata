"""全局限速器测试：注入 sleep/clock 断言最小间隔；零 qps 永不等待。"""
import threading

import pytest

from qadata.llm.ratelimit import RateLimiter


def test_rate_limiter_enforces_min_interval():
    """相邻 acquire 间隔 ≥ 1/qps：时钟静止时第二次必须睡满一个间隔。"""
    sleeps: list[float] = []
    clock = [0.0]

    def fake_sleep(s):
        sleeps.append(s)

    def fake_clock():
        return clock[0]

    rl = RateLimiter(qps=2, sleep=fake_sleep, clock=fake_clock)  # 间隔 0.5s
    rl.acquire()          # t=0：首放行，不睡
    rl.acquire()          # t=0：睡 0.5
    clock[0] += 0.5
    rl.acquire()          # t=0.5：窗口已到，不睡
    assert sleeps == [0.5]


def test_rate_limiter_zero_qps_never_waits():
    rl = RateLimiter(qps=0, sleep=lambda s: pytest.fail("不应睡眠"), clock=lambda: 0.0)
    for _ in range(5):
        rl.acquire()


def test_rate_limiter_thread_safe_smoke():
    """并发 acquire 冒烟：互斥下不炸、无死锁（真实时钟，小间隔）。"""
    locked = []

    rl = RateLimiter(qps=10)  # 间隔 0.1s，真实时钟
    threads = [threading.Thread(target=lambda: (rl.acquire(), locked.append(1)))
               for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert len(locked) == 5