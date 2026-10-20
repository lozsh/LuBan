"""ZIP 容器目录预检与实际解压配额；调用方可将流写盘或在内存中消费。"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from .errors import ErrorCode, LubanError

_READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ZipQuota:
    max_members: int
    max_member_bytes: int
    max_total_bytes: int

    def __post_init__(self) -> None:
        if min(self.max_members, self.max_member_bytes, self.max_total_bytes) < 0:
            raise ValueError("ZIP 配额不可为负数")


@dataclass
class ZipTracker:
    """一次 ZIP 解压共享的实际展开字节计数器。"""

    total_bytes: int = 0


def _reject(reason: str) -> None:
    raise LubanError(ErrorCode.VALIDATION_ERROR, f"ZIP 配额超限或损坏: {reason}")


def check_zip_quota(zf: zipfile.ZipFile, quota: ZipQuota) -> None:
    """解压前按中央目录拒绝成员数、单成员与总展开量超限。"""
    members = zf.infolist()
    if len(members) > quota.max_members:
        _reject("成员数")
    total = 0
    for info in members:
        if info.file_size < 0 or info.file_size > quota.max_member_bytes:
            _reject(f"单成员大小: {info.filename}")
        total += info.file_size
        if total > quota.max_total_bytes:
            _reject("总展开大小")


def stream_zip_member(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, quota: ZipQuota, tracker: ZipTracker
) -> Iterator[bytes]:
    """限量读取单个成员；任何异常均由调用方清理暂存文件。

    消费完整个迭代器才能完成 CRC/长度校验。DOCX 可 ``b"".join(...)``
    后交给解析器，无需落盘；同一容器的所有成员必须共用 tracker。
    """
    if info.file_size < 0 or info.file_size > quota.max_member_bytes:
        _reject(f"单成员大小: {info.filename}")
    if tracker.total_bytes < 0 or tracker.total_bytes > quota.max_total_bytes:
        _reject("总展开大小")
    member_bytes = 0
    try:
        with zf.open(info) as reader:
            while True:
                # 即使 open() 的 reader 伪造头部长度，也只请求剩余额度 + 1 字节。
                size = min(
                    _READ_CHUNK,
                    info.file_size - member_bytes + 1,
                    quota.max_member_bytes - member_bytes + 1,
                    quota.max_total_bytes - tracker.total_bytes + 1,
                )
                chunk = reader.read(size)
                if not chunk:
                    break
                member_bytes += len(chunk)
                if (
                    member_bytes > info.file_size
                    or member_bytes > quota.max_member_bytes
                    or tracker.total_bytes + len(chunk) > quota.max_total_bytes
                ):
                    _reject(f"实际展开大小: {info.filename}")
                tracker.total_bytes += len(chunk)
                yield chunk
        if member_bytes != info.file_size:
            _reject(f"实际长度与目录不符: {info.filename}")
    except (zipfile.BadZipFile, EOFError) as exc:
        raise LubanError(ErrorCode.VALIDATION_ERROR, f"非法 zip 包: {exc}") from None
