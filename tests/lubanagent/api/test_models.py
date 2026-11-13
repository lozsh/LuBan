"""验收 7：models 脱敏 + brain-mappings。"""

import pytest
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
        "sk-synthetic-secret",
        "sk-nested-secret",
        "sk-url-secret",
        "sk-other-secret",
        "password",
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


async def _create_profile(client, key="reasoning", **fields):
    response = await client.post(
        "/v1/brain-profiles",
        json={"key": key, "name": "推理专用", "model_key": "fake-chat", **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_brain_profile_custom_crud_and_live_mapping(client):
    created = await _create_profile(client)
    assert created["key"] == "reasoning"
    assert created["name"] == "推理专用"
    catalog = (await client.get("/v1/brain-profiles")).json()
    assert catalog["env"] == "dev"
    assert "reasoning" in [slot["key"] for slot in catalog["items"]]
    mappings = (await client.get("/v1/brain-mappings")).json()
    assert {"env": "dev", "profile": "reasoning", "model_key": "fake-chat"} in mappings
    changed = await client.patch(
        "/v1/brain-profiles/reasoning", json={"name": "深度推理", "description": "复杂任务"}
    )
    assert changed.status_code == 200
    assert changed.json()["description"] == "复杂任务"
    deleted = await client.delete("/v1/brain-profiles/reasoning")
    assert deleted.status_code == 200 and deleted.json() == {"removed": True}
    assert "reasoning" not in [
        slot["key"] for slot in (await client.get("/v1/brain-profiles")).json()["items"]
    ]


@pytest.mark.parametrize(
    "brain",
    [
        {"profile": "reasoning"},
        {"profile": "smart", "fallback_profiles": ["reasoning"]},
    ],
)
async def test_brain_profile_in_use_delete_rejected_primary_and_fallback(client, brain):
    await _create_profile(client)
    agent = await client.post(
        "/v1/agents", json={"key": "using-slot", "name": "Using", "brain": brain}
    )
    assert agent.status_code == 201, agent.text
    refs = await client.get("/v1/brain-profiles/reasoning/references")
    assert refs.status_code == 200
    assert refs.json()["agent_count"] == 1
    assert refs.json()["references"][0]["agent_key"] == "using-slot"
    deleted = await client.delete("/v1/brain-profiles/reasoning")
    assert deleted.status_code == 409, deleted.text
    assert deleted.json()["error"]["code"] == "BRAIN_PROFILE_IN_USE"
    assert deleted.json()["error"]["details"]["references"][0]["agent_key"] == "using-slot"
    assert "reasoning" in [
        row["profile"] for row in (await client.get("/v1/brain-mappings")).json()
    ]


async def test_brain_profile_history_blocks_delete_even_after_current_changes(client):
    await _create_profile(client)
    created = await client.post(
        "/v1/agents", json={"key": "history", "name": "History", "brain": {"profile": "reasoning"}}
    )
    assert created.status_code == 201
    changed = await client.post(
        "/v1/agents/history/versions",
        json={**created.json(), "version": 2, "brain": {"profile": "smart"}},
    )
    assert changed.status_code == 201
    refs = (await client.get("/v1/brain-profiles/reasoning/references")).json()
    assert refs["references"] == [
        {
            "agent_key": "history",
            "agent_name": "History",
            "version": 1,
            "current": False,
            "field": "brain.profile",
        }
    ]
    assert (await client.delete("/v1/brain-profiles/reasoning")).status_code == 409


async def test_brain_profile_rename_preserves_versions_and_all_env_mappings(client, db):
    from lubanagent.core import BrainProfile
    from lubanagent.persistence import ModelRepository

    await _create_profile(client)
    await client.put(
        "/v1/brain-mappings", json={"profile": "reasoning", "env": "prod", "model_key": "fake-chat"}
    )
    agent = (
        await client.post(
            "/v1/agents",
            json={"key": "legacy", "name": "Legacy", "brain": {"profile": "reasoning"}},
        )
    ).json()
    renamed = await client.patch(
        "/v1/brain-profiles/reasoning", json={"key": "deep_reasoning", "name": "深度推理"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["key"] == "deep_reasoning"
    assert "reasoning" in renamed.json()["aliases"]
    historical = (await client.get("/v1/agents/legacy/versions/1")).json()["definition"]
    assert historical["brain"] == agent["brain"]
    for env in ["dev", "prod"]:
        mappings = (await client.get("/v1/brain-mappings", params={"env": env})).json()
        assert {"env": env, "profile": "deep_reasoning", "model_key": "fake-chat"} in mappings
        assert not any(mapping["profile"] == "reasoning" for mapping in mappings)
        async with db.session() as session:
            assert (
                await ModelRepository(session).get_brain_mapping(env, BrainProfile("reasoning"))
            ).key == "fake-chat"
    next_version = await client.post(
        "/v1/agents/legacy/versions", json={**historical, "version": 2}
    )
    assert next_version.status_code == 201
    assert next_version.json()["definition"]["brain"]["profile"] == "deep_reasoning"
    assert (await client.delete("/v1/brain-profiles/deep_reasoning")).status_code == 409


async def test_brain_profile_invalid_conflict_and_unknown_values(client):
    for key in ["", "Capital", "../bad", "a" * 65]:
        response = await client.post("/v1/brain-profiles", json={"key": key, "name": "Invalid"})
        assert response.status_code == 422
    await _create_profile(client)
    assert (
        await client.post("/v1/brain-profiles", json={"key": "reasoning", "name": "Repeat"})
    ).status_code == 409
    assert (
        await client.patch("/v1/brain-profiles/reasoning", json={"key": "smart"})
    ).status_code == 409
    assert (await client.delete("/v1/brain-profiles/missing")).status_code == 404
    unknown = await client.post(
        "/v1/agents", json={"key": "invalid", "name": "Invalid", "brain": {"profile": "missing"}}
    )
    assert (
        unknown.status_code == 404 and unknown.json()["error"]["code"] == "BRAIN_PROFILE_NOT_FOUND"
    )


async def test_brain_profile_custom_agent_runs_end_to_end(client):
    await _create_profile(client)
    agent = await client.post(
        "/v1/agents",
        json={"key": "custom-run", "name": "Custom", "brain": {"profile": "reasoning"}},
    )
    assert agent.status_code == 201
    stream = await client.post("/v1/agents/custom-run/runs", json={"input": "你好"})
    assert stream.status_code == 200
    import json

    events = [
        json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
    ]
    assert any(event["type"] == "run_completed" for event in events), stream.text
    run = (await client.get("/v1/runs/" + events[0]["run_id"])).json()
    assert run["brain_profile"] == "reasoning" and run["status"] == "completed"


async def test_deleted_preset_not_resurrected_by_seed(client, db):
    from lubanagent.app import seed_defaults

    assert (await client.delete("/v1/brain-profiles/vision")).status_code == 200
    await seed_defaults(db)
    assert "vision" not in [
        slot["key"] for slot in (await client.get("/v1/brain-profiles")).json()["items"]
    ]
    assert not any(
        row["profile"] == "vision" for row in (await client.get("/v1/brain-mappings")).json()
    )


async def test_brain_profile_summary_dependency_survives_cheap_rename(client, db):
    from lubanagent.app import seed_defaults

    created = await client.post(
        "/v1/agents",
        json={"key": "summary-user", "name": "摘要助手", "memory": {"summary": True}},
    )
    assert created.status_code == 201
    renamed = await client.patch(
        "/v1/brain-profiles/cheap", json={"key": "summary_model", "name": "记忆摘要"}
    )
    assert renamed.status_code == 200
    refs = (await client.get("/v1/brain-profiles/summary_model/references")).json()
    assert refs["references"][0]["field"] == "memory.summary"
    assert (await client.delete("/v1/brain-profiles/summary_model")).status_code == 409
    await seed_defaults(db)
    catalog = (await client.get("/v1/brain-profiles")).json()["items"]
    assert "cheap" not in [slot["key"] for slot in catalog]
    assert next(slot for slot in catalog if slot["key"] == "summary_model")["name"] == "记忆摘要"
    updated = await client.post(
        "/v1/agents/summary-user/versions", json={**created.json(), "version": 2}
    )
    assert updated.status_code == 201


async def test_unused_presets_can_delete_but_not_silently_resurrect(client, db):
    from lubanagent.app import seed_defaults

    for key in ["smart", "cheap"]:
        assert (await client.delete(f"/v1/brain-profiles/{key}")).status_code == 200
    await seed_defaults(db)
    catalog = (await client.get("/v1/brain-profiles")).json()["items"]
    assert {slot["key"] for slot in catalog} == {"fast", "vision"}
    missing = await client.post("/v1/agents", json={"key": "default", "name": "Default"})
    assert missing.status_code == 404
    explicit = await client.post(
        "/v1/agents", json={"key": "explicit", "name": "Explicit", "brain": {"profile": "fast"}}
    )
    assert explicit.status_code == 201
    summary = await client.post(
        "/v1/agents",
        json={
            "key": "summary",
            "name": "Summary",
            "brain": {"profile": "fast"},
            "memory": {"summary": True},
        },
    )
    assert summary.status_code == 404


async def test_brain_profile_alias_cannot_be_reused_and_rename_back_preserves_identity(client):
    await _create_profile(client)
    assert (
        await client.patch("/v1/brain-profiles/reasoning", json={"key": "deep_reasoning"})
    ).status_code == 200
    conflict = await client.post(
        "/v1/brain-profiles", json={"key": "reasoning", "name": "Different"}
    )
    assert conflict.status_code == 409
    assert (
        await client.patch("/v1/brain-profiles/fast", json={"key": "reasoning"})
    ).status_code == 409
    restored = await client.patch("/v1/brain-profiles/deep_reasoning", json={"key": "reasoning"})
    assert restored.status_code == 200
    assert restored.json()["aliases"] == ["deep_reasoning"]
    assert (await client.delete("/v1/brain-profiles/deep_reasoning")).status_code == 200
    assert (
        await client.put(
            "/v1/brain-mappings", json={"profile": "reasoning", "model_key": "fake-chat"}
        )
    ).status_code == 404
    recreated = await _create_profile(client)
    assert recreated["aliases"] == ["deep_reasoning"]


async def test_brain_profile_long_identifier_and_legacy_agent_run_after_rename(client, db):
    from lubanagent.persistence.models import AgentRow, RunRow

    key = "a" * 64
    await _create_profile(client, key)
    created = await client.post(
        "/v1/agents", json={"key": "long-profile", "name": "Long", "brain": {"profile": key}}
    )
    assert created.status_code == 201
    run = await client.post("/v1/agents/long-profile/runs", json={"input": "same input"})
    assert run.status_code == 200 and '"type": "run_completed"' in run.text
    renamed = await client.patch(f"/v1/brain-profiles/{key}", json={"key": "renamed"})
    assert renamed.status_code == 200
    before = (await client.get("/v1/agents/long-profile/versions/1")).json()["definition"]
    assert before["brain"]["profile"] == key
    rerun = await client.post(
        "/v1/agents/long-profile/runs", json={"input": "same input", "version": 1}
    )
    assert rerun.status_code == 200 and '"type": "run_completed"' in rerun.text
    async with db.session() as session:
        rows = (
            await session.scalars(
                select(RunRow).join(AgentRow).where(AgentRow.key == "long-profile")
            )
        ).all()
        # Run 如实记录当时请求/定义使用的旧标识，不重写历史。
        assert len(rows) == 2 and all(row.brain_profile == key for row in rows)


async def test_brain_profile_model_error_rolls_back_entire_create_or_patch(client, db):
    from lubanagent.persistence.models import BrainProfileAliasRow, BrainProfileRow

    invalid = await client.post(
        "/v1/brain-profiles", json={"key": "not-created", "name": "X", "model_key": "ghost"}
    )
    assert invalid.status_code == 404
    async with db.session() as session:
        assert (
            await session.scalar(
                select(BrainProfileRow).where(BrainProfileRow.key == "not-created")
            )
            is None
        )
        assert await session.get(BrainProfileAliasRow, "not-created") is None
    await _create_profile(client)
    invalid = await client.patch(
        "/v1/brain-profiles/reasoning",
        json={"key": "must-not-persist", "name": "Must not persist", "model_key": "ghost"},
    )
    assert invalid.status_code == 404
    catalog = (await client.get("/v1/brain-profiles")).json()["items"]
    slot = next(slot for slot in catalog if slot["key"] == "reasoning")
    assert slot["name"] == "推理专用" and slot["aliases"] == []
    async with db.session() as session:
        assert await session.get(BrainProfileAliasRow, "must-not-persist") is None


@pytest.mark.parametrize(
    "body",
    [
        {"key": "valid", "name": "   "},
        {"key": "valid", "name": "X", "unknown": "extra"},
        {"key": "valid", "name": "X", "env": "a" * 33},
        {"key": "valid", "name": "X", "description": "a" * 513},
    ],
)
async def test_brain_profile_metadata_validation(client, body):
    assert (await client.post("/v1/brain-profiles", json=body)).status_code == 422


@pytest.mark.parametrize("version", [0, -1])
@pytest.mark.parametrize("dependency", ["primary", "fallback", "summary"])
async def test_brain_slot_all_runnable_versions_guard_delete(client, version, dependency):
    if dependency == "summary":
        slot = "cheap"
        brain = {"profile": "smart"}
    else:
        slot = "reasoning"
        await _create_profile(client)
        brain = (
            {"profile": slot}
            if dependency == "primary"
            else {"profile": "smart", "fallback_profiles": [slot]}
        )
    key = f"nonpositive-{dependency}"
    created = await client.post(
        "/v1/agents",
        json={
            "key": key,
            "name": key,
            "version": version,
            "brain": brain,
            "memory": {"summary": dependency == "summary"},
        },
    )
    assert created.status_code == 201
    # 数字不大于零的版本目前仍可真实运行，不能因内部 stub 约定而漏查。
    first = await client.post(
        f"/v1/agents/{key}/runs", json={"input": "same input", "version": version}
    )
    assert first.status_code == 200 and '"type": "run_completed"' in first.text
    field = {
        "primary": "brain.profile",
        "fallback": "brain.fallback_profiles",
        "summary": "memory.summary",
    }[dependency]
    references = (await client.get(f"/v1/brain-profiles/{slot}/references")).json()["references"]
    assert references == [
        {"agent_key": key, "agent_name": key, "version": version, "current": True, "field": field}
    ]
    assert (await client.delete(f"/v1/brain-profiles/{slot}")).status_code == 409
    # 将当前版切到不依赖槽位，旧0/负数版本仍须保护。
    next_version = await client.post(
        f"/v1/agents/{key}/versions",
        json={
            **created.json(),
            "version": 1,
            "brain": {"profile": "fast"},
            "memory": {"summary": False},
        },
    )
    assert next_version.status_code == 201
    references = (await client.get(f"/v1/brain-profiles/{slot}/references")).json()["references"]
    assert references[0]["version"] == version and references[0]["current"] is False
    assert (await client.delete(f"/v1/brain-profiles/{slot}")).status_code == 409
    restored = await client.post(
        f"/v1/agents/{key}/runs", json={"input": "same input", "version": version}
    )
    assert restored.status_code == 200 and '"type": "run_completed"' in restored.text
