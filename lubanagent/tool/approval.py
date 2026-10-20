"""高风险工具人工确认: 审批存储可插拔（内存缺省 / DB 持久化），发起、异步等待与决定恢复。

R11 遗留项修复：审批记录不再强制进程内存——经 ApprovalStore 协议注入存储实现，
`DbApprovalStore`（persistence 层）落库后进程重启不丢决定、可审计；等待方以
future 即时唤醒（同进程 decide）+ 周期轮询 store（跨进程 decide 兜底）双通道恢复。
decide 经 `try_resolve` 条件更新（pending→terminal）保证原子性：并发/跨进程
双 decide 恰一个成功。

边界（如实声明）：审批记录持久化 ≠ Run 可恢复——Run 无 checkpoint，进程重启后
Run 仍卡 WAITING_APPROVAL；本模块只保证"决定本身"不丢、不重复、可归因。
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from lubanagent.core import ApprovalRequest, ErrorCode, LubanError, ToolDescriptor

from .executor import ApprovalHandler

TerminalStatus = Literal["approved", "rejected", "expired"]


class ApprovalStore(Protocol):
    """审批存储协议：MemoryApprovalStore（进程内）与 DbApprovalStore（落库）共用。"""

    async def save(self, request: ApprovalRequest) -> None:
        """新请求落存储；同 id 重复 save 由实现方决定（当前调用方不触发）。"""
        ...

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        """不存在返回 None。"""
        ...

    async def list_pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        """pending 记录，创建时间升序。"""
        ...

    async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        """全部记录（含终态，审计用），创建时间升序。"""
        ...

    async def try_resolve(
        self, approval_id: str, status: TerminalStatus, *, decided_by: str | None = None
    ) -> bool:
        """原子 pending→terminal；False = 不存在或已被并发决定（decide 竞态裁决点）。"""
        ...


class MemoryApprovalStore:
    """进程内 dict 存储（缺省实现）：行为与旧版一致——进程重启即丢，跨实例不可见。"""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._requests[request.id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._requests.get(approval_id)

    async def list_pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        return [
            request
            for request in self._requests.values()
            if request.status == "pending" and (run_id is None or request.run_id == run_id)
        ]

    async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        return [
            request
            for request in self._requests.values()
            if run_id is None or request.run_id == run_id
        ]

    async def try_resolve(
        self, approval_id: str, status: TerminalStatus, *, decided_by: str | None = None
    ) -> bool:
        request = self._requests.get(approval_id)
        if request is None or request.status != "pending":
            return False
        self._requests[approval_id] = request.resolved(status, decided_by=decided_by)
        return True


class ApprovalManager:
    """审批管理器: 发起 -> 等待决定（future 即时唤醒 + 轮询兜底）-> decide 恢复。

    store=None（缺省）= MemoryApprovalStore，进程局部语义（跨实例 decide/wait
    显式 VALIDATION_ERROR，不静默放行）；注入 DbApprovalStore 后决定落库、
    跨进程可见。等待循环每 poll_interval_seconds 重查一次 store：另一进程
    （或重启后的新实例）decide 后，本进程 waiter 在一个轮询周期内恢复。
    """

    def __init__(
        self,
        *,
        store: ApprovalStore | None = None,
        default_timeout_seconds: float = 3600.0,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._store: ApprovalStore = store if store is not None else MemoryApprovalStore()
        self._default_timeout_seconds = default_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._waiters: dict[str, asyncio.Future[bool]] = {}

    async def create_request(
        self, run_id: str, tool: ToolDescriptor, args: dict
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            id=uuid.uuid4().hex,
            run_id=run_id,
            tool_key=tool.key,
            args=dict(args),
            created_at=datetime.now(UTC),
            status="pending",
        )
        await self._store.save(request)
        return request

    async def wait_for_decision(
        self, approval_id: str, *, timeout_seconds: float | None = None
    ) -> bool:
        """True=批准 / False=拒绝或过期；跨进程 decide 经轮询兜底感知。"""
        request = await self._store.get(approval_id)
        if request is None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"审批不存在: {approval_id}")
        if request.status != "pending":
            return request.status == "approved"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._waiters[approval_id] = future
        timeout = self._default_timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = loop.time() + timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await self._store.try_resolve(approval_id, "expired")
                    return False
                # shield：段超时的取消只落在本层 wrapper，future 保留给下一轮/decide
                segment = min(self._poll_interval_seconds, remaining)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), segment)
                except TimeoutError:
                    request = await self._store.get(approval_id)
                    if request is None:
                        raise LubanError(
                            ErrorCode.VALIDATION_ERROR, f"审批不存在: {approval_id}"
                        ) from None
                    if request.status != "pending":
                        return request.status == "approved"
        finally:
            self._waiters.pop(approval_id, None)

    async def decide(
        self, approval_id: str, approved: bool, *, decided_by: str = ""
    ) -> ApprovalRequest:
        """不存在或已终结 -> LubanError(VALIDATION_ERROR)；否则唤醒等待方。

        终结靠 store 条件更新裁决：并发双 decide 恰一个成功，后到者显式报错。
        """
        terminal: TerminalStatus = "approved" if approved else "rejected"
        resolved = await self._store.try_resolve(
            approval_id, terminal, decided_by=decided_by or None
        )
        if not resolved:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"审批不可决定: {approval_id}")
        request = await self._store.get(approval_id)
        assert request is not None  # try_resolve 成功则记录必在
        future = self._waiters.get(approval_id)
        if future is not None and not future.done():
            future.set_result(approved)
        return request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return await self._store.get(approval_id)

    async def pending(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        return await self._store.list_pending(run_id=run_id)

    async def list_all(self, *, run_id: str | None = None) -> list[ApprovalRequest]:
        """全部审批记录（含终态）：审计/重启后回查入口。"""
        return await self._store.list_all(run_id=run_id)

    def as_handler(self, run_id: str) -> ApprovalHandler:
        """生成传给 ToolExecutor 的 handler: create_request + wait_for_decision。"""

        async def handler(tool: ToolDescriptor, args: dict) -> bool:
            request = await self.create_request(run_id, tool, args)
            return await self.wait_for_decision(request.id)

        return handler


class AutoApproveManager(ApprovalManager):
    """无人值守场景的自动放行管理器：请求即批，不等待人工。

    供 `luban eval run --auto-approve` 等无交互进程使用——审批型（高风险）
    工具在评测进程里没有人工可等，不配 manager 会直接 ToolApprovalRequiredError
    打死整个 Run。**高风险工具会被真实执行**：仅用于评测/演示等受控环境
    （评测数据集通常指向 Mock 业务服务），生产服务禁止使用。
    请求仍走完整状态机与事件流（requested → resolved），Trace 语义不变；
    注入持久化 store 时自动批准同样落库（decided_by=auto-approve 归因）。
    """

    async def wait_for_decision(
        self, approval_id: str, *, timeout_seconds: float | None = None
    ) -> bool:
        request = await self._store.get(approval_id)
        if request is None:
            return False
        if request.status == "pending":
            resolved = await self._store.try_resolve(
                approval_id, "approved", decided_by="auto-approve"
            )
            if resolved:
                return True
            # 并发下已被他人决定，回读真实终态
            request = await self._store.get(approval_id)
            assert request is not None
        return request.status == "approved"


__all__ = [
    "ApprovalManager",
    "ApprovalRequest",
    "ApprovalStore",
    "AutoApproveManager",
    "MemoryApprovalStore",
]
