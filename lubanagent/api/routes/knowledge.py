"""knowledge 路由：工作台知识库管理（列表/上传/删除/重切/切块查看/检索测试）。

KnowledgeSourceManager 由工厂（lubanagent/app/factory.py）挂
app.state.knowledge_manager；路由经 request.app.state 取用，
不 import knowledge 模块（api → knowledge 为分层禁忌，同 skills 先例）。
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from lubanagent.api.errors import conflict_response
from lubanagent.api.schemas import (
    KnowledgeChunkOut,
    KnowledgeHitOut,
    KnowledgeQueryRequest,
    KnowledgeRechunkRequest,
    KnowledgeSourceOut,
)
from lubanagent.core import ErrorCode, LubanError

router = APIRouter(tags=["knowledge"])

_SOURCE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
# 与 knowledge.manager 的 MAX_FILE_BYTES/MAX_TOTAL_BYTES 保持一致；不跨层 import。
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_MAX_FILES = 50
_UPLOAD_CHUNK_BYTES = 64 * 1024
# 上传文件说明（md/txt/pdf/docx/csv/json/html/xml，白名单由知识层 SUPPORTED_SUFFIXES 校验）
_FILES_FIELD = Annotated[
    list[UploadFile], File(description="知识文档，支持 md/txt/pdf/docx/csv/json/html/xml")
]


def _manager(request: Request):  # type: ignore[no-untyped-def]
    """读 app.state.knowledge_manager（工厂装配）；未装配属装配错误。"""
    manager = getattr(request.app.state, "knowledge_manager", None)
    if manager is None:
        raise LubanError(ErrorCode.CONFIG_ERROR, "KnowledgeSourceManager 未装配")
    return manager


def _to_out(info) -> KnowledgeSourceOut:  # type: ignore[no-untyped-def]
    return KnowledgeSourceOut(
        name=info.name,
        description=info.description,
        chunk_size=info.chunk_size,
        overlap=info.overlap,
        files=[{"filename": f.filename, "size": f.size} for f in info.files],
        chunk_count=info.chunk_count,
        managed=info.managed,
        created_at=info.created_at,
        updated_at=info.updated_at,
    )


@router.get("/knowledge/sources", response_model=list[KnowledgeSourceOut])
async def list_knowledge_sources(request: Request) -> list[KnowledgeSourceOut]:
    return [_to_out(info) for info in await _manager(request).list_sources()]


@router.post("/knowledge/sources", response_model=KnowledgeSourceOut, status_code=201)
async def create_knowledge_source(
    request: Request,
    files: _FILES_FIELD,
    name: Annotated[str, Form(pattern=_SOURCE_NAME_PATTERN)],
    description: Annotated[str, Form()] = "",
    chunk_size: Annotated[int, Form(gt=0)] = 500,
    overlap: Annotated[int, Form(ge=0)] = 50,
) -> KnowledgeSourceOut:
    if len(files) > _MAX_FILES:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"上传文件数超过上限 {_MAX_FILES}",
            details={"count": len(files), "max": _MAX_FILES},
        )
    payload: list[tuple[str, bytes]] = []
    total = 0
    for file in files:
        filename = file.filename or "untitled"
        chunks: list[bytes] = []
        size = 0
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"文件超过单文件上限 {_MAX_FILE_BYTES} 字节: {filename}",
                    details={"filename": filename, "size": size, "max": _MAX_FILE_BYTES},
                )
            total += len(chunk)
            if total > _MAX_TOTAL_BYTES:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"上传总量超过上限 {_MAX_TOTAL_BYTES} 字节",
                    details={"total": total, "max": _MAX_TOTAL_BYTES},
                )
            chunks.append(chunk)
        payload.append((filename, b"".join(chunks)))
    manager = _manager(request)
    if await manager.get_source(name) is not None:
        return conflict_response(f"知识源已存在: {name}", {"source": name})
    info = await manager.create_source(
        name=name,
        description=description,
        chunk_size=chunk_size,
        overlap=overlap,
        files=payload,
    )
    return _to_out(info)


@router.delete("/knowledge/sources/{name}", status_code=204)
async def delete_knowledge_source(name: str, request: Request) -> None:
    await _manager(request).delete_source(name)


@router.post("/knowledge/sources/{name}/rechunk", response_model=KnowledgeSourceOut)
async def rechunk_knowledge_source(
    name: str, body: KnowledgeRechunkRequest, request: Request
) -> KnowledgeSourceOut:
    info = await _manager(request).rechunk(name, chunk_size=body.chunk_size, overlap=body.overlap)
    return _to_out(info)


@router.get("/knowledge/sources/{name}/chunks", response_model=list[KnowledgeChunkOut])
async def list_knowledge_chunks(
    name: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[KnowledgeChunkOut]:
    manager = _manager(request)
    if await manager.get_source(name) is None:
        raise LubanError(
            ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND,
            f"知识源不存在: {name}",
            details={"source": name},
        )
    chunks = await manager.list_chunks(name, limit=limit)
    return [
        KnowledgeChunkOut(
            chunk_index=chunk["chunk_index"],
            content=chunk["content"],
            metadata=chunk.get("metadata") or {},
            created_at=chunk.get("created_at"),
        )
        for chunk in chunks
    ]


@router.post("/knowledge/query", response_model=list[KnowledgeHitOut])
async def query_knowledge(
    body: KnowledgeQueryRequest, request: Request
) -> list[KnowledgeHitOut]:
    hits = await _manager(request).search(body.source, body.query, top_k=body.top_k)
    return [
        KnowledgeHitOut(
            source=hit["source"],
            chunk_index=hit["chunk_index"],
            content=hit["content"],
            score=hit["score"],
            metadata=hit.get("metadata") or {},
        )
        for hit in hits
    ]
