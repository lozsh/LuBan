"""文档灌入：parse（多格式）→ chunk（字符滑窗）→ embed → 入库。"""

import asyncio
import csv
import json
import math
import os
import stat
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.zip_quota import ZipQuota, ZipTracker, check_zip_quota, stream_zip_member
from lubanagent.knowledge.embedding import EmbeddingProvider
from lubanagent.knowledge.store import KnowledgeStore

_DEFAULT_CHUNK_SIZE = 500
_DEFAULT_OVERLAP = 50
_MAX_INPUT_BYTES = 10 * 1024 * 1024
_MAX_TEXT_CHARS = 2_000_000
_MAX_PDF_PAGES = 200
_MAX_CHUNKS = 5000
_PARSE_TIMEOUT = 30.0
_DOCX_QUOTA = ZipQuota(512, 10 * 1024 * 1024, 50 * 1024 * 1024)

# 支持的文档后缀（工作台知识库上传白名单；parse_document 逐一分发）
SUPPORTED_SUFFIXES = frozenset(
    {".md", ".txt", ".pdf", ".docx", ".csv", ".json", ".html", ".xml"}
)

_SKIP_TAGS = {"script", "style"}


class _HTMLTextExtractor(HTMLParser):
    """剥标签取正文文本；script/style 内容整体跳过。"""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def _parse_csv(raw: str) -> str:
    """每行单元格 `" | "` join（表头保留为首行）。"""
    reader = csv.reader(StringIO(raw))
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in reader if row)


def _parse_json(raw: str) -> str:
    """list → 逐项单行 dumps（行式切块友好）；dict/标量 → indent=2 dumps。"""
    obj = json.loads(raw)
    if isinstance(obj, list):
        return "\n".join(json.dumps(item, ensure_ascii=False) for item in obj)
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _parse_html(raw: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(raw)
    return extractor.text


def _limited_text(text: str, limit: int) -> str:
    if len(text) > limit:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "提取文本超出字符配额")
    return text


def _load_docx_document(path: Path):
    from docx import Document

    return Document(str(path))


def _parse_docx(path: Path, *, zip_quota: ZipQuota = _DOCX_QUOTA,
                max_text_chars: int = _MAX_TEXT_CHARS) -> str:
    try:
        # 必须在 Document 打开之前消费所有成员，包括图片和不参与正文的内容。
        with zipfile.ZipFile(path) as archive:
            check_zip_quota(archive, zip_quota)
            tracker = ZipTracker()
            for info in archive.infolist():
                for _ in stream_zip_member(archive, info, zip_quota, tracker):
                    pass
        document = _load_docx_document(path)
        parts: list[str] = []
        length = 0
        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                length += len(paragraph.text) + (2 if parts else 0)
                if length > max_text_chars:
                    raise LubanError(ErrorCode.VALIDATION_ERROR, "提取文本超出字符配额")
                parts.append(paragraph.text)
        return "\n\n".join(parts)
    except ImportError as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "解析 DOCX 需要 python-docx",
                         details={"hint": "pip install python-docx", "path": str(path)}) from exc
    except (
        zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError,
        RuntimeError, NotImplementedError, ValueError,
    ) as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "非法 DOCX ZIP 容器") from exc


def _parse_pdf(path: Path, *, max_pdf_pages: int = _MAX_PDF_PAGES,
               max_text_chars: int = _MAX_TEXT_CHARS) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "解析 PDF 需要 pypdf",
                         details={"hint": "pip install pypdf", "path": str(path)}) from exc
    reader = PdfReader(str(path))
    if len(reader.pages) > max_pdf_pages:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "PDF 页数超出配额")
    parts: list[str] = []
    length = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        length += len(text) + (2 if parts else 0)
        if length > max_text_chars:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "提取文本超出字符配额")
        parts.append(text)
    return "\n\n".join(parts)


