"""运行取消：CancellationToken 协作式取消与按 run_id 的注册表。"""

import threading

from lubanagent.core import RunCancelledError


class CancellationToken:
    """单次运行的取消令牌；事件循环点调用 throw_if_cancelled 即刻生效。"""

    def __init__(self) -> None:
        self._flag = threading.Event()

    def cancel(self) -> None:
        self._flag.set()

    @property
    def cancelled(self) -> bool:
        return self._flag.is_set()

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
