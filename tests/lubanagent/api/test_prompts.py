"""Prompt 库 API：六端点 + key/版本 conflict + 404 区分。"""

PROMPT_BODY = {
    "key": "faq-prompt",
    "name": "FAQ Prompt",
    "description": "测试",
    "content": "你是 {company} 的客服 FAQ 专家。",
    "variables": {"company": "Luban 商店"},
    "version": 1,
}


async def test_prompt_crud_and_versions(client):
    create = await client.post("/v1/prompts", json=PROMPT_BODY)
    assert create.status_code == 201
    assert create.json()["version"] == 1

    # key 重复 → 409
    conflict = await client.post("/v1/prompts", json=PROMPT_BODY)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VALIDATION_ERROR"

    listed = (await client.get("/v1/prompts")).json()
    assert [p["key"] for p in listed] == ["faq-prompt"]

    got = (await client.get("/v1/prompts/faq-prompt")).json()
    assert got["content"] == "你是 {company} 的客服 FAQ 专家。"

    # 发 v2 → 版本序列 + 当前版推进
    v2 = await client.post(
        "/v1/prompts/faq-prompt/versions", json={**PROMPT_BODY, "version": 2, "content": "v2"}
    )
    assert v2.status_code == 201
    versions = (await client.get("/v1/prompts/faq-prompt/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert (await client.get("/v1/prompts/faq-prompt")).json()["version"] == 2

    # 指定版本快照
    snapshot = (await client.get("/v1/prompts/faq-prompt/versions/1")).json()
    assert snapshot["content"] == "你是 {company} 的客服 FAQ 专家。"

    # 版本重复 → 409
    dup = await client.post(
        "/v1/prompts/faq-prompt/versions", json={**PROMPT_BODY, "version": 2}
    )
    assert dup.status_code == 409


async def test_prompt_not_found_variants(client):
    missing = await client.get("/v1/prompts/ghost")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROMPT_NOT_FOUND"

    await client.post("/v1/prompts", json=PROMPT_BODY)
    missing_version = await client.get("/v1/prompts/faq-prompt/versions/99")
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "PROMPT_VERSION_NOT_FOUND"


async def test_agent_with_prompt_reference_snapshot(client):
    """Agent 引用 prompt 创建 → 快照内嵌解析后全文；prompt 发新版不影响旧快照。"""
    await client.post("/v1/prompts", json=PROMPT_BODY)
    create = await client.post(
        "/v1/agents",
        json={
            "key": "ref-agent",
            "name": "ref-agent",
            "prompt_key": "faq-prompt",
            "brain": {"profile": "smart"},
        },
    )
    assert create.status_code == 201
    body = create.json()
    # 快照落地：instructions = prompt content，变量合并，溯源标签正确
    assert body["instructions"] == "你是 {company} 的客服 FAQ 专家。"
    assert body["prompt_variables"] == {"company": "Luban 商店"}
    assert body["prompt_key"] == "faq-prompt"
    assert body["prompt_version"] == 1

    # prompt 发 v2 → 旧 Agent 版本快照不变（回放确定性）
    await client.post(
        "/v1/prompts/faq-prompt/versions", json={**PROMPT_BODY, "version": 2, "content": "v2"}
    )
    v1_snapshot = (await client.get("/v1/agents/ref-agent/versions/1")).json()
    assert v1_snapshot["definition"]["instructions"] == "你是 {company} 的客服 FAQ 专家。"
    assert v1_snapshot["definition"]["prompt_version"] == 1

    # 整份定义回传（含 prompt_version=1）→ 仍钉 v1：改其他字段发版不悄悄换 prompt 内容
    current = (await client.get("/v1/agents/ref-agent")).json()
    pinned = await client.post(
        "/v1/agents/ref-agent/versions",
        json={**current, "version": current["version"] + 1},
    )
    assert pinned.status_code == 201
    assert pinned.json()["definition"]["instructions"] == "你是 {company} 的客服 FAQ 专家。"
    assert pinned.json()["definition"]["prompt_version"] == 1

    # 显式清空 prompt_version（升级语义，分配对话框即此路径）→ 落地 prompt 当前版 v2
    upgraded = await client.post(
        "/v1/agents/ref-agent/versions",
        json={**current, "version": current["version"] + 2, "prompt_version": None},
    )
    assert upgraded.status_code == 201
    assert upgraded.json()["definition"]["instructions"] == "v2"
    assert upgraded.json()["definition"]["prompt_version"] == 2


async def test_agent_with_missing_prompt_reference(client):
    create = await client.post(
        "/v1/agents",
        json={"key": "bad-agent", "name": "bad", "prompt_key": "ghost"},
    )
    assert create.status_code == 404
    assert create.json()["error"]["code"] == "PROMPT_NOT_FOUND"