def _copy_limited_snapshot(source_path: Path, snapshot: Path, limit: int) -> None:
    """以不可跟随链接、非阻塞的 fd 读取普通文件，复制实际字节到私有快照。"""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source_path, flags)
    except OSError as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "无法打开普通文档文件") from exc
    try:
        with os.fdopen(fd, "rb", buffering=0, closefd=True) as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise LubanError(ErrorCode.VALIDATION_ERROR, "文档输入必须是普通文件")
            with snapshot.open("xb") as target:
                count = 0
                while True:
                    chunk = source.read(min(65536, limit - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise LubanError(ErrorCode.VALIDATION_ERROR, "输入文件超过大小配额")
                    target.write(chunk)
    except OSError as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "复制文档快照失败") from exc


async def _parse_snapshot(path: Path, *, max_input_bytes: int, max_text_chars: int,
                          max_pdf_pages: int, zip_quota: ZipQuota, parse_timeout: float) -> str:
    with tempfile.TemporaryDirectory(prefix="luban-ingest-") as directory:
        # 输入复制与解析在同一个可终止子进程中，私有目录由父进程回收。
        snapshot = Path(directory) / ("input" + path.suffix.lower())
        return await _parse_in_child(
            snapshot, source_path=path, max_input_bytes=max_input_bytes,
            max_text_chars=max_text_chars, max_pdf_pages=max_pdf_pages,
            zip_quota=zip_quota, parse_timeout=parse_timeout,
        )


async def _spawn_tracked(*args: str, **kwargs) -> asyncio.subprocess.Process:
    """启动任务遭取消时，等到取得句柄再终止回收；不留下无人认领的子进程。"""
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        return await asyncio.shield(spawn)
    except BaseException:
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # 当前任务若再次被取消，仍须完成已启动的进程清理。
            process = await spawn
        except Exception:
            raise
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


def _read_limited(path: Path, max_input_bytes: int) -> str:
    with path.open("rb") as source:
        data = source.read(max_input_bytes + 1)
    if len(data) > max_input_bytes:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "输入文件超过大小配额")
    return data.decode("utf-8")


def _child_limits(timeout: float) -> None:
    # 仅 Linux 声明地址空间限制有效；macOS RLIMIT_AS 不作为内存安全保证。
    if sys.platform != "linux":
        return
    import resource

    cpu = max(1, math.ceil(timeout))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    memory = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def _worker_main() -> None:
    # 子进程通过参数传递路径及配额，stdout 仅输出有界 JSON，错误不附带第三方 traceback。
    source = Path(sys.argv[1])
    path = Path(sys.argv[2])
    max_input, max_text, max_pages, members, member_bytes, total_bytes = map(int, sys.argv[3:9])
    try:
        _copy_limited_snapshot(source, path, max_input)
        if path.suffix.lower() == ".docx":
            text = _parse_docx(path, zip_quota=ZipQuota(members, member_bytes, total_bytes),
                               max_text_chars=max_text)
        else:
            text = _parse_pdf(path, max_pdf_pages=max_pages, max_text_chars=max_text)
        result = {"text": text}
    except LubanError as exc:
        result = {"error": exc.message, "details": exc.details}
    except Exception:
        result = {"error": "文档解析失败"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


async def _parse_in_child(path: Path, *, source_path: Path, max_input_bytes: int,
                          max_text_chars: int, max_pdf_pages: int, zip_quota: ZipQuota,
                          parse_timeout: float) -> str:
    args = (str(source_path), str(path), str(max_input_bytes), str(max_text_chars),
            str(max_pdf_pages), str(zip_quota.max_members), str(zip_quota.max_member_bytes),
            str(zip_quota.max_total_bytes))
    deadline = asyncio.get_running_loop().time() + parse_timeout
    try:
        async with asyncio.timeout_at(deadline):
            process = await _spawn_tracked(
                sys.executable, "-m", "lubanagent.knowledge.ingest", *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                preexec_fn=(lambda: _child_limits(parse_timeout))
                if sys.platform == "linux" else None,
            )
    except TimeoutError as exc:
        raise LubanError(ErrorCode.RUN_TIMEOUT, "文档解析超时") from exc
    # JSON unicode 最多 12 bytes/char（ensure_ascii=False 的可表示文本更小）；
    # 坏子进程的 stdout/stderr 也始终只保留定量字节。
    output_limit = max_text_chars * 12 + 8192

    async def read_stdout() -> bytes:
        data = bytearray()
        while True:
            chunk = await process.stdout.read(min(65536, output_limit - len(data) + 1))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > output_limit:
                raise LubanError(ErrorCode.VALIDATION_ERROR, "解析输出超过配额")

    async def drain_stderr() -> None:
        while await process.stderr.read(65536):
            pass

    tasks = [asyncio.create_task(read_stdout()), asyncio.create_task(drain_stderr())]
    try:
        async with asyncio.timeout_at(deadline):
            output, _ = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode != 0:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "解析子进程异常退出")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "解析子进程输出无效") from exc
        if "error" in result:
            raise LubanError(ErrorCode.VALIDATION_ERROR, result["error"],
                             details=result.get("details", {}))
        return _limited_text(result["text"], max_text_chars)
    except TimeoutError as exc:
        raise LubanError(ErrorCode.RUN_TIMEOUT, "文档解析超时") from exc
    finally:
        if process.returncode is None:
            process.kill()
        for task in tasks:
            task.cancel()
        # 在取消路径中也等待 OS 子进程回收和两条管道结束，避免遗留僵尸。
        async def reap() -> None:
            await asyncio.gather(process.wait(), *tasks, return_exceptions=True)

        cleanup = asyncio.create_task(reap())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup)
            raise


