"""KnowledgeSourceManager：托管源 CRUD/重切/恢复注册/路径安全/校验矩阵。"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import event, select

from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import Registry
from lubanagent.knowledge import (
    FakeEmbedding,
    KnowledgeSourceManager,
    KnowledgeStore,
)
from lubanagent.persistence import Base, Database
from lubanagent.persistence.models.knowledge import KnowledgeChunkRow, KnowledgeSourceRow


@pytest.fixture
async def db(tmp_path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.close()


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def manager(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding, registry: Registry, tmp_path: Path
) -> KnowledgeSourceManager:
    return KnowledgeSourceManager(
        db=db,
        store=store,
        embedder=embedder,
        registry=registry,
        files_root=tmp_path / "files",
    )


LONG_TEXT = "退货政策支持七天无理由退货。\n\n" * 80  # 足够切出多块
ALT_TEXT = "保修说明整机保修一年。\n\n" * 40


async def test_create_source_persists_files_chunks_and_registration(
    manager: KnowledgeSourceManager, registry: Registry, tmp_path: Path
) -> None:
    info = await manager.create_source(
        name="team-wiki",
        description="团队 wiki",
        chunk_size=500,
        overlap=50,
        files=[("guide.md", LONG_TEXT.encode()), ("notes.txt", ALT_TEXT.encode())],
    )
    assert info.managed and info.chunk_count > 1
    assert [f.filename for f in info.files] == ["guide.md", "notes.txt"]
    # 文件落盘
    assert (tmp_path / "files" / "team-wiki" / "guide.md").read_text(encoding="utf-8") == LONG_TEXT
    # connector 已注册，Agent YAML 可解析
    assert "team-wiki" in registry.list_knowledge()


@pytest.mark.parametrize(
    ("name", "filename", "content", "chunk_size", "error"),
    [
        ("invalid-docx", "broken.docx", b"not a zip", 500, "非法 DOCX"),
        ("chunk-quota", "large.txt", b"x" * 5001, 1, "灌库块数超过配额"),
    ],
    ids=["invalid-docx", "chunk-quota"],
)
async def test_create_source_rejection_leaves_no_files_or_database_state(
    manager: KnowledgeSourceManager,
    store: KnowledgeStore,
    registry: Registry,
    tmp_path: Path,
    name: str,
    filename: str,
    content: bytes,
    chunk_size: int,
    error: str,
) -> None:
    with pytest.raises(LubanError, match=error):
        await manager.create_source(
            name=name, description="", chunk_size=chunk_size, overlap=0,
            files=[(filename, content)],
        )
    assert not (tmp_path / "files" / name).exists()
    assert not list((tmp_path / "files").iterdir())
    assert await store.count_chunks(name) == 0
    assert await manager.get_source(name) is None
    assert name not in registry.list_knowledge()


async def test_create_source_embed_failure_cleans_own_files(
    manager: KnowledgeSourceManager, store: KnowledgeStore, embedder: FakeEmbedding,
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_embed(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("synthetic embed failure")

    monkeypatch.setattr(embedder, "embed", failed_embed)
    with pytest.raises(RuntimeError, match="synthetic embed failure"):
        await manager.create_source(
            name="embed-error", description="", chunk_size=500, overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert not list((tmp_path / "files").iterdir())
    assert await store.count_chunks("embed-error") == 0
    assert await manager.get_source("embed-error") is None
    assert "embed-error" not in registry.list_knowledge()


async def test_create_source_rejects_unmanaged_existing_directory_without_touching_it(
    manager: KnowledgeSourceManager, store: KnowledgeStore, tmp_path: Path,
) -> None:
    source_dir = tmp_path / "files" / "unmanaged"
    source_dir.mkdir(parents=True)
    sentinel = source_dir / "a.md"
    sentinel.write_bytes(b"existing data")
    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="unmanaged", description="", chunk_size=500, overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert sentinel.read_bytes() == b"existing data"
    assert list((tmp_path / "files").iterdir()) == [source_dir]
    assert await manager.get_source("unmanaged") is None
    assert await store.count_chunks("unmanaged") == 0


async def test_create_source_preserves_orphaned_chunks(
    manager: KnowledgeSourceManager, store: KnowledgeStore, tmp_path: Path,
) -> None:
    await store.add_chunks("orphaned", [{
        "chunk_index": 0, "content": "old", "embedding": [1.0],
        "metadata": {"filename": "old.md"},
    }])
    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="orphaned", description="", chunk_size=500, overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR
    assert [chunk["content"] for chunk in await store.list_chunks("orphaned")] == ["old"]
    assert not (tmp_path / "files" / "orphaned").exists()


async def test_create_source_does_not_delete_directory_created_during_ingest(
    manager: KnowledgeSourceManager, store: KnowledgeStore, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lubanagent.knowledge import manager as manager_module

    original_ingest = manager_module.ingest_files
    source_dir = tmp_path / "files" / "race"

    async def concurrent_ingest(*args: object, **kwargs: object) -> int:
        result = await original_ingest(*args, **kwargs)
        source_dir.mkdir()
        (source_dir / "sentinel.txt").write_text("untouched")
        return result

    monkeypatch.setattr(manager_module, "ingest_files", concurrent_ingest)
    with pytest.raises(FileExistsError):
        await manager.create_source(
            name="race", description="", chunk_size=500, overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert (source_dir / "sentinel.txt").read_text() == "untouched"
    assert await store.count_chunks("race") == 0
    assert await manager.get_source("race") is None
    assert list((tmp_path / "files").iterdir()) == [source_dir]


async def test_create_source_registration_failure_rolls_back_new_state(
    manager: KnowledgeSourceManager, store: KnowledgeStore, registry: Registry,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_register(name: str, connector: object) -> None:
        raise RuntimeError("synthetic register failure")

    monkeypatch.setattr(registry, "register_knowledge", failed_register)
    with pytest.raises(RuntimeError, match="synthetic register failure"):
        await manager.create_source(
            name="register-error", description="", chunk_size=500, overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert not list((tmp_path / "files").iterdir())
    assert await store.count_chunks("register-error") == 0
    assert await manager.get_source("register-error") is None
    assert "register-error" not in registry.list_knowledge()


async def test_create_source_preserves_ingest_filename_metadata(
    manager: KnowledgeSourceManager, store: KnowledgeStore,
) -> None:
    await manager.create_source(
        name="filenames", description="", chunk_size=500, overlap=50,
        files=[("../../guide.md", LONG_TEXT.encode()), ("notes.txt", ALT_TEXT.encode())],
    )
    chunks = await store.list_chunks("filenames")
    assert {chunk["metadata"]["filename"] for chunk in chunks} == {"guide.md", "notes.txt"}


async def test_concurrent_create_loser_preserves_winner(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lubanagent.knowledge import manager as manager_module

    first = KnowledgeSourceManager(db=db, store=store, embedder=embedder,
                                   registry=Registry(), files_root=tmp_path / "files")
    second = KnowledgeSourceManager(db=db, store=store, embedder=embedder,
                                    registry=Registry(), files_root=tmp_path / "files")
    original = manager_module.ingest_files
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def paused_ingest(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager_module, "ingest_files", paused_ingest)
    winner = asyncio.create_task(first.create_source(
        name="same-name", description="winner", chunk_size=500, overlap=50,
        files=[("winner.md", LONG_TEXT.encode())]))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        with pytest.raises(LubanError) as exc:
            await second.create_source(name="same-name", description="loser",
                                       chunk_size=500, overlap=50,
                                       files=[("loser.md", ALT_TEXT.encode())])
        assert exc.value.code == ErrorCode.CONFIG_ERROR
    finally:
        release.set()
    await winner
    chunks = await store.list_chunks("same-name")
    assert chunks and {item["metadata"]["filename"] for item in chunks} == {"winner.md"}
    assert (tmp_path / "files" / "same-name" / "winner.md").exists()
    assert not (tmp_path / "files" / "same-name" / "loser.md").exists()
    async with db.session() as session:
        rows = (await session.scalars(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "same-name"))).all()
    assert len(rows) == 1 and rows[0].description == "winner"


async def test_cancel_create_cleans_reservation_and_staged_files(
    manager: KnowledgeSourceManager, store: KnowledgeStore, embedder: FakeEmbedding,
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def paused_embed(texts):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(embedder, "embed", paused_embed)
    task = asyncio.create_task(manager.create_source(
        name="cancel-me", description="", chunk_size=500, overlap=50,
        files=[("a.md", LONG_TEXT.encode())]))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.count_chunks("cancel-me") == 0
    assert list((tmp_path / "files").iterdir()) == []
    async with db.session() as session:
        assert await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "cancel-me")) is None


async def test_cancel_after_reservation_commit_cleans_owned_row(
    manager: KnowledgeSourceManager, store: KnowledgeStore, db: Database,
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_transaction = store.source_transaction
    owner_task: asyncio.Task | None = None
    first = True

    @asynccontextmanager
    async def cancel_after_commit(source: str):
        nonlocal first
        async with original_transaction(source) as session:
            yield session
        if first:
            first = False
            assert owner_task is not None
            owner_task.cancel()
            await asyncio.sleep(0)

    monkeypatch.setattr(store, "source_transaction", cancel_after_commit)
    owner_task = asyncio.create_task(manager.create_source(
        name="commit-cancel", description="", chunk_size=500, overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    ))
    with pytest.raises(asyncio.CancelledError):
        await owner_task
    async with db.session() as session:
        assert await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "commit-cancel")) is None
    assert await store.count_chunks("commit-cancel") == 0
    assert not list((tmp_path / "files").iterdir())
    assert "commit-cancel" not in registry.list_knowledge()


async def test_cancel_after_final_publish_keeps_committed_source(
    manager: KnowledgeSourceManager, store: KnowledgeStore, db: Database,
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_transaction = store.source_transaction
    owner_task: asyncio.Task | None = None
    calls = 0

    @asynccontextmanager
    async def cancel_after_publish(source: str):
        nonlocal calls
        async with original_transaction(source) as session:
            yield session
        calls += 1
        if calls == 3:  # 预留提交、块提交、最终清单提交
            assert owner_task is not None
            owner_task.cancel()
            await asyncio.sleep(0)

    monkeypatch.setattr(store, "source_transaction", cancel_after_publish)
    owner_task = asyncio.create_task(manager.create_source(
        name="published", description="", chunk_size=500, overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    ))
    with pytest.raises(asyncio.CancelledError):
        await owner_task
    async with db.session() as session:
        row = await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "published"))
        assert row is not None
        assert row.files == [{"filename": "a.md", "size": len(LONG_TEXT.encode())}]
        assert row.chunk_count > 0
    assert await store.count_chunks("published") == row.chunk_count
    assert (tmp_path / "files" / "published" / "a.md").read_text() == LONG_TEXT
    assert "published" in registry.list_knowledge()
    assert sorted(path.name for path in (tmp_path / "files").iterdir()) == ["published"]


async def test_pending_reservation_rejects_unowned_chunk_writers(
    manager: KnowledgeSourceManager, store: KnowledgeStore, embedder: FakeEmbedding,
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def paused_embed(texts: list[str]) -> list[list[float]]:
        entered.set()
        await release.wait()
        return await FakeEmbedding().embed(texts)

    monkeypatch.setattr(embedder, "embed", paused_embed)
    task = asyncio.create_task(manager.create_source(
        name="reserved", description="", chunk_size=500, overlap=50,
        files=[("owned.md", LONG_TEXT.encode())],
    ))
    await asyncio.wait_for(entered.wait(), 5)
    chunk = {"chunk_index": 0, "content": "outsider", "embedding": [1.0]}
    try:
        for write in (
            store.add_chunks("reserved", [chunk]),
            store.write_chunks("reserved", [chunk], replace=True, max_chunks=10),
            store.delete_source("reserved"),
        ):
            with pytest.raises(LubanError, match="正在创建"):
                await write
        async with db.session() as session:
            row = await session.scalar(select(KnowledgeSourceRow).where(
                KnowledgeSourceRow.name == "reserved"))
            assert row is not None and row.files == []
        with pytest.raises(LubanError, match="预留已失效"):
            await store.write_chunks("reserved", [chunk], replace=True, max_chunks=10,
                                     reservation_id=row.id + 1)
        assert await store.count_chunks("reserved") == 0
    finally:
        release.set()
    await task
    assert (tmp_path / "files" / "reserved" / "owned.md").exists()
    assert {item["metadata"]["filename"] for item in await store.list_chunks("reserved")} == {
        "owned.md"
    }


async def test_create_source_rejects_duplicate_name(
    manager: KnowledgeSourceManager,
) -> None:
    await manager.create_source(
        name="dup",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="dup",
            description="",
            chunk_size=500,
            overlap=50,
            files=[("a.md", LONG_TEXT.encode())],
        )
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


async def test_create_source_rejects_bad_name(manager: KnowledgeSourceManager) -> None:
    for bad in ["A-B", "a", "-ab", "a b", "../escape", "x" * 65]:
        with pytest.raises(LubanError) as exc_info:
            await manager.create_source(
                name=bad,
                description="",
                chunk_size=500,
                overlap=50,
                files=[("a.md", b"x")],
            )
        assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_create_source_sanitizes_traversal_filename(
    manager: KnowledgeSourceManager, tmp_path: Path
) -> None:
    info = await manager.create_source(
        name="safe",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("../../evil.md", b"content")],
    )
    # 只存 basename，目录内无越界文件
    assert info.files[0].filename == "evil.md"
    assert (tmp_path / "files" / "safe" / "evil.md").exists()
    assert not (tmp_path / "evil.md").exists()


async def test_create_source_rejects_bad_suffix_and_size_caps(
    manager: KnowledgeSourceManager,
) -> None:
    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="bad-suffix",
            description="",
            chunk_size=500,
            overlap=50,
            files=[("a.exe", b"x")],
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["supported"] is not None

    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="too-big",
            description="",
            chunk_size=500,
            overlap=50,
            files=[("a.md", b"x" * (10 * 1024 * 1024 + 1))],
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="empty-files",
            description="",
            chunk_size=500,
            overlap=50,
            files=[],
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_create_source_rejects_invalid_chunk_params(
    manager: KnowledgeSourceManager,
) -> None:
    with pytest.raises(LubanError) as exc_info:
        await manager.create_source(
            name="bad-params",
            description="",
            chunk_size=50,
            overlap=50,
            files=[("a.md", b"x")],
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_list_sources_merges_managed_and_unmanaged(
    manager: KnowledgeSourceManager, registry: Registry, store: KnowledgeStore
) -> None:
    from lubanagent.knowledge import SqlKnowledgeConnector

    connector = SqlKnowledgeConnector("seed-doc", store=store, embedder=FakeEmbedding())
    registry.register_knowledge("seed-doc", connector)
    await manager.create_source(
        name="managed-one",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    infos = {info.name: info for info in await manager.list_sources()}
    assert infos["managed-one"].managed is True
    assert infos["managed-one"].chunk_count > 0
    assert infos["seed-doc"].managed is False
    assert infos["seed-doc"].chunk_size is None


async def test_rechunk_changes_chunk_count_and_params(
    manager: KnowledgeSourceManager,
) -> None:
    info = await manager.create_source(
        name="rechunk-me",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    finer = await manager.rechunk("rechunk-me", chunk_size=100, overlap=10)
    assert finer.chunk_size == 100 and finer.overlap == 10
    assert finer.chunk_count > info.chunk_count


async def test_rechunk_insert_failure_preserves_row_files_and_chunks(
    manager: KnowledgeSourceManager, store: KnowledgeStore, db: Database, tmp_path: Path
) -> None:
    await manager.create_source(name="rechunk-fail", description="", chunk_size=500,
                                overlap=50, files=[("a.md", LONG_TEXT.encode())])
    before = await store.list_chunks("rechunk-fail")
    path = tmp_path / "files" / "rechunk-fail" / "a.md"
    file_bytes = path.read_bytes()
    async with db.session() as session:
        row = await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "rechunk-fail"))
        params = (row.chunk_size, row.overlap, row.chunk_count, row.files)

    def fail_insert(mapper, connection, target):
        if target.source == "rechunk-fail":
            raise RuntimeError("synthetic insert failure")

    event.listen(KnowledgeChunkRow, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic insert failure"):
            await manager.rechunk("rechunk-fail", chunk_size=100, overlap=0)
    finally:
        event.remove(KnowledgeChunkRow, "before_insert", fail_insert)
    assert await store.list_chunks("rechunk-fail") == before
    assert path.read_bytes() == file_bytes
    async with db.session() as session:
        row = await session.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.name == "rechunk-fail"))
        assert (row.chunk_size, row.overlap, row.chunk_count, row.files) == params


async def test_rechunk_missing_files_raises(
    manager: KnowledgeSourceManager, tmp_path: Path
) -> None:
    import shutil

    await manager.create_source(
        name="lost-files",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    shutil.rmtree(tmp_path / "files" / "lost-files")
    with pytest.raises(LubanError) as exc_info:
        await manager.rechunk("lost-files", chunk_size=200, overlap=20)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_delete_source_clears_everything(
    manager: KnowledgeSourceManager, registry: Registry, store: KnowledgeStore, tmp_path: Path
) -> None:
    await manager.create_source(
        name="bye",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    await manager.delete_source("bye")
    assert await store.count_chunks("bye") == 0
    assert not (tmp_path / "files" / "bye").exists()
    assert "bye" not in registry.list_knowledge()
    assert await manager.get_source("bye") is None
    # 幂等性由 API 层 404 保证；重复删除此处报 NOT_FOUND
    with pytest.raises(LubanError) as exc_info:
        await manager.delete_source("bye")
    assert exc_info.value.code == ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND


async def test_delete_unmanaged_rejected(
    manager: KnowledgeSourceManager, registry: Registry, store: KnowledgeStore
) -> None:
    from lubanagent.knowledge import SqlKnowledgeConnector

    connector = SqlKnowledgeConnector("seed-doc", store=store, embedder=FakeEmbedding())
    registry.register_knowledge("seed-doc", connector)
    with pytest.raises(LubanError) as exc_info:
        await manager.delete_source("seed-doc")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_restore_idempotent_and_survives_restart(
    db: Database, store: KnowledgeStore, embedder: FakeEmbedding, tmp_path: Path
) -> None:
    registry1 = Registry()
    manager1 = KnowledgeSourceManager(
        db=db, store=store, embedder=embedder, registry=registry1, files_root=tmp_path / "files"
    )
    await manager1.create_source(
        name="restart-me",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    # 模拟重启：新 registry + 新 manager（同 db/files_root）
    registry2 = Registry()
    manager2 = KnowledgeSourceManager(
        db=db, store=store, embedder=embedder, registry=registry2, files_root=tmp_path / "files"
    )
    registered = await manager2.restore()
    assert registered == ["restart-me"]
    assert "restart-me" in registry2.list_knowledge()
    # 二次 restore 幂等：已注册跳过，不报 CONFIG_ERROR
    assert await manager2.restore() == []
    # ensure_restored 后 list 不再触发重复注册
    infos = await manager2.list_sources()
    assert [info.name for info in infos] == ["restart-me"]


async def test_search_filters_unknown_source(manager: KnowledgeSourceManager) -> None:
    await manager.create_source(
        name="searchable",
        description="",
        chunk_size=500,
        overlap=50,
        files=[("a.md", LONG_TEXT.encode())],
    )
    hits = await manager.search("searchable", "退货政策", top_k=5)
    assert hits and all(hit["source"] == "searchable" for hit in hits)
    assert hits[0]["score"] >= hits[-1]["score"]
    with pytest.raises(LubanError) as exc_info:
        await manager.search("nope", "查询", top_k=5)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
