"""avatars 路由：上传/获取/移除 的 HTTP 契约（含版本化与安全头）。"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from lubanagent.api.routes import avatars as avatars_route
from lubanagent.avatar.manager import MAX_AVATAR_BYTES
from lubanagent.core import ErrorCode, LubanError
from lubanagent.persistence import SecurityRepository
from tests.lubanagent.api.conftest import create_demo_agent, make_client


class CountingUpload:
    filename = "m.png"

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        result, self.data = (
            (self.data, b"") if size < 0 else (self.data[:size], self.data[size:])
        )
        return result


async def test_upload_avatar_stops_at_small_quota(monkeypatch) -> None:
    monkeypatch.setattr(avatars_route, "MAX_AVATAR_BYTES", 5, raising=False)
    saves: list[tuple] = []

    class ManagerSpy:
        async def save(self, *args):
            saves.append(args)

    async def definition(*_):
        return SimpleNamespace()

    monkeypatch.setattr(avatars_route, "_current_definition", definition)

    request = Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(avatar_manager=ManagerSpy())),
    })
    file = CountingUpload(b"x" * (2 * 65536))
    with pytest.raises(LubanError) as error:
        await avatars_route.upload_avatar("demo", file, request, None)
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert file.read_sizes == [65536]
    assert saves == []


async def test_avatar_route_uses_manager_limit() -> None:
        assert avatars_route.MAX_AVATAR_BYTES == MAX_AVATAR_BYTES


SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
    b'<circle cx="32" cy="32" r="30" fill="#4f46e5"/></svg>'
)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # PNG 头 + 占位内容（后缀白名单校验，不做内容解析）
GLB_MAGIC = b"glTF" + b"\x00" * 64  # GLB magic + 占位内容


@pytest.fixture
async def client(db, registry, tmp_path):
    async with await make_client(
        db, registry, avatar_files_root=str(tmp_path / "avatars")
    ) as http_client:
        yield http_client


@pytest.fixture
def files_root(tmp_path):
    return tmp_path / "avatars"


async def test_upload_svg_creates_version_and_serves_file(client, files_root) -> None:
    await create_demo_agent(client, "cs-bot")
    response = await client.put(
        "/v1/agents/cs-bot/avatar",
        files=[("file", ("mascot.svg", SVG, "image/svg+xml"))],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 上传即版本：avatar 字段入定义、版本号 +1
    assert body["key"] == "cs-bot"
    assert body["avatar"] == "avatar.svg"
    assert body["version"] == 2

    # 文件落盘
    assert (files_root / "cs-bot" / "avatar.svg").read_bytes() == SVG

    # GET：字节一致 + Content-Type + 安全头（SVG 直开不执行脚本）
    fetched = await client.get("/v1/agents/cs-bot/avatar")
    assert fetched.status_code == 200
    assert fetched.content == SVG
    assert fetched.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in fetched.headers["content-security-policy"]
    assert fetched.headers["x-content-type-options"] == "nosniff"


async def test_upload_png_and_glb_media_types(client) -> None:
    await create_demo_agent(client, "cs-bot")
    for filename, data, media in [
        ("m.png", PNG_MAGIC, "image/png"),
        ("m.glb", GLB_MAGIC, "model/gltf-binary"),
        ("m.gltf", b"{}", "model/gltf+json"),
    ]:
        put = await client.put(
            "/v1/agents/cs-bot/avatar",
            files=[("file", (filename, data, "application/octet-stream"))],
        )
        assert put.status_code == 200, put.text
        fetched = await client.get("/v1/agents/cs-bot/avatar")
        assert fetched.status_code == 200
        assert fetched.headers["content-type"].startswith(media)


async def test_upload_replaces_previous_type_file(client, files_root) -> None:
    await create_demo_agent(client, "cs-bot")
    await client.put(
        "/v1/agents/cs-bot/avatar", files=[("file", ("m.svg", SVG, "image/svg+xml"))],
    )
    await client.put(
        "/v1/agents/cs-bot/avatar",
        files=[("file", ("m.glb", GLB_MAGIC, "application/octet-stream"))],
    )
    # 换类型后旧扩展名文件清掉，只剩当前
    assert (files_root / "cs-bot" / "avatar.glb").is_file()
    assert not (files_root / "cs-bot" / "avatar.svg").exists()


async def test_upload_bad_suffix_422(client) -> None:
    await create_demo_agent(client, "cs-bot")
    response = await client.put(
        "/v1/agents/cs-bot/avatar", files=[("file", ("m.gif", b"GIF89a", "image/gif"))]
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_upload_too_large_422(client) -> None:
    from lubanagent.avatar import MAX_AVATAR_BYTES

    await create_demo_agent(client, "cs-bot")
    response = await client.put(
        "/v1/agents/cs-bot/avatar",
        files=[("file", ("m.png", b"x" * (MAX_AVATAR_BYTES + 1), "image/png"))],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_upload_agent_not_found(client) -> None:
    response = await client.put(
        "/v1/agents/nope/avatar",
        files=[("file", ("m.svg", SVG, "image/svg+xml"))],
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_get_without_avatar_404(client) -> None:
    await create_demo_agent(client, "cs-bot")
    response = await client.get("/v1/agents/cs-bot/avatar")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AVATAR_NOT_FOUND"


async def test_get_missing_file_404(client, files_root) -> None:
    await create_demo_agent(client, "cs-bot")
    await client.put(
        "/v1/agents/cs-bot/avatar", files=[("file", ("m.svg", SVG, "image/svg+xml"))],
    )
    # 文件被外部删掉：定义引用了不存在的文件 → 同 404 AVATAR_NOT_FOUND
    (files_root / "cs-bot" / "avatar.svg").unlink()
    response = await client.get("/v1/agents/cs-bot/avatar")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "AVATAR_NOT_FOUND"


async def test_delete_clears_file_and_definition(client, files_root) -> None:
    await create_demo_agent(client, "cs-bot")
    await client.put(
        "/v1/agents/cs-bot/avatar", files=[("file", ("m.svg", SVG, "image/svg+xml"))],
    )

    response = await client.delete("/v1/agents/cs-bot/avatar")
    assert response.status_code == 200
    assert response.json() == {"removed": True}

    # 文件目录清空 + 当前定义 avatar 为空
    assert not (files_root / "cs-bot").exists()
    definition = (await client.get("/v1/agents/cs-bot")).json()
    assert definition["avatar"] is None
    assert definition["version"] == 3  # 创建 v1 + 上传 v2 + 移除 v3

    # 再删 → 404
    again = await client.delete("/v1/agents/cs-bot/avatar")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "AVATAR_NOT_FOUND"


async def test_avatar_display_toggle_retains_asset_and_version_history(
    client, files_root
) -> None:
    created = await create_demo_agent(client, "cs-bot")
    assert created["show_avatar"] is True
    uploaded = await client.put(
        "/v1/agents/cs-bot/avatar", files=[("file", ("m.svg", SVG, "image/svg+xml"))]
    )
    assert uploaded.status_code == 200
    visible = uploaded.json()

    hidden = await client.post(
        "/v1/agents/cs-bot/versions",
        json={**visible, "version": 3, "show_avatar": False},
    )
    assert hidden.status_code == 201, hidden.text
    reloaded = (await client.get("/v1/agents/cs-bot")).json()
    assert reloaded["show_avatar"] is False
    assert reloaded["avatar"] == "avatar.svg"
    assert reloaded["instructions"] == created["instructions"]
    assert (files_root / "cs-bot" / "avatar.svg").read_bytes() == SVG
    # 关闭是展示设置，不是文件删除或权限开关。
    fetched = await client.get("/v1/agents/cs-bot/avatar")
    assert fetched.status_code == 200 and fetched.content == SVG

    enabled = await client.post(
        "/v1/agents/cs-bot/versions",
        json={**reloaded, "version": 4, "show_avatar": True},
    )
    assert enabled.status_code == 201, enabled.text
    assert (await client.get("/v1/agents/cs-bot")).json()["show_avatar"] is True
    for version, expected in [(2, True), (3, False), (4, True)]:
        snapshot = (await client.get(f"/v1/agents/cs-bot/versions/{version}")).json()
        assert snapshot["definition"]["show_avatar"] is expected
        assert snapshot["definition"]["avatar"] == "avatar.svg"
    assert (files_root / "cs-bot" / "avatar.svg").read_bytes() == SVG


async def test_hidden_avatar_upload_and_remove_preserve_display_setting(client) -> None:
    created = await client.post(
        "/v1/agents", json={"key": "cs-bot", "name": "CS", "show_avatar": False}
    )
    assert created.status_code == 201
    assert created.json()["show_avatar"] is False
    for version, filename, data in [(2, "m.svg", SVG), (3, "m.png", PNG_MAGIC)]:
        uploaded = await client.put(
            "/v1/agents/cs-bot/avatar",
            files=[("file", (filename, data, "application/octet-stream"))],
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["version"] == version
        assert uploaded.json()["show_avatar"] is False
        fetched = await client.get("/v1/agents/cs-bot/avatar")
        assert fetched.status_code == 200 and fetched.content == data
    removed = await client.delete("/v1/agents/cs-bot/avatar")
    assert removed.status_code == 200
    current = (await client.get("/v1/agents/cs-bot")).json()
    assert current["version"] == 4
    assert current["avatar"] is None
    assert current["show_avatar"] is False


async def test_avatar_display_setting_does_not_bypass_rbac(db, registry, tmp_path) -> None:
    password = "viewer-test-password"
    async with db.session() as session:
        repo = SecurityRepository(session)
        await repo.seed()
        await repo.create_user(
            username="avatar-viewer", display_name="Viewer", password=password,
            role_keys=["viewer"],
        )
    async with await make_client(
        db, registry, auth_enabled=True, api_key="bootstrap-test-key",
        avatar_files_root=str(tmp_path / "avatars"),
    ) as client:
        admin = {"Authorization": "Bearer bootstrap-test-key"}
        created = await client.post(
            "/v1/agents", headers=admin,
            json={"key": "cs-bot", "name": "CS", "show_avatar": False},
        )
        assert created.status_code == 201
        uploaded = await client.put(
            "/v1/agents/cs-bot/avatar", headers=admin,
            files=[("file", ("m.svg", SVG, "image/svg+xml"))],
        )
        assert uploaded.status_code == 200
        login = await client.post(
            "/v1/auth/login", json={"username": "avatar-viewer", "password": password}
        )
        assert login.status_code == 200
        viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
        definition = (await client.get("/v1/agents/cs-bot", headers=viewer)).json()
        assert definition["show_avatar"] is False
        payload = {**definition, "version": 3, "show_avatar": True}
        assert (await client.post("/v1/agents/cs-bot/versions", json=payload)).status_code == 401
        denied = await client.post(
            "/v1/agents/cs-bot/versions", headers=viewer, json=payload
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
        asset = await client.get("/v1/agents/cs-bot/avatar", headers=viewer)
        assert asset.status_code == 200 and asset.content == SVG
        saved = await client.post(
            "/v1/agents/cs-bot/versions", headers=admin, json=payload
        )
        assert saved.status_code == 201
        assert saved.json()["definition"]["show_avatar"] is True
