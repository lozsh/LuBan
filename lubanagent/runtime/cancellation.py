"""运行取消：CancellationToken 协作式取消与按 run_id 的注册表。"""

import asyncio
import threading

from lubanagent.core import RunCancelledError


class CancellationToken:
    """单次运行的取消令牌；事件循环点调用 throw_if_cancelled 即刻生效。"""

    def __init__(self) -> None:
        self._flag = threading.Event()
        self._async_waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def cancel(self) -> None:
        # call_soon_threadsafe 兜底跨线程取消（asyncio.Event 非线程安全）；
        # loop 已关闭时直接 set（令牌生命周期与 run 同界，届时无人在等）
        self._flag.set()
        for loop, waiter in self._async_waiters:
            try:
                loop.call_soon_threadsafe(waiter.set)
            except RuntimeError:
                waiter.set()
        self._async_waiters.clear()

    @property
    def cancelled(self) -> bool:
        return self._flag.is_set()

    def subscribe_async(self) -> asyncio.Event:
        """领一个 asyncio 等待器（后台任务监听 Run 取消用）；cancel() 时全部置位。

        已取消时返回的等待器直接置位（补领语义，不漏信号）。
        """
        waiter = asyncio.Event()
        if self.cancelled:
            waiter.set()
        else:
            self._async_waiters.append((asyncio.get_running_loop(), waiter))
        return waiter

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelledError("run cancelled")


class CancellationRegistry:
    """run_id -> CancellationToken；重复 create 返回既有 token。"""

    def __init__(self) -> None:
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = threading.Lock()

    def create(self, run_id: str) -> CancellationToken:
        with self._lock:
            token = self._tokens.get(run_id)
            if token is None:
                token = CancellationToken()
                self._tokens[run_id] = token
            return token

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True

    def is_active(self, run_id: str) -> bool:
        """该 run 是否在本进程在册（resume 活跃拒绝检查用）。"""
        with self._lock:
            return run_id in self._tokens

    def discard(self, run_id: str) -> None:
        with self._lock:
            self._tokens.pop(run_id, None)
