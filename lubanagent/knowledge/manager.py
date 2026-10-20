"""KnowledgeSourceManager：工作台知识源全生命周期（落盘/灌库/注册/重切/删除/检索测试）。

只管 API 创建的托管源（knowledge_sources 表）；代码种子源（project-docs 等）不在表内，
列表时与 registry 合并展示（managed=False，参数不可改、不可删）。
路由层经 app.state.knowledge_manager 鸭子类型访问（api→knowledge 被 import-linter 禁止）。
"""

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from lubanagent.core import ErrorCode, LubanError
from lubanagent.extension import Registry
from lubanagent.knowledge.embedding import EmbeddingProvider
from lubanagent.knowledge.ingest import SUPPORTED_SUFFIXES, ingest_files
from lubanagent.knowledge.retrieve import SqlKnowledgeConnector
from lubanagent.knowledge.store import KnowledgeStore
from lubanagent.persistence import Database
from lubanagent.persistence.models.knowledge import KnowledgeChunkRow, KnowledgeSourceRow

# 源名规则：与 Agent YAML key 同风格（小写字母数字开头，可 - _，2-64 位）
_SOURCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
MAX_FILE_BYTES = 10 * 1024 * 1024  # 单文件上限（同 Skill zip 上限）
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 单次上传总量上限


@dataclass(frozen=True)
class StoredFile:
    filename: str
    size: int


