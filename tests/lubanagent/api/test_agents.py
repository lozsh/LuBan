"""Agent CRUD 与版本快照路由。"""

from tests.lubanagent.api.conftest import create_demo_agent


async def test_agent_crud_and_versions(client):
    await create_demo_agent(client)

    listing = await client.get("/v1/agents")
    assert listing.status_code == 200
    assert [agent["key"] for agent in listing.json()] == ["demo-agent"]

    detail = await client.get("/v1/agents/demo-agent")
    assert detail.status_code == 200
    assert detail.json()["version"] == 1

    # 新版本快照
    v2 = await client.post(
        "/v1/agents/demo-agent/versions",
        json={"key": "demo-agent", "name": "demo-agent", "version": 2, "tools": []},
    )
    assert v2.status_code == 201
    assert v2.json()["version"] == 2

    snapshot = await client.get("/v1/agents/demo-agent/versions/2")
    assert snapshot.status_code == 200
    assert snapshot.json()["definition"]["version"] == 2


async def test_create_agent_conflict(client):
    await create_demo_agent(client)
    duplicate = await client.post(
        "/v1/agents", json={"key": "demo-agent", "name": "demo-agent", "tools": []}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_create_version_conflict_and_missing_agent(client):
    await create_demo_agent(client)
    duplicate = await client.post(
        "/v1/agents/demo-agent/versions",
        json={"key": "demo-agent", "name": "demo-agent", "version": 1, "tools": []},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = await client.post(
        "/v1/agents/ghost/versions",
        json={"key": "ghost", "name": "ghost", "version": 2, "tools": []},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_get_agent_and_version_not_found(client):
    response = await client.get("/v1/agents/ghost")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"

    await create_demo_agent(client)
    missing_version = await client.get("/v1/agents/demo-agent/versions/42")
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "AGENT_VERSION_NOT_FOUND"
