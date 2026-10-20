"""审批路由：ApprovalManager 的 HTTP 面（客服场景人工确认入口）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from lubanagent.api.auth import get_principal
from lubanagent.api.deps import ApprovalManagerDep
from lubanagent.api.schemas import ApprovalOut, DecideOut, DecideRequest, ErrorOut
from lubanagent.core import ErrorCode
from lubanagent.extension import Principal
from lubanagent.tool import ApprovalRequest

router = APIRouter(tags=["approvals"])

# B008 规避：Depends 调用收敛到 Annotated 别名，不在参数默认值里调用
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _to_out(request: ApprovalRequest) -> ApprovalOut:
    return ApprovalOut(
        id=request.id,
        run_id=request.run_id,
        tool_key=request.tool_key,
        args=request.args,
        created_at=request.created_at,
        status=request.status,
    )


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    manager: ApprovalManagerDep,
    run_id: str | None = None,
) -> list[ApprovalOut]:
    return [_to_out(request) for request in await manager.pending(run_id=run_id)]


@router.post("/approvals/{approval_id}/decide", response_model=DecideOut)
async def decide(
    approval_id: str,
    body: DecideRequest,
    manager: ApprovalManagerDep,
    principal: PrincipalDep,
):
    request = await manager.get(approval_id)
    if request is None or request.status != "pending":
        # 契约：审批不存在或已终结 → 404 + ErrorOut(VALIDATION_ERROR)
        error = ErrorOut.of(
            ErrorCode.VALIDATION_ERROR.value, f"审批不可决定: {approval_id}"
        )
        return JSONResponse(status_code=404, content=error.model_dump(mode="json"))
    # 决定人归因取「已验证主体」（鉴权主体 subject），不信任请求体里的自报 decided_by
    resolved = await manager.decide(
        approval_id, body.approved, decided_by=principal.subject
    )
    return DecideOut(
        status=resolved.status,
        decided_by=resolved.decided_by,
        decided_at=resolved.decided_at,
    )