@dataclass(frozen=True)
class SourceInfo:
    name: str
    description: str
    chunk_size: int | None  # 非托管（代码种子）源无参数
    overlap: int | None
    files: list[StoredFile] = field(default_factory=list)
    chunk_count: int = 0
    managed: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _row_to_info(row: KnowledgeSourceRow) -> SourceInfo:
    return SourceInfo(
        name=row.name,
        description=row.description,
        chunk_size=row.chunk_size,
        overlap=row.overlap,
        files=[StoredFile(filename=f["filename"], size=f["size"]) for f in row.files],
        chunk_count=row.chunk_count,
        managed=True,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class KnowledgeSourceManager:
    """托管知识源 CRUD + 重切 + 恢复注册；文件落盘 files_root/<name>/，重灌从盘上重读。"""

    def __init__(
        self,
        *,
        db: Database,
        store: KnowledgeStore,
        embedder: EmbeddingProvider,
        registry: Registry,
        files_root: Path,
    ) -> None:
        self._db = db
        self._store = store
        self._embedder = embedder
        self._registry = registry
        self._files_root = files_root
        self._restored = False

    # ---- 启动恢复 ----

    async def ensure_restored(self) -> None:
        """幂等：首次调用读全表逐行注册 connector（已注册的名字跳过）。"""
        if self._restored:
            return
        await self.restore()
        self._restored = True

    async def restore(self) -> list[str]:
        """读全表注册全部托管源，返回本次注册的名字（测试用；不置 _restored 标志）。"""
        registered: list[str] = []
        async with self._db.session() as session:
            rows = (await session.scalars(select(KnowledgeSourceRow))).all()
        for row in rows:
            if not row.files:  # 尚在创建中的预留行；崩溃遗留需人工核查清理。
                continue
            if row.name in self._registry.list_knowledge():
                continue
            self._registry.register_knowledge(
                row.name, self._make_connector(row.name)
            )
            registered.append(row.name)
        return registered

    def _make_connector(self, name: str) -> SqlKnowledgeConnector:
        return SqlKnowledgeConnector(name, store=self._store, embedder=self._embedder)

    # ---- 查询 ----

    async def list_sources(self) -> list[SourceInfo]:
        """DB 托管行 ∪ registry 里不在 DB 的名字（代码种子源，managed=False）。"""
        await self.ensure_restored()
        async with self._db.session() as session:
            rows = (
                await session.scalars(
                    select(KnowledgeSourceRow).order_by(KnowledgeSourceRow.name)
                )
            ).all()
        infos = [_row_to_info(row) for row in rows if row.files]
        managed_names = {info.name for info in infos}
        for name in self._registry.list_knowledge():
            if name in managed_names:
                continue
            infos.append(
                SourceInfo(
                    name=name,
                    description="",
                    chunk_size=None,
                    overlap=None,
                    files=[],
                    chunk_count=await self._store.count_chunks(name),
                    managed=False,
                )
            )
        return infos

    async def get_source(self, name: str) -> SourceInfo | None:
        await self.ensure_restored()
        async with self._db.session() as session:
            row = await session.scalar(
                select(KnowledgeSourceRow).where(KnowledgeSourceRow.name == name)
            )
        if row is not None and row.files:
            return _row_to_info(row)
        if name in self._registry.list_knowledge():
            return SourceInfo(
                name=name,
                description="",
                chunk_size=None,
                overlap=None,
                files=[],
                chunk_count=await self._store.count_chunks(name),
                managed=False,
            )
        return None

    async def list_chunks(self, name: str, *, limit: int) -> list[dict]:
        return await self._store.list_chunks(name, limit=limit)

    async def search(self, source: str | None, query: str, *, top_k: int) -> list[dict]:
        """检索测试：embed 查询后按源过滤（None=全源）；未知源报错。"""
        if source is not None:
            known = {info.name for info in await self.list_sources()}
            if source not in known:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"知识源未注册: {source}",
                    details={"source": source},
                )
        embeddings = await self._embedder.embed([query])
        return await self._store.search(embeddings[0], source=source, top_k=top_k)

    # ---- 写操作 ----

    async def create_source(
        self,
        *,
        name: str,
        description: str,
        chunk_size: int,
        overlap: int,
        files: list[tuple[str, bytes]],
    ) -> SourceInfo:
        """预留唯一行 → 同根暂存解析/灌库 → 发布文件 → 提交行/注册；失败清理自有资源。

        DB 与文件系统非同一事务；进程崩溃留下的空 files 预留行不会自动回收，
        须人工核查后清理。正常异常及取消路径清理本次创建。
        """
        self._validate_name(name)
        self._validate_chunk_params(chunk_size, overlap)
        if not files:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "至少上传一个文件", details={"files": []})
        stored = self._validate_files(files)

        source_dir = self._source_dir(name)
        await asyncio.to_thread(self._files_root.mkdir, parents=True, exist_ok=True)
        staged_dir: Path | None = None
        owned_id: int | None = None
        published = False
        registered = False
        finished = False
        async def reserve() -> int:
            # 预留与提交不可被调用方取消打断，否则事务退出时所有权不明。
            async with self._store.source_transaction(name) as session:
                existing = await session.scalar(select(KnowledgeSourceRow).where(
                    KnowledgeSourceRow.name == name
                ))
                if existing is not None or source_dir.exists() or await session.scalar(
                    select(KnowledgeChunkRow.id).where(KnowledgeChunkRow.source == name).limit(1)
                ) is not None or name in self._registry.list_knowledge():
                    raise LubanError(ErrorCode.CONFIG_ERROR, f"知识源数据已存在: {name}",
                                     details={"source": name})
                row = KnowledgeSourceRow(name=name, description=description,
                                         chunk_size=chunk_size, overlap=overlap,
                                         files=[], chunk_count=0)
                session.add(row)
                await session.flush()
                return row.id

        try:
            # shield 让事务完成；取消时取回真正已提交的 id，再清理自己的行。
            reserve_task = asyncio.create_task(reserve())
            try:
                owned_id = await asyncio.shield(reserve_task)
            except asyncio.CancelledError:
                owned_id = await reserve_task
                raise

            # 创建与记录所有权之间不跨 await；取消不能在 mkdtemp 完成后留下未知目录。
            staged_dir = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=self._files_root))
            for stored_file, (_, data) in zip(stored, files, strict=True):
                await asyncio.to_thread((staged_dir / stored_file.filename).write_bytes, data)
            # ingest_files 用 path.name 填 metadata.filename；暂存目录不影响文件名溯源。
            paths = [staged_dir / f.filename for f in stored]
            chunk_count = await ingest_files(
                paths,
                source=name,
                store=self._store,
                embedder=self._embedder,
                chunk_size=chunk_size,
                overlap=overlap,
                replace=True,
                reservation_id=owned_id,
            )
            # mkdir(exist_ok=False) 独占发布目录；即使并发出现同名目录也不会覆盖。
            # 独占建目录与所有权标记在同一无 await 的片段完成。
            source_dir.mkdir()
            published = True
            for stored_file in stored:
                await asyncio.to_thread(
                    (staged_dir / stored_file.filename).replace,
                    source_dir / stored_file.filename,
                )
            async with self._store.source_transaction(name) as session:
                row = await session.scalar(select(KnowledgeSourceRow).where(
                    KnowledgeSourceRow.id == owned_id
                ))
                assert row is not None
                row.files = [{"filename": f.filename, "size": f.size} for f in stored]
                row.chunk_count = chunk_count
                self._registry.register_knowledge(name, self._make_connector(name))
                registered = True
            finished = True
            result = await self.get_source(name)
            assert result is not None
            return result
        except BaseException:
            # 清理只针对真正取得预留行且尚未发布成功的这次创建；取消时也完成清理。
            async def cleanup() -> None:
                if not finished and owned_id is not None:
                    owned_pending = False
                    async with self._store.source_transaction(name) as session:
                        row = await session.scalar(select(KnowledgeSourceRow).where(
                            KnowledgeSourceRow.id == owned_id
                        ))
                        if row is not None and not row.files:
                            owned_pending = True
                            await session.execute(sa_delete(KnowledgeChunkRow).where(
                                KnowledgeChunkRow.source == name
                            ))
                            await session.delete(row)
                    # 提交已经成功时不可注销或删除已发布的源。
                    if owned_pending:
                        if registered:
                            self._registry.unregister_knowledge(name)
                        if published:
                            await asyncio.to_thread(shutil.rmtree, source_dir)
            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            raise
        finally:
            if staged_dir is not None:
                task = asyncio.create_task(asyncio.to_thread(shutil.rmtree, staged_dir))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise

    async def delete_source(self, name: str) -> None:
        """仅托管源可删：块 + 落盘目录 + 行 + registry 注册全清。"""
        await self._get_managed(name)
        await self._store.delete_source(name)
        source_dir = self._source_dir(name)
        await asyncio.to_thread(lambda: shutil.rmtree(source_dir, ignore_errors=True))
        async with self._db.session() as session:
            await session.execute(
                sa_delete(KnowledgeSourceRow).where(KnowledgeSourceRow.name == name)
            )
        self._registry.unregister_knowledge(name)

    async def rechunk(self, name: str, *, chunk_size: int, overlap: int) -> SourceInfo:
        """按新参数从落盘文件重新切块灌库并更新行；文件目录缺失则报错。"""
        await self._get_managed(name)
        self._validate_chunk_params(chunk_size, overlap)
        source_dir = self._source_dir(name)

        def _list_files() -> list[Path]:
            if not source_dir.is_dir():
                return []
            return [
                path
                for path in source_dir.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
            ]

        paths = sorted(await asyncio.to_thread(_list_files))
        if not paths:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"知识源落盘文件缺失，无法重新切块: {source_dir}",
                details={"files_root": str(source_dir), "source": name},
            )
        await ingest_files(
            paths,
            source=name,
            store=self._store,
            embedder=self._embedder,
            chunk_size=chunk_size,
            overlap=overlap,
            replace=True,
            chunk_params=(chunk_size, overlap),
        )
        result = await self.get_source(name)
        assert result is not None
        return result

    # ---- 内部 ----

    async def _get_managed(self, name: str) -> SourceInfo:
        info = await self.get_source(name)
        if info is None:
            raise LubanError(
                ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND,
                f"知识源不存在: {name}",
                details={"source": name},
            )
        if not info.managed:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "仅支持操作通过工作台创建的知识源",
                details={"source": name},
            )
        return info

    def _source_dir(self, name: str) -> Path:
        # 名字已过正则白名单（无 / .. 等），resolve 后仍校验在 files_root 内（双保险）
        source_dir = (self._files_root / name).resolve()
        if not source_dir.is_relative_to(self._files_root.resolve()):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"非法知识源名: {name}",
                details={"source": name},
            )
        return source_dir

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _SOURCE_NAME_RE.match(name):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "知识源名必须为 2-64 位小写字母/数字/-/_，且以字母或数字开头",
                details={"source": name},
            )

    @staticmethod
    def _validate_chunk_params(chunk_size: int, overlap: int) -> None:
        if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                "chunk_size 必须 > 0 且 0 <= overlap < chunk_size",
                details={"chunk_size": chunk_size, "overlap": overlap},
            )

    @staticmethod
    def _validate_files(files: list[tuple[str, bytes]]) -> list[StoredFile]:
        """后缀白名单 + 大小上限；文件名取 basename（防路径穿越）。"""
        stored: list[StoredFile] = []
        total = 0
        for filename, data in files:
            name = Path(filename).name
            if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"不支持的文档类型: {filename}",
                    details={"filename": filename, "supported": sorted(SUPPORTED_SUFFIXES)},
                )
            if len(data) > MAX_FILE_BYTES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"文件超过单文件上限 {MAX_FILE_BYTES} 字节: {name}",
                    details={"filename": name, "size": len(data), "max": MAX_FILE_BYTES},
                )
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"上传总量超过上限 {MAX_TOTAL_BYTES} 字节",
                    details={"total": total, "max": MAX_TOTAL_BYTES},
                )
            stored.append(StoredFile(filename=name, size=len(data)))
        return stored
