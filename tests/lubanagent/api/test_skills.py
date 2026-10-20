"""skills 路由：list 读 app.state.skill_registry、POST 上传 zip、DELETE。"""

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from lubanagent.api.routes import skills as skills_route
from lubanagent.app import create_app
from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import Registry
from lubanagent.persistence import Base, Database
from lubanagent.skill import SkillRegistry

from .conftest import make_settings


class CountingUpload:
    def __init__(self, data: bytes, filename: str = "file.zip") -> None:
        self.data = data
        self.filename = filename
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            result, self.data = self.data, b""
        else:
            result, self.data = self.data[:size], self.data[size:]
        return result


def _route_request(**state: object) -> Request:
    return Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(**state))})


async def test_upload_skill_stops_at_small_quota(monkeypatch) -> None:
    monkeypatch.setattr(skills_route, "_MAX_SKILL_ZIP_BYTES", 5)

    class RegistrySpy:
        calls = 0

        async def install(self, data: bytes) -> None:
            self.calls += 1

    registry = RegistrySpy()
    file = CountingUpload(b"x" * (2 * 65536))
    with pytest.raises(LubanError) as error:
        await skills_route.upload_skill(_route_request(skill_registry=registry), file)
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert file.read_sizes == [65536]
    assert registry.calls == 0


async def test_upload_skill_streams_valid_payload(monkeypatch) -> None:
    monkeypatch.setattr(skills_route, "_MAX_SKILL_ZIP_BYTES", 2 * 65536)

    class RegistrySpy:
        received = None

        async def install(self, data: bytes):
            self.received = data
            return SimpleNamespace(
                meta=SimpleNamespace(name="demo", version="1"), model_dump=lambda **_: {}
            )

    registry = RegistrySpy()
    file = CountingUpload(b"x" * (65536 + 3))
    result = await skills_route.upload_skill(_route_request(skill_registry=registry), file)
    assert result.key == "demo"
    assert registry.received == b"x" * (65536 + 3)
    assert file.read_sizes == [65536, 65536, 65536]


_SKILL_MD = '---\nname: {name}\ndescription: {desc}\nversion: "{ver}"\n---\n{body}\n'


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _skill_zip(name: str, **kw: str) -> bytes:
    kw = {"desc": "x", "ver": "1", "body": "x", **kw}
    return _zip_bytes({f"{name}/SKILL.md": _SKILL_MD.format(name=name, **kw)})


def _file(name: str, data: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, data, "application/zip")}


@pytest.fixture
async def skills_client(tmp_path: Path):
    """装配带 skill_registry 的 app + client（模拟 app/main.py 挂 app.state）。"""
    settings = make_settings(skills_root=str(tmp_path))
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.scan()
    application = create_app(settings=settings, registry=Registry(), db=database)
    application.state.skill_registry = registry  # app/main.py 同款挂载
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client
    await database.close()


async def test_list_empty(skills_client: httpx.AsyncClient) -> None:
    response = await skills_client.get("/v1/skills")
    assert response.status_code == 200
    assert response.json() == []


async def test_upload_then_list(skills_client: httpx.AsyncClient) -> None:
    response = await skills_client.post(
        "/v1/skills", files=_file("demo.zip", _skill_zip("demo", desc="d", ver="1.0"))
    )
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["key"] == "demo"
    assert out["version"] == "1.0"

    listed = await skills_client.get("/v1/skills")
    assert [s["key"] for s in listed.json()] == ["demo"]


async def test_upload_zip_slip_422(skills_client: httpx.AsyncClient) -> None:
    data = _zip_bytes(
        {
            "evil/SKILL.md": _SKILL_MD.format(name="evil", desc="x", ver="1", body="x"),
            "../../evil.txt": "pwned",
        }
    )
    response = await skills_client.post("/v1/skills", files=_file("evil.zip", data))
    assert response.status_code == 422


async def test_upload_bad_zip_422(skills_client: httpx.AsyncClient) -> None:
    response = await skills_client.post(
        "/v1/skills", files=_file("bad.zip", b"not a zip")
    )
    assert response.status_code == 422


async def test_delete_then_gone(skills_client: httpx.AsyncClient) -> None:
    await skills_client.post("/v1/skills", files=_file("demo.zip", _skill_zip("demo")))
    response = await skills_client.delete("/v1/skills/demo")
    assert response.status_code == 204
    listed = await skills_client.get("/v1/skills")
    assert listed.json() == []


async def test_delete_not_found_422(skills_client: httpx.AsyncClient) -> None:
    response = await skills_client.delete("/v1/skills/ghost")
    assert response.status_code == 422
