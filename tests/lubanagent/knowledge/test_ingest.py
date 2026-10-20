"""ingest：parse 三格式与未知后缀报错、chunk 滑窗/段落边界、ingest_file 端到端与 replace 幂等。"""

import asyncio
import importlib.util
import io
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from sqlalchemy import event

from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.zip_quota import ZipQuota
from lubanagent.knowledge import (
    FakeEmbedding,
    KnowledgeStore,
    chunk_text,
    ingest_file,
    ingest_files,
    parse_document,
)
from lubanagent.knowledge import ingest as ingest_module
from lubanagent.persistence.models.knowledge import KnowledgeChunkRow

FAQ_TEXT = (
    "# 产品 FAQ\n\n"
    "退货政策：支持七天无理由退货，退货运费由商家承担。\n\n"
    "发货时间：工作日 16 点前下单当日发出，其余次日发出。\n\n"
    "保修说明：整机保修一年，核心部件保修三年。\n"
)


async def test_parse_markdown(tmp_path: Path) -> None:
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    assert await parse_document(path) == FAQ_TEXT


async def test_parse_txt(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("纯文本内容", encoding="utf-8")
    assert await parse_document(path) == "纯文本内容"


async def test_parse_unknown_suffix_raises_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "data.exe"
    path.write_text("binary", encoding="utf-8")
    with pytest.raises(LubanError) as exc_info:
        await parse_document(path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.skipif(
    importlib.util.find_spec("pypdf") is not None, reason="pypdf 已安装时无缺失报错路径"
)
async def test_parse_pdf_without_pypdf_raises_with_hint(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(LubanError) as exc_info:
        await parse_document(path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["hint"] == "pip install pypdf"


def test_chunk_text_window_lengths_and_overlap() -> None:
    text = "a" * 1200
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert [len(chunk) for chunk in chunks] == [500, 500, 300]
    assert chunks[0][-50:] == chunks[1][:50]
    assert chunks[1][-50:] == chunks[2][:50]


def test_chunk_text_prefers_paragraph_boundary() -> None:
    text = "x" * 100 + "\n\n" + "y" * 600
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert chunks[0] == "x" * 100
    assert "".join(chunks).count("y") > 0


def test_chunk_text_empty_and_invalid_params() -> None:
    assert chunk_text("") == []
    with pytest.raises(LubanError) as exc_info:
        chunk_text("abc", chunk_size=50, overlap=50)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_ingest_file_end_to_end(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    count = await ingest_file(path, source="faq", store=store, embedder=embedder)
    assert count == len(chunk_text(FAQ_TEXT))

    chunks = await store.list_chunks("faq")
    assert len(chunks) == count
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(count))
    assert all(chunk["metadata"]["filename"] == "faq.md" for chunk in chunks)
    assert all(len(chunk["embedding"]) == 2048 for chunk in chunks)


async def test_ingest_replace_is_idempotent(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    first = await ingest_file(path, source="faq", store=store, embedder=embedder)
    second = await ingest_file(path, source="faq", store=store, embedder=embedder)
    assert first == second
    assert len(await store.list_chunks("faq")) == first


async def test_ingest_without_replace_appends(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    path = tmp_path / "faq.md"
    path.write_text(FAQ_TEXT, encoding="utf-8")
    first = await ingest_file(path, source="faq", store=store, embedder=embedder)
    await ingest_file(path, source="faq", store=store, embedder=embedder, replace=False)
    assert len(await store.list_chunks("faq")) == first * 2


# ---- 新格式解析（csv/json/html/xml/docx）与多文件灌库 ----


async def test_parse_csv_row_join(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("产品,价格\n手机,2999\n笔记本,5999\n", encoding="utf-8")
    assert await parse_document(path) == "产品 | 价格\n手机 | 2999\n笔记本 | 5999"


async def test_parse_json_list_line_per_item(tmp_path: Path) -> None:
    path = tmp_path / "items.json"
    payload = '[{"q": "退货", "a": "七天无理由"}, {"q": "发票", "a": "可开"}]'
    path.write_text(payload, encoding="utf-8")
    text = await parse_document(path)
    assert text.splitlines() == ['{"q": "退货", "a": "七天无理由"}', '{"q": "发票", "a": "可开"}']


async def test_parse_json_object_indented(tmp_path: Path) -> None:
    path = tmp_path / "conf.json"
    path.write_text('{"name": "说明", "version": 2}', encoding="utf-8")
    text = await parse_document(path)
    assert '"name": "说明"' in text and '"version": 2' in text and "\n" in text


async def test_parse_html_strips_tags_and_script(tmp_path: Path) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        "<html><head><style>.a{}</style><script>bad()</script></head>"
        "<body><h1>标题</h1><p>第一段</p><p>第二段</p></body></html>",
        encoding="utf-8",
    )
    assert await parse_document(path) == "标题\n第一段\n第二段"


async def test_parse_xml_text_nodes(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml"
    path.write_text("<items><item>甲</item><item>乙</item></items>", encoding="utf-8")
    assert await parse_document(path) == "甲\n乙"


@pytest.mark.skipif(
    importlib.util.find_spec("docx") is None, reason="python-docx 未安装时跳过"
)
async def test_parse_docx_paragraphs(tmp_path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_paragraph("第一段")
    document.add_paragraph("")
    document.add_paragraph("第二段")
    path = tmp_path / "doc.docx"
    document.save(str(path))
    assert await parse_document(path) == "第一段\n\n第二段"


@pytest.mark.skipif(
    importlib.util.find_spec("docx") is None, reason="python-docx 未安装时跳过"
)
def test_docx_direct_parser_enforces_text_limit(tmp_path: Path) -> None:
    from docx import Document

    document = Document()
    document.add_paragraph("第一段")
    document.add_paragraph("第二段")
    path = tmp_path / "document.docx"
    document.save(path)
    assert ingest_module._parse_docx(path) == "第一段\n\n第二段"
    with pytest.raises(LubanError, match="提取文本超出字符配额"):
        ingest_module._parse_docx(path, max_text_chars=4)


async def test_parse_unknown_suffix_lists_supported_set(tmp_path: Path) -> None:
    path = tmp_path / "data.exe"
    path.write_text("binary", encoding="utf-8")
    with pytest.raises(LubanError) as exc_info:
        await parse_document(path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["supported"] == sorted(
        {".md", ".txt", ".pdf", ".docx", ".csv", ".json", ".html", ".xml"}
    )


async def test_ingest_files_multi_file_contiguous_index_and_metadata(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    a = tmp_path / "a.md"
    a.write_text("甲" * 300 + "\n\n" + "乙" * 300, encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("丙" * 400, encoding="utf-8")
    from lubanagent.knowledge import ingest_files

    count = await ingest_files(
        [a, b], source="multi", store=store, embedder=embedder, chunk_size=500, overlap=50
    )
    chunks = await store.list_chunks("multi")
    assert len(chunks) == count > 2
    # chunk_index 全源连续
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(count))
    # 每块 metadata.filename 对应来源文件，且两文件都出现
    filenames = {chunk["metadata"]["filename"] for chunk in chunks}
    assert filenames == {"a.md", "b.md"}
    # a 的块排在 b 前面
    assert chunks[0]["metadata"]["filename"] == "a.md"
    assert chunks[-1]["metadata"]["filename"] == "b.md"


async def test_ingest_files_replace_deletes_old_chunks(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    from lubanagent.knowledge import ingest_files

    a = tmp_path / "a.md"
    a.write_text("内容甲" * 100, encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("内容乙" * 10, encoding="utf-8")
    await ingest_files([a], source="src", store=store, embedder=embedder)
    await ingest_files([b], source="src", store=store, embedder=embedder, replace=True)
    chunks = await store.list_chunks("src")
    assert all(chunk["metadata"]["filename"] == "b.md" for chunk in chunks)


async def test_input_and_extracted_text_small_quotas(tmp_path: Path) -> None:
    large = tmp_path / "too-big.txt"
    large.write_text("a" * 20, encoding="utf-8")
    with pytest.raises(LubanError, match="输入"):
        await parse_document(large, max_input_bytes=10)
    with pytest.raises(LubanError, match="文本"):
        await parse_document(large, max_text_chars=10)
    json_file = tmp_path / "data.json"
    json_file.write_text('"\\u4e00"', encoding="utf-8")
    with pytest.raises(LubanError, match="文本"):
        await parse_document(json_file, max_text_chars=2)


async def test_docx_zip_quotas_and_invalid_zip(tmp_path: Path) -> None:
    path = tmp_path / "doc.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb", "a" * 1000)
    for quota in (ZipQuota(0, 2000, 2000), ZipQuota(1, 10, 2000), ZipQuota(1, 2000, 10)):
        with pytest.raises(LubanError) as exc:
            await parse_document(path, zip_quota=quota)
        assert exc.value.code == ErrorCode.VALIDATION_ERROR
    path.write_bytes(b"broken zip")
    with pytest.raises(LubanError) as exc:
        await parse_document(path)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


async def test_docx_verifies_actual_stream_before_document(tmp_path: Path, monkeypatch) -> None:
    from docx import Document

    path = tmp_path / "doc.docx"
    doc = Document()
    doc.add_paragraph("normal")
    doc.save(path)
    original = zipfile.ZipFile
    invoked = []

    class FakeZip(original):
        def open(self, name, mode="r", pwd=None, *, force_zip64=False):
            reader = super().open(name, mode, pwd=pwd, force_zip64=force_zip64)
            if getattr(name, "filename", name) == "[Content_Types].xml":
                return io.BytesIO(reader.read() + b"extra")
            return reader

    monkeypatch.setattr(zipfile, "ZipFile", FakeZip)
    monkeypatch.setattr(ingest_module, "_load_docx_document", lambda path: invoked.append(path))
    with pytest.raises(LubanError, match="实际"):
        ingest_module._parse_docx(path)
    assert not invoked


def test_pdf_rejects_extracted_text_incrementally(tmp_path: Path, monkeypatch) -> None:
    import pypdf

    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF fake")

    class FakePage:
        def extract_text(self):
            return "abcd"

    class FakeReader:
        def __init__(self, name):
            self.pages = [FakePage(), FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    with pytest.raises(LubanError, match="文本"):
        ingest_module._parse_pdf(path, max_text_chars=5)
    assert ingest_module._parse_pdf(path, max_text_chars=10) == "abcd\n\nabcd"


async def test_pdf_page_and_text_quotas(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    with pytest.raises(LubanError, match="页"):
        await parse_document(path, max_pdf_pages=1)
    assert await parse_document(path, max_pdf_pages=2) == "\n\n"


async def test_chunk_limit_preserves_old_source_and_skips_embedding(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding, monkeypatch
) -> None:
    old = tmp_path / "old.md"
    old.write_text("old", encoding="utf-8")
    await ingest_file(old, source="original", store=store, embedder=embedder)
    new = tmp_path / "new.md"
    new.write_text("abcdefghij", encoding="utf-8")
    embed = AsyncMock(side_effect=AssertionError("embed should not run"))
    monkeypatch.setattr(embedder, "embed", embed)
    with pytest.raises(LubanError, match="块"):
        await ingest_files(
            [new], source="original", store=store, embedder=embedder,
            chunk_size=2, overlap=0, max_chunks=2,
        )
    embed.assert_not_awaited()
    assert [item["content"] for item in await store.list_chunks("original")] == ["old"]


async def test_chunk_limit_is_cumulative_across_files(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding, monkeypatch
) -> None:
    old = tmp_path / "old.md"
    old.write_text("old", encoding="utf-8")
    await ingest_file(old, source="existing", store=store, embedder=embedder)
    files = [tmp_path / "first.txt", tmp_path / "second.txt"]
    for path in files:
        path.write_text("abcd", encoding="utf-8")
    embed = AsyncMock(side_effect=AssertionError("embed should not run"))
    monkeypatch.setattr(embedder, "embed", embed)
    with pytest.raises(LubanError, match="块"):
        await ingest_files(
            files, source="existing", store=store, embedder=embedder,
            chunk_size=2, overlap=0, max_chunks=3,
        )
    embed.assert_not_awaited()
    assert [item["content"] for item in await store.list_chunks("existing")] == ["old"]


async def test_append_counts_existing_chunks_before_embedding(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding, monkeypatch
) -> None:
    path = tmp_path / "one.txt"
    path.write_text("new", encoding="utf-8")
    await ingest_file(path, source="quota", store=store, embedder=embedder)
    embed = AsyncMock(side_effect=AssertionError("embed should not run"))
    monkeypatch.setattr(embedder, "embed", embed)
    with pytest.raises(LubanError, match="块"):
        await ingest_files([path], source="quota", store=store, embedder=embedder,
                           replace=False, max_chunks=1)
    embed.assert_not_awaited()
    assert [row["content"] for row in await store.list_chunks("quota")] == ["new"]


async def test_append_rechecks_quota_at_write_after_concurrent_ingest(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding, monkeypatch
) -> None:
    path = tmp_path / "two.txt"
    path.write_text("new", encoding="utf-8")
    original_embed = embedder.embed
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def paused_embed(texts):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return await original_embed(texts)

    monkeypatch.setattr(embedder, "embed", paused_embed)
    first = asyncio.create_task(ingest_files([path], source="concurrent", store=store,
                                            embedder=embedder, replace=False, max_chunks=1))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        assert await ingest_files([path], source="concurrent", store=store,
                                  embedder=embedder, replace=False, max_chunks=1) == 1
    finally:
        release.set()
    with pytest.raises(LubanError, match="块"):
        await first
    assert await store.count_chunks("concurrent") == 1


async def test_replace_write_failure_rolls_back_old_chunks(
    tmp_path: Path, store: KnowledgeStore, embedder: FakeEmbedding
) -> None:
    path = tmp_path / "new.txt"
    path.write_text("new", encoding="utf-8")
    await store.add_chunks("rollback", [{"chunk_index": 0, "content": "old",
                                        "embedding": [1.0], "metadata": {}}])
    before = await store.list_chunks("rollback")

    def fail_insert(mapper, connection, target):
        if target.source == "rollback" and target.content == "new":
            raise RuntimeError("synthetic insert failure")

    event.listen(KnowledgeChunkRow, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic insert failure"):
            await ingest_files([path], source="rollback", store=store, embedder=embedder)
    finally:
        event.remove(KnowledgeChunkRow, "before_insert", fail_insert)
    assert await store.list_chunks("rollback") == before


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
async def test_binary_snapshot_rejects_growth_during_copy(
    tmp_path: Path, monkeypatch, suffix: str,
) -> None:
    path = tmp_path / ("source" + suffix)
    path.write_bytes(b"a" * 8)
    original_open = ingest_module.os.open
    snapshot = tmp_path / ("snapshot" + suffix)

    def grow_after_open(name, flags, *args, **kwargs):
        fd = original_open(name, flags, *args, **kwargs)
        if name == path:
            with path.open("ab") as file:
                file.write(b"b" * 4)
        return fd

    monkeypatch.setattr(ingest_module.os, "open", grow_after_open)
    with pytest.raises(LubanError, match="输入文件超过大小配额"):
        ingest_module._copy_limited_snapshot(path, snapshot, 8)
    assert path.read_bytes() == b"a" * 8 + b"b" * 4
    assert snapshot.stat().st_size <= 8


async def test_binary_snapshot_reads_at_most_quota_plus_one_and_cleans_on_reject(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"a" * (65536 + 2))
    original_fdopen = ingest_module.os.fdopen
    requests = []
    snapshots = []
    original_copy = ingest_module._copy_limited_snapshot

    class TrackedSource:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            requests.append(size)
            return self.source.read(size)

    def tracked_fdopen(*args, **kwargs):
        return TrackedSource(original_fdopen(*args, **kwargs))

    def tracked_copy(source, snapshot, limit):
        snapshots.append(snapshot)
        original_copy(source, snapshot, limit)

    monkeypatch.setattr(ingest_module.os, "fdopen", tracked_fdopen)
    monkeypatch.setattr(ingest_module, "_copy_limited_snapshot", tracked_copy)
    with pytest.raises(LubanError, match="输入文件超过大小配额"):
        tracked_copy(path, tmp_path / "snapshot.pdf", 65536)
    assert requests == [65536, 1]
    assert snapshots and snapshots[0].stat().st_size == 65536


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
async def test_binary_parser_uses_snapshot_after_source_replacement(
    tmp_path: Path, monkeypatch, suffix: str,
) -> None:
    path = tmp_path / ("source" + suffix)
    path.write_bytes(b"original")
    snapshot = tmp_path / ("snapshot" + suffix)
    ingest_module._copy_limited_snapshot(path, snapshot, 8)
    replacement = tmp_path / ("replacement" + suffix)
    replacement.write_bytes(b"x" * 100)
    replacement.replace(path)
    assert snapshot.read_bytes() == b"original"
    assert path.stat().st_size == 100


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
async def test_binary_snapshot_rejects_nonregular_source(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "source.pdf"
    if kind == "symlink":
        target = tmp_path / "target.pdf"
        target.write_bytes(b"safe")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(LubanError) as error:
        await asyncio.wait_for(parse_document(path), 2)
    assert error.value.code == ErrorCode.VALIDATION_ERROR


async def test_cancel_during_snapshot_copy_removes_private_file(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"safe")
    real_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children = []
    snapshots = []

    async def paused_copy_spawn(*args, **kwargs):
        snapshots.append(Path(args[4]))
        child = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=kwargs["stdout"], stderr=kwargs["stderr"],
        )
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", paused_copy_spawn)
    task = asyncio.create_task(parse_document(path))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert children[0].returncode is not None
    assert snapshots and not snapshots[0].parent.exists()


def test_child_worker_dispatches_pdf_docx_and_normalizes_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = tmp_path / "doc.pdf"
    docx = tmp_path / "doc.docx"
    pdf.write_bytes(b"short")
    docx.write_bytes(b"short")
    calls = []

    def parse_pdf(path: Path, *, max_pdf_pages: int, max_text_chars: int) -> str:
        calls.append(("pdf", path, max_pdf_pages, max_text_chars))
        return "parsed"

    def parse_docx(path: Path, *, zip_quota: ZipQuota, max_text_chars: int) -> str:
        calls.append(("docx", path, zip_quota.max_total_bytes, max_text_chars))
        raise LubanError(ErrorCode.VALIDATION_ERROR, "rejected")

    class CapturedStdout:
        def __init__(self) -> None:
            self.value = ""

        def write(self, value: str) -> None:
            self.value += value

    output = CapturedStdout()
    monkeypatch.setattr(ingest_module, "_parse_pdf", parse_pdf)
    monkeypatch.setattr(ingest_module, "_parse_docx", parse_docx)
    monkeypatch.setattr(ingest_module.sys, "stdout", output)
    args = ["worker", str(pdf), str(tmp_path / "input.pdf"), "5", "10", "2", "3", "4", "6"]
    monkeypatch.setattr(ingest_module.sys, "argv", args)
    ingest_module._worker_main()
    assert output.value == '{"text": "parsed"}'
    assert calls[-1] == ("pdf", Path(args[2]), 2, 10)
    assert Path(args[2]).read_bytes() == b"short"

    output.value = ""
    args[1] = str(docx)
    args[2] = str(tmp_path / "input.docx")
    ingest_module._worker_main()
    assert "rejected" in output.value
    assert calls[-1] == ("docx", Path(args[2]), 6, 10)

    output.value = ""
    args[1] = str(pdf)
    args[2] = str(tmp_path / "too-small.pdf")
    args[3] = "4"
    ingest_module._worker_main()
    assert "输入文件超过大小配额" in output.value

    output.value = ""
    args[2] = str(tmp_path / "parse-error.pdf")
    args[3] = "5"
    monkeypatch.setattr(ingest_module, "_parse_pdf", lambda *a, **kw: 1 / 0)
    ingest_module._worker_main()
    assert "文档解析失败" in output.value


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_parser_spawn_pending_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"safe")
    real_spawn = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    release = asyncio.Event()
    children = []
    snapshots = []

    async def delayed_spawn(*args, **kwargs):
        snapshots.append(Path(args[4]))
        entered.set()
        await release.wait()
        child = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=kwargs["stdout"], stderr=kwargs["stderr"],
        )
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(parse_document(path, parse_timeout=0.05 if mode == "timeout" else 5))
    await asyncio.wait_for(entered.wait(), 2)
    if mode == "cancel":
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    else:
        await asyncio.sleep(0.1)
        release.set()
        with pytest.raises(LubanError, match="超时"):
            await asyncio.wait_for(task, 3)
    assert len(children) == 1 and children[0].returncode is not None
    assert snapshots and not snapshots[0].parent.exists()


async def test_parser_timeout_and_cancel_reap_process(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"x")
    real_spawn = asyncio.create_subprocess_exec
    children = []
    snapshots = []
    started = asyncio.Event()

    async def slow_spawn(*args, **kwargs):
        snapshots.append(Path(args[4]))
        assert snapshots[-1].parent.exists() and not snapshots[-1].exists()
        process = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(30)",
            stdout=kwargs["stdout"], stderr=kwargs["stderr"],
        )
        children.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_spawn)
    with pytest.raises(LubanError, match="超时"):
        await parse_document(path, parse_timeout=0.05)
    assert children[-1].returncode is not None
    assert not snapshots[-1].parent.exists()
    started.clear()
    task = asyncio.create_task(parse_document(path, parse_timeout=30))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[-1].returncode is not None
    assert not snapshots[-1].parent.exists()