async def parse_document(
    path: Path, *, max_input_bytes: int = _MAX_INPUT_BYTES,
    max_text_chars: int = _MAX_TEXT_CHARS, max_pdf_pages: int = _MAX_PDF_PAGES,
    zip_quota: ZipQuota = _DOCX_QUOTA, parse_timeout: float = _PARSE_TIMEOUT,
) -> str:
    """所有入口共用输入/文本配额；PDF/DOCX 由可终止子进程解析。"""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES and suffix != ".htm":
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"不支持的文档类型: {suffix or '(无后缀)'}",
            details={"path": str(path), "supported": sorted(SUPPORTED_SUFFIXES)},
        )
    if min(max_input_bytes, max_text_chars, max_pdf_pages) < 0 or parse_timeout <= 0:
        raise ValueError("解析配额与超时必须为非负数，超时必须大于零")
    if suffix in {".pdf", ".docx"}:
        return await _parse_snapshot(path, max_input_bytes=max_input_bytes,
                                     max_text_chars=max_text_chars, max_pdf_pages=max_pdf_pages,
                                     zip_quota=zip_quota, parse_timeout=parse_timeout)
    if await asyncio.to_thread(lambda: path.stat().st_size) > max_input_bytes:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "输入文件超过大小配额")
    raw = await asyncio.to_thread(_read_limited, path, max_input_bytes)
    if suffix in {".md", ".txt"}:
        return _limited_text(raw, max_text_chars)
    if suffix == ".csv":
        return _limited_text(await asyncio.to_thread(_parse_csv, raw), max_text_chars)
    if suffix == ".json":
        return _limited_text(await asyncio.to_thread(_parse_json, raw), max_text_chars)
    return _limited_text(await asyncio.to_thread(_parse_html, raw), max_text_chars)


if __name__ == "__main__":
    _worker_main()


def chunk_text(
    text: str, *, chunk_size: int = _DEFAULT_CHUNK_SIZE, overlap: int = _DEFAULT_OVERLAP,
    max_chunks: int | None = None,
) -> list[str]:
    """按字符滑窗切块，窗口内遇段落边界（空行）优先断；相邻块保留 overlap 字符重叠。"""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            "chunk_size 必须 > 0 且 0 <= overlap < chunk_size",
            details={"chunk_size": chunk_size, "overlap": overlap},
        )
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            boundary = text.rfind("\n\n", start + overlap + 1, end)
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            if max_chunks is not None and len(chunks) >= max_chunks:
                raise LubanError(ErrorCode.VALIDATION_ERROR, "灌库块数超过配额")
            chunks.append(chunk)
        if end >= length:
            break
        start = end - overlap
    return chunks


async def ingest_files(
    paths: list[Path],
    *,
    source: str,
    store: KnowledgeStore,
    embedder: EmbeddingProvider,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    overlap: int = _DEFAULT_OVERLAP,
    replace: bool = True,
    max_chunks: int = _MAX_CHUNKS,
    chunk_params: tuple[int, int] | None = None,
    reservation_id: int | None = None,
) -> int:
    """解析/嵌入成功后，原子校验同源所有权与累计配额并写入切块。"""
    if max_chunks < 0:
        raise ValueError("max_chunks 不可为负数")
    # 提前拒绝已满源，不消耗 embed；写事务里还会复查，防并发追加。
    existing = 0 if replace else await store.count_chunks(source)
    if existing > max_chunks:
        raise LubanError(ErrorCode.VALIDATION_ERROR, "灌库块数超过配额")
    chunks: list[str] = []
    files: list[str] = []  # 与 chunks 一一对应的来源文件名（进 metadata 供溯源）
    for path in paths:
        text = await parse_document(path)
        pieces = chunk_text(text, chunk_size=chunk_size, overlap=overlap,
                            max_chunks=max_chunks - existing - len(chunks))
        chunks.extend(pieces)
        files.extend([path.name] * len(pieces))
    embeddings = await embedder.embed(chunks) if chunks else []
    stamp = datetime.now(UTC).isoformat()
    records = [
        {
            "chunk_index": index,
            "content": chunk,
            "embedding": embedding,
            # ingested_at 供 max_age_days 超龄过滤（防错答守门）；filename 供溯源
            "metadata": {"filename": doc, "ingested_at": stamp},
        }
        for index, (chunk, embedding, doc) in enumerate(
            zip(chunks, embeddings, files, strict=True)
        )
    ]
    return await store.write_chunks(source, records, replace=replace,
                                    max_chunks=max_chunks, chunk_params=chunk_params,
                                    reservation_id=reservation_id)


async def ingest_file(
    path: Path,
    *,
    source: str,
    store: KnowledgeStore,
    embedder: EmbeddingProvider,
    replace: bool = True,
) -> int:
    """单文件灌库（ingest_files 薄包装，签名不变——种子源等既有调用方零改动）。"""
    return await ingest_files(
        [path], source=source, store=store, embedder=embedder, replace=replace
    )
