"""全局限速器：并发评测下保护上游 API（与指数退避互补，429 仍走 backoff）。

语义：相邻 acquire 间隔 ≥ 1/qps 秒；qps=0 直通（默认不限速）。
线程安全：锁保护窗口游标，等待发生在锁内——并发线程按窗口排队（每线程预留
自己的时间片），不会同时放行多个调用。
"""
import threading
import time


class RateLimiter:
    def __init__(self, qps: float, sleep=time.sleep, clock=time.monotonic):
        self._interval = 1.0 / qps if qps > 0 else 0.0
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next = self._clock()

    def acquire(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = self._clock()
            wait = self._next - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._next = now + self._interval