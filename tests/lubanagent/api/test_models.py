"""验收 7：models 脱敏 + brain-mappings。"""

from sqlalchemy import select

from lubanagent.persistence.models import ModelRow

_SECRET_URL = "http://secret-host.internal:9000/v1"


def _model_payload() -> dict:
    return {
        "key": "qwen-plus",
        "provider": "openai_compat",
        "model_name": "qwen-plus",
        "factory": "openai_compat",
        "base_url": _SECRET_URL,
        "api_key_env": "QWEN_API_KEY",
        "params": {"temperature": 0.2},
    }


async def test_list_models_seeded_and_masked(client):
    response = await client.get("/v1/models")
    assert response.status_code == 200
    keys = [model["key"] for model in response.json()]
    assert "fake-chat" in keys
    # 脱敏：输出结构里没有 base_url 等连接信息字段
    assert all("base_url" not in model for model in response.json())


async def test_upsert_model_masked_response(client):
    response = await client.put("/v1/models", json=_model_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["api_key_env"] == "QWEN_API_KEY"  # 变量名可输出
    assert "base_url" not in body
    assert _SECRET_URL not in response.text  # 连接信息不外泄

    listing = await client.get("/v1/models")
    assert _SECRET_URL not in listing.text
    qwen = next(m for m in listing.json() if m["key"] == "qwen-plus")
    assert qwen["api_key_env"] == "QWEN_API_KEY"


async def test_reject_secret_model_params_without_writing(db, client):
    for params in (
        {"api_key": "sk-synthetic-secret"},
        {"default_headers": {"Authorization": "Bearer sk-synthetic-secret"}},
        {"client_options": {"headers": {"x-api-key": "sk-synthetic-secret"}}},
        {"base_url": "https://evil.invalid/v1"},
    ):
        payload = {**_model_payload(), "params": params}
        response = await client.put("/v1/models", json=payload)
        assert response.status_code == 422
        assert "sk-synthetic-secret" not in response.text
        async with db.session() as session:
            assert (
                await session.scalar(select(ModelRow).where(ModelRow.key == payload["key"])) is None
            )


async def test_reject_embedded_url_credentials_without_writing(db, client):
    response = await client.put(
        "/v1/models", json={**_model_payload(), "base_url": "http://user:password@local.invalid/v1"}
    )
    assert response.status_code == 422
    assert "password" not in response.text
    async with db.session() as session:
        assert await session.scalar(select(ModelRow).where(ModelRow.key == "qwen-plus")) is None


async def test_base_url_query_and_fragment_rejected_without_writing(db, client):
    for url in (
        "http://127.0.0.1:11434/v1?key=sk-synthetic-secret",
        "http://127.0.0.1:11434/v1#token=sk-synthetic-secret",
    ):
        response = await client.put("/v1/models", json={**_model_payload(), "base_url": url})
        assert response.status_code == 422
        assert "sk-synthetic-secret" not in response.text
    async with db.session() as session:
        assert await session.scalar(select(ModelRow).where(ModelRow.key == "qwen-plus")) is None


async def test_model_edit_omitting_base_url_preserves_connection(db, client):
    created = await client.put("/v1/models", json=_model_payload())
    assert created.status_code == 200
    changed = await client.put(
        "/v1/models", json={**_model_payload(), "enabled": False, "base_url": None}
    )
    assert changed.status_code == 200
    async with db.session() as session:
        row = await session.scalar(select(ModelRow).where(ModelRow.key == "qwen-plus"))
        assert row.base_url == _SECRET_URL
        assert row.enabled is False


async def test_reject_whitelist_violations_and_redacted_placeholders(db, client):
    for params in (
        {"temperature": 0.2, "custom_flag": 1},
        {"temperature": "[REDACTED]"},
    ):
        payload = {**_model_payload(), "params": params}
        response = await client.put("/v1/models", json=payload)
        assert response.status_code == 422
        assert str(params) not in response.text
        async with db.session() as session:
            assert (
                await session.scalar(select(ModelRow).where(ModelRow.key == payload["key"])) is None
            )


async def test_existing_dirty_params_are_redacted_without_changing_database(db, client):
    async with db.session() as session:
        session.add(
            ModelRow(
                key="legacy-secret",
                provider="openai_compat",
                model_name="old",
                factory="openai_compat",
                capability={},
                base_url="http://user:password@local.invalid/v1",
                api_key_env="SYNTHETIC_KEY",
                params={
                    "temperature": 0.3,
                    "api_key": "sk-synthetic-secret",
                    "nested": {
                        "Authorization": "Bearer sk-nested-secret",
                        "url": "https://local.invalid?api_key=sk-url-secret",
                        "access_key": "sk-other-secret",
                    },
                },
                enabled=True,
            )
        )
        await session.flush()
    response = await client.get("/v1/models")
    assert response.status_code == 200
    body = next(item for item in response.json() if item["key"] == "legacy-secret")
    assert body["params"]["temperature"] == 0.3
    for secret in (
        "sk-synthetic-secret", "sk-nested-secret", "sk-url-secret", "sk-other-secret", "password"
    ):
        assert secret not in response.text
    async with db.session() as session:
        row = await session.scalar(select(ModelRow).where(ModelRow.key == "legacy-secret"))
        assert row.params["api_key"] == "sk-synthetic-secret"


async def test_brain_mapping_set_and_model_not_found(client):
    ok = await client.put("/v1/brain-mappings", json={"profile": "smart", "model_key": "fake-chat"})
    assert ok.status_code == 200
    assert ok.json() == {"env": "dev", "profile": "smart", "model_key": "fake-chat"}

    missing = await client.put(
        "/v1/brain-mappings", json={"profile": "smart", "model_key": "ghost"}
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "MODEL_NOT_FOUND"


async def test_upsert_model_validation_error(client):
    response = await client.put("/v1/models", json={"key": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_brain_mappings_list_echo(client):
    """G3 补丁：GET /v1/brain-mappings 回显四槽位当前映射。"""
    await client.put("/v1/brain-mappings", json={"profile": "smart", "model_key": "fake-chat"})
    response = await client.get("/v1/brain-mappings")
    assert response.status_code == 200
    rows = {(r["env"], r["profile"]): r["model_key"] for r in response.json()}
    assert ("dev", "smart") in rows
    assert rows[("dev", "smart")] == "fake-chat"
