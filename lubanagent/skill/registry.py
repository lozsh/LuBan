"""Skill 注册表: 扫描入库版本化、L1 元数据注入格式化、load_skill 工具构造。"""

from __future__ import annotations

import io
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lubanagent.core import ErrorCode, LubanError, RiskLevel, ToolDescriptor
from lubanagent.core.zip_quota import ZipQuota, ZipTracker, check_zip_quota, stream_zip_member
from lubanagent.extension import RegisteredTool

from .loader import SkillMeta, SkillPackage, load_body, scan_skills_dir

if TYPE_CHECKING:  # 避免 persistence -> skill -> persistence 循环 import
    from lubanagent.persistence.repositories.skill import SkillRepository

# 单个目录项，不允许特殊目录项、路径分隔符或绝对路径。
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z")
SKILL_ZIP_QUOTA = ZipQuota(
    max_members=512, max_member_bytes=10 * 1024 * 1024, max_total_bytes=50 * 1024 * 1024
)


class SkillRegistry:
    """skills_root 下 Skill 包的运行期视图; scan 后有 repository 则入库版本化。"""

    def __init__(self, *, skills_root: Path, repository: SkillRepository | None = None) -> None:
        self._root = Path(skills_root)
        self._repository = repository
        self._packages: dict[str, SkillPackage] = {}

    async def scan(self) -> list[SkillPackage]:
        """扫描目录刷新内存视图; 有 repository 时逐包 upsert (内容 hash 变化才更新版本)。"""
        packages = scan_skills_dir(self._root)
        if self._repository is not None:
            # upsert 自身仅 flush；以 savepoint 防止半途失败污染调用方会话。
            async with self._repository._session.begin_nested():
                for package in packages:
                    await self._repository.upsert(package)
        self._packages = {p.meta.name: p for p in packages}
        return packages

    def list_packages(self) -> list[SkillPackage]:
        """内存视图：所有已扫描 Skill 包（list_skills 路由读此，不依赖 DB）。"""
        return list(self._packages.values())

    async def install(self, zip_bytes: bytes) -> SkillPackage:
        """在同一文件系统暂存、验证并替换；扫描失败恢复旧目录和内存。"""
        root = self._root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        # 暂存与备份在 root 旁，避免 scan 将其作为额外 Skill 包读取。
        with tempfile.TemporaryDirectory(prefix="luban-skill-", dir=root.parent) as work:
            staging = Path(work) / "staging"
            staging.mkdir()
            self._safe_extract(zip_bytes, staging)
            packages = scan_skills_dir(staging)
            if len(packages) != 1:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"zip 必须恰好含一个 Skill 包（含 SKILL.md），实际: {len(packages)}",
                )
            package = packages[0]
            name = package.meta.name
            dest = self._destination(name)
            source = Path(package.path)
            backup = Path(work) / "backup"
            old_packages = self._packages
            had_old = dest.is_dir()
            try:
                if had_old:
                    os.replace(dest, backup)
                os.replace(source, dest)
                await self.scan()
                installed = self._packages.get(name)
                if installed is None:
                    raise LubanError(
                        ErrorCode.VALIDATION_ERROR,
                        f"Skill 安装后扫描未命中: {name}",
                        details={"name": name},
                    )
                return installed
            except BaseException:
                if dest.is_dir() and not dest.is_symlink():
                    shutil.rmtree(dest)
                if backup.exists():
                    os.replace(backup, dest)
                self._packages = old_packages
                raise

    def _destination(self, name: str) -> Path:
        root = self._root.resolve()
        if (
            not isinstance(name, str)
            or name in ("", ".", "..")
            or not _NAME_PATTERN.fullmatch(name)
        ):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"Skill name 非法: {name}",
                details={"name": name},
            )
        dest = root / name
        if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"Skill 目标不是普通目录: {name}",
                details={"name": name},
            )
        resolved = dest.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"Skill name 路径逃逸被拒绝: {name}",
                details={"name": name},
            )
        return dest

    def delete(self, name: str) -> None:
        """删除 skills_root/{name}/ 目录（不存在则 noop；内存视图下次 scan 刷新）。"""
        dest = self._destination(name)
        if dest.is_dir():
            shutil.rmtree(dest)

    @staticmethod
    def _safe_extract(zip_bytes: bytes, target: Path) -> None:
        """预检配额与路径/类型，再逐成员流式校验实际展开量并写盘。"""
        target_resolved = target.resolve()
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"非法 zip 包: {exc}") from None
        with zf:
            check_zip_quota(zf, SKILL_ZIP_QUOTA)
            members = zf.infolist()
            for info in members:
                member = info.filename
                parts = member.rstrip("/").split("/")
                mode = (info.external_attr >> 16) & 0o170000
                if (
                    not member
                    or member.startswith("/")
                    or re.match(r"^[A-Za-z]:", member) is not None
                    or "\\" in member
                    or any(part in ("", ".", "..") for part in parts)
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or (mode == stat.S_IFDIR and not info.is_dir())
                    or (target / member).resolve() == target_resolved
                    or not (target / member).resolve().is_relative_to(target_resolved)
                ):
                    raise LubanError(
                        ErrorCode.VALIDATION_ERROR,
                        f"zip 包含非法路径或文件类型: {member}",
                        details={"member": member},
                    )
            tracker = ZipTracker()
            for info in members:
                dest = target / info.filename
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    for _ in stream_zip_member(zf, info, SKILL_ZIP_QUOTA, tracker):
                        raise LubanError(ErrorCode.VALIDATION_ERROR, "zip 目录含数据")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as output:
                    for chunk in stream_zip_member(zf, info, SKILL_ZIP_QUOTA, tracker):
                        output.write(chunk)

    def get(self, name: str) -> SkillPackage | None:
        return self._packages.get(name)

    def list_metadata(self) -> list[SkillMeta]:
        return [p.meta for p in self._packages.values()]

    def format_metadata_for_prompt(self, names: list[str] | None = None) -> str:
        """L1 元数据清单 ("- name: description" 逐行); names 过滤, 缺省全部。"""
        selected = self._packages.values()
        if names is not None:
            wanted = set(names)
            selected = [p for p in selected if p.meta.name in wanted]
        return "\n".join(f"- {p.meta.name}: {p.meta.description}" for p in selected)

    def make_load_tool(self, *, allowed: list[str] | None = None) -> RegisteredTool:
        """构造 skill_load 工具: args {name} -> {body, scripts, references}。

        allowed 非 None 时仅允许加载清单内 Skill (按 Agent 分配, 代码强制拒绝)。
        """
        descriptor = ToolDescriptor(
            key="skill_load",
            name="skill_load",
            description="加载指定 Skill 的正文与资源清单 (渐进加载: 元数据命中后取正文)",
            args_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk=RiskLevel.LOW,
        )

        async def handler(name: str) -> dict[str, Any]:
            if allowed is not None and name not in allowed:
                raise LubanError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    f"Skill 未分配给该 Agent: {name}",
                    details={"skill": name},
                )
            package = self._packages.get(name)
            if package is None:
                raise LubanError(
                    ErrorCode.VALIDATION_ERROR,
                    f"Skill 不存在: {name}",
                    details={"skill": name},
                )
            return {
                "body": load_body(package),
                "scripts": list(package.scripts),
                "references": list(package.references),
            }

        return RegisteredTool(descriptor=descriptor, handler=handler)
