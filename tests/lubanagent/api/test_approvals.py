"""验收 5：approvals —— 构造 pending → GET 列出 → POST decide → 状态终结。"""

from lubanagent.core import ToolDescriptor


def _descriptor() -> ToolDescriptor:
    return ToolDescriptor(key="refund", name="refund", description="退款", args_schema={})


async def test_approval_flow(app, client):
    manager = app.state.ctx.approval_manager
    pending = await manager.create_request("run-1", _descriptor(), {"amount": 10})

    listing = await client.get("/v1/approvals", params={"run_id": "run-1"})
    assert listing.status_code == 200
    items = listing.json()
    assert [item["id"] for item in items] == [pending.id]
    assert items[0]["status"] == "pending"
    assert items[0]["tool_key"] == "refund"

    decided = await client.post(
        f"/v1/approvals/{pending.id}/decide", json={"approved": True, "decided_by": "op"}
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert decided.json()["decided_by"] == "anonymous"
    assert decided.json()["decided_at"] is not None
    assert (await manager.get(pending.id)).status == "approved"
    assert (await manager.get(pending.id)).decided_by == "anonymous"
    assert (await manager.get(pending.id)).decided_at is not None

    # 已终结的审批不可再决定
    again = await client.post(f"/v1/approvals/{pending.id}/decide", json={"approved": False})
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_approval_reject_and_not_found(app, client):
    manager = app.state.ctx.approval_manager
    pending = await manager.create_request("run-2", _descriptor(), {})
    rejected = await client.post(f"/v1/approvals/{pending.id}/decide", json={"approved": False})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decided_by"] == "anonymous"
    assert (await manager.get(pending.id)).status == "rejected"

    missing = await client.post("/v1/approvals/nope/decide", json={"approved": True})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_decider_is_verified_key_holder_not_client_claim(db, registry):
    from tests.lubanagent.api.conftest import make_client

    async with await make_client(
        db, registry, auth_enabled=True, api_key="synthetic-key"
    ) as client:
        app = client._transport.app
        pending = await app.state.ctx.approval_manager.create_request("run-3", _descriptor(), {})
        path = f"/v1/approvals/{pending.id}/decide"
        assert (
            await client.post(path, json={"approved": True, "decided_by": "admin"})
        ).status_code == 401
        decided = await client.post(
            path,
            json={"approved": True, "decided_by": "admin"},
            headers={"Authorization": "Bearer synthetic-key"},
        )
        assert decided.status_code == 200
        assert decided.json()["decided_by"] == "api-key-holder"
        assert (await app.state.ctx.approval_manager.get(pending.id)).decided_by == "api-key-holder"


async def test_approvals_run_id_filter(app, client):
    manager = app.state.ctx.approval_manager
    await manager.create_request("run-a", _descriptor(), {})
    await manager.create_request("run-b", _descriptor(), {})
    filtered = await client.get("/v1/approvals", params={"run_id": "run-a"})
    assert [item["run_id"] for item in filtered.json()] == ["run-a"]
    all_items = await client.get("/v1/approvals")
    assert len(all_items.json()) == 2
