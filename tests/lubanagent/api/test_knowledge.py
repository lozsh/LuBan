"""knowledge 路由：上传/列表/删除/重切/切块查看/检索测试 的 HTTP 契约。"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from lubanagent.api.routes import knowledge as knowledge_route
from lubanagent.core import ErrorCode, LubanError
from lubanagent.knowledge.manager import MAX_FILE_BYTES, MAX_TOTAL_BYTES
from tests.lubanagent.api.conftest import make_client


class CountingUpload:
    def __init__(self, data: bytes = b"x", filename: str = "file.md") -> None:
        self.data = data
        self.filename = filename
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        result, self.data = (
            (self.data, b"") if size < 0 else (self.data[:size], self.data[size:])
        )
        return result


class ManagerSpy:
    def __init__(self) -> None:
        self.created = 0
        self.lookups = 0
        self.payload = None

    async def get_source(self, name: str) -> None:
        self.lookups += 1
        return None

    async def create_source(self, **kwargs):
        self.created += 1
        self.payload = kwargs["files"]
        return SimpleNamespace(
            name=kwargs["name"], description="", chunk_size=500, overlap=50,
            files=[], chunk_count=0, managed=True, created_at=None, updated_at=None,
        )


def _route_request(manager: ManagerSpy) -> Request:
    return Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(knowledge_manager=manager)),
    })


async def _create_direct(manager: ManagerSpy, files: list[CountingUpload]):
    return await knowledge_route.create_knowledge_source(
        _route_request(manager), files, "test-source", "", 500, 50
    )


async def test_upload_file_quota_stops_reading_and_skips_manager(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_route, "_MAX_FILE_BYTES", 5, raising=False)
    manager = ManagerSpy()
    first, next_file = CountingUpload(b"x" * (2 * 65536)), CountingUpload()
    with pytest.raises(LubanError) as error:
        await _create_direct(manager, [first, next_file])
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert first.read_sizes == [65536]
    assert next_file.read_sizes == []
    assert manager.created == 0
    assert manager.lookups == 0


async def test_upload_total_quota_stops_at_crossing_file(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_route, "_MAX_FILE_BYTES", 65536, raising=False)
    monkeypatch.setattr(knowledge_route, "_MAX_TOTAL_BYTES", 5, raising=False)
    manager = ManagerSpy()
    first, second, last = CountingUpload(b"abcd"), CountingUpload(b"ef"), CountingUpload()
    with pytest.raises(LubanError) as error:
        await _create_direct(manager, [first, second, last])
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert first.read_sizes == [65536, 65536]
    assert second.read_sizes == [65536]
    assert last.read_sizes == []
    assert manager.created == 0
    assert manager.lookups == 0


async def test_upload_too_many_files_reads_nothing(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_route, "_MAX_FILES", 2, raising=False)
    manager = ManagerSpy()
    files = [CountingUpload() for _ in range(3)]
    with pytest.raises(LubanError) as error:
        await _create_direct(manager, files)
    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert [file.read_sizes for file in files] == [[], [], []]
    assert manager.created == 0
    assert manager.lookups == 0


async def test_upload_normal_payload_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_route, "_MAX_FILE_BYTES", 65536, raising=False)
    monkeypatch.setattr(knowledge_route, "_MAX_TOTAL_BYTES", 65536, raising=False)
    manager = ManagerSpy()
    files = [CountingUpload(b"ab", "a.md"), CountingUpload(b"cd", "b.md")]
    result = await _create_direct(manager, files)
    assert result.name == "test-source"
    assert manager.created == 1
    assert manager.payload == [("a.md", b"ab"), ("b.md", b"cd")]
    assert [file.read_sizes for file in files] == [[65536, 65536], [65536, 65536]]

def test_upload_quota_matches_manager_contract() -> None:
    assert knowledge_route._MAX_FILE_BYTES == MAX_FILE_BYTES
    assert knowledge_route._MAX_TOTAL_BYTES == MAX_TOTAL_BYTES
    assert knowledge_route._MAX_FILES == 50


LONG_TEXT = "退货政策支持七天无理由退货。\n\n" * 80


@pytest.fixture
async def client(db, registry, tmp_path):
    async with await make_client(
        db, registry, knowledge_files_root=str(tmp_path / "kfiles")
    ) as http_client:
        yield http_client


async def test_upload_and_list_sources(client) -> None:
    response = await client.post(
        "/v1/knowledge/sources",
        data={"name": "team-wiki", "description": "团队 wiki",
              "chunk_size": "500", "overlap": "50"},
        files=[("files", ("guide.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "team-wiki"
    assert body["managed"] is True
    assert body["chunk_count"] > 0
    assert body["files"] == [{"filename": "guide.md", "size": len(LONG_TEXT.encode())}]

    listed = await client.get("/v1/knowledge/sources")
    assert listed.status_code == 200
    names = {item["name"]: item for item in listed.json()}
    assert names["team-wiki"]["chunk_count"] == body["chunk_count"]


async def test_upload_merges_unmanaged_registry_source(client, registry) -> None:
    from lubanagent.knowledge import FakeEmbedding, KnowledgeStore, SqlKnowledgeConnector

    store = KnowledgeStore(client._transport.app.state.ctx.db)
    connector = SqlKnowledgeConnector("seed-doc", store=store, embedder=FakeEmbedding())
    registry.register_knowledge("seed-doc", connector)

    response = await client.get("/v1/knowledge/sources")
    items = {item["name"]: item for item in response.json()}
    assert items["seed-doc"]["managed"] is False
    assert items["seed-doc"]["chunk_size"] is None


async def test_upload_duplicate_name_409(client) -> None:
    payload = {
        "data": {"name": "dup"},
        "files": [("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    }
    first = await client.post("/v1/knowledge/sources", **payload)
    assert first.status_code == 201, first.text
    second = await client.post("/v1/knowledge/sources", **payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_upload_bad_suffix_422(client) -> None:
    response = await client.post(
        "/v1/knowledge/sources",
        data={"name": "bad-suffix"},
        files=[("files", ("a.exe", b"binary", "application/octet-stream"))],
    )
    assert response.status_code == 422
    assert "supported" in response.json()["error"]["details"]


async def test_upload_oversize_422(client) -> None:
    big = b"x" * (10 * 1024 * 1024 + 1)
    response = await client.post(
        "/v1/knowledge/sources",
        data={"name": "too-big"},
        files=[("files", ("a.md", big, "text/markdown"))],
    )
    assert response.status_code == 422


async def test_upload_bad_chunk_params_422(client) -> None:
    response = await client.post(
        "/v1/knowledge/sources",
        data={"name": "bad-params", "chunk_size": "50", "overlap": "50"},
        files=[("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    assert response.status_code == 422


async def test_upload_invalid_name_422(client) -> None:
    response = await client.post(
        "/v1/knowledge/sources",
        data={"name": "Bad Name"},
        files=[("files", ("a.md", b"x", "text/markdown"))],
    )
    assert response.status_code == 422


async def test_chunks_listing_honors_limit(client) -> None:
    await client.post(
        "/v1/knowledge/sources",
        data={"name": "chunky"},
        files=[("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    full = await client.get("/v1/knowledge/sources/chunky/chunks")
    assert full.status_code == 200
    all_chunks = full.json()
    assert all_chunks and "embedding" not in all_chunks[0]
    assert all_chunks[0]["content"]

    limited = await client.get("/v1/knowledge/sources/chunky/chunks?limit=1")
    assert limited.status_code == 200
    assert len(limited.json()) == 1

    missing = await client.get("/v1/knowledge/sources/nope/chunks")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "KNOWLEDGE_SOURCE_NOT_FOUND"


async def test_rechunk_changes_chunk_count(client) -> None:
    await client.post(
        "/v1/knowledge/sources",
        data={"name": "rechunk-me"},
        files=[("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    before = (await client.get("/v1/knowledge/sources")).json()
    before_count = next(item["chunk_count"] for item in before if item["name"] == "rechunk-me")

    response = await client.post(
        "/v1/knowledge/sources/rechunk-me/rechunk",
        json={"chunk_size": 100, "overlap": 10},
    )
    assert response.status_code == 200, response.text
    assert response.json()["chunk_size"] == 100
    assert response.json()["chunk_count"] > before_count

    missing = await client.post(
        "/v1/knowledge/sources/ghost/rechunk", json={"chunk_size": 100, "overlap": 10}
    )
    assert missing.status_code == 404


async def test_delete_source_then_404(client, tmp_path) -> None:
    await client.post(
        "/v1/knowledge/sources",
        data={"name": "bye"},
        files=[("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    first = await client.delete("/v1/knowledge/sources/bye")
    assert first.status_code == 204
    again = await client.delete("/v1/knowledge/sources/bye")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "KNOWLEDGE_SOURCE_NOT_FOUND"
    # 落盘目录已清
    assert not (tmp_path / "kfiles" / "bye").exists()


async def test_delete_unmanaged_422(client, registry) -> None:
    from lubanagent.knowledge import FakeEmbedding, KnowledgeStore, SqlKnowledgeConnector

    store = KnowledgeStore(client._transport.app.state.ctx.db)
    connector = SqlKnowledgeConnector("seed-doc", store=store, embedder=FakeEmbedding())
    registry.register_knowledge("seed-doc", connector)
    response = await client.delete("/v1/knowledge/sources/seed-doc")
    assert response.status_code == 422


async def test_query_returns_scored_hits(client) -> None:
    await client.post(
        "/v1/knowledge/sources",
        data={"name": "searchable"},
        files=[("files", ("a.md", LONG_TEXT.encode(), "text/markdown"))],
    )
    response = await client.post(
        "/v1/knowledge/query", json={"query": "退货政策", "source": "searchable", "top_k": 3}
    )
    assert response.status_code == 200, response.text
    hits = response.json()
    assert hits and all(hit["source"] == "searchable" for hit in hits)
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert len(hits) <= 3

    unknown = await client.post(
        "/v1/knowledge/query", json={"query": "退货", "source": "ghost", "top_k": 3}
    )
    assert unknown.status_code == 422
