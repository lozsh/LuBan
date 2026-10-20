"""验收 1：/openapi.json 完整产出全部路由。"""


async def test_openapi_paths_complete(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    expected = {
        "/health",
        "/v1/agents",
        "/v1/agents/{key}",
        "/v1/agents/{key}/versions",
        "/v1/agents/{key}/versions/{version}",
        "/v1/agents/{key}/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/cancel",
        "/v1/models",
        "/v1/brain-mappings",
        "/v1/traces/{run_id}",
        "/v1/approvals",
        "/v1/approvals/{approval_id}/decide",
        "/v1/evals",
        "/v1/evals/{result_id}",
        "/v1/skills",
    }
    assert expected <= set(paths), f"缺少路由: {expected - set(paths)}"
    assert {"get", "post"} <= set(paths["/v1/agents"])
    assert {"get", "put"} <= set(paths["/v1/models"])
