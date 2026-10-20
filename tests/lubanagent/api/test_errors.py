"""验收 4：错误映射统一 ErrorOut 形状，业务错误不泄堆栈。"""


def _assert_error_shape(body: dict, code: str) -> None:
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    assert isinstance(error["retryable"], bool)


async def test_404_error_shape(client):
    response = await client.get("/v1/agents/ghost")
    assert response.status_code == 404
    _assert_error_shape(response.json(), "AGENT_NOT_FOUND")


async def test_422_validation_error_shape(client):
    # 缺必填字段 name → RequestValidationError → 422 VALIDATION_ERROR
    response = await client.post("/v1/agents", json={"key": "x"})
    assert response.status_code == 422
    body = response.json()
    _assert_error_shape(body, "VALIDATION_ERROR")
    assert body["error"]["details"]["errors"]


async def test_422_validation_error_does_not_echo_input(client):
    """422 details 只回显 loc/type/msg，不回显原始输入值。"""
    response = await client.post(
        "/v1/agents", json={"key": {"nested": "sk-synthetic-secret"}, "name": "x"}
    )
    assert response.status_code == 422
    assert "sk-synthetic-secret" not in response.text


async def test_422_run_request_validation(client):
    response = await client.post("/v1/agents/x/runs", json={})
    assert response.status_code == 422
    _assert_error_shape(response.json(), "VALIDATION_ERROR")


async def test_unhandled_error_no_stack(app):
    # 注入一个必炸的 AuthProvider：未捕获异常 → 500 INTERNAL_ERROR，响应不含堆栈
    import httpx

    class BoomAuth:
        async def verify(self, request):
            raise RuntimeError("boom-secret-detail")

    app.state.ctx.settings.auth_enabled = True
    app.state.ctx.registry.register_auth(BoomAuth())
    # ServerErrorMiddleware 发送响应后会再抛异常供服务器记日志，测试客户端须吞掉
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/agents")
    assert response.status_code == 500
    body = response.json()
    _assert_error_shape(body, "INTERNAL_ERROR")
    assert "boom-secret-detail" not in response.text
    assert "Traceback" not in response.text
