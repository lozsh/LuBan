"""Skill 加载器: SKILL.md 解析与目录扫描, 渐进加载 L1 元数据 / L2 正文 / L3 资源。"""

import hashlib
from pathlib import Path

import yaml

from lubanagent.core import ErrorCode, LubanError, SkillMeta, SkillPackage

_SKILL_MD = "SKILL.md"


def _invalid(message: str, *, details: dict | None = None) -> LubanError:
    return LubanError(ErrorCode.VALIDATION_ERROR, message, details=details)


def parse_skill_md(path: Path) -> tuple[SkillMeta, str]:
    """解析 SKILL.md: YAML frontmatter (name/description 必填) + Markdown 正文。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _invalid(f"SKILL.md 读取失败: {path}", details={"reason": str(exc)}) from None

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise _invalid(f"SKILL.md 缺少 YAML frontmatter: {path}")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise _invalid(f"SKILL.md frontmatter 未闭合: {path}")

    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise _invalid(
            f"SKILL.md frontmatter 非法 YAML: {path}", details={"reason": str(exc)}
        ) from None
    if not isinstance(data, dict):
        raise _invalid(f"SKILL.md frontmatter 必须是映射: {path}")

    name, description = data.get("name"), data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise _invalid(f"SKILL.md frontmatter 缺少必填字段 name: {path}")
    if not isinstance(description, str) or not description.strip():
        raise _invalid(f"SKILL.md frontmatter 缺少必填字段 description: {path}")
    version = data.get("version", "0.0.0")

    meta = SkillMeta(name=name.strip(), description=description.strip(), version=str(version))
    return meta, "\n".join(lines[end + 1 :]).strip()


def _reject_links(package_dir: Path) -> None:
    """拒绝包内所有链接，避免扫描、hash 和后续按需读取越过包边界。"""
    for entry in package_dir.rglob("*"):
        if entry.is_symlink():
            raise _invalid(f"Skill 包含符号链接: {entry}")


def content_hash(package_dir: Path) -> str:
    """全包文件 sha256 聚合 (相对路径+内容), 用于同 name 内容变化判定。"""
    _reject_links(package_dir)
    digest = hashlib.sha256()
    files = sorted(p for p in package_dir.rglob("*") if p.is_file())
    for file in files:
        digest.update(file.relative_to(package_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _list_rel(package_dir: Path, sub: str) -> list[str]:
    base = package_dir / sub
    if not base.is_dir():
        return []
    return sorted(p.relative_to(package_dir).as_posix() for p in base.rglob("*") if p.is_file())


def scan_skills_dir(root: Path) -> list[SkillPackage]:
    """扫描 root/*/SKILL.md (L1+清单, 不读正文); 无法解析的包抛 VALIDATION_ERROR, 不静默跳过。"""
    packages: list[SkillPackage] = []
    if not root.is_dir():
        return packages
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            raise _invalid(f"Skill 根目录含符号链接: {child}")
        if not child.is_dir():
            continue
        skill_md = child / _SKILL_MD
        if skill_md.is_symlink():
            raise _invalid(f"Skill 元数据含符号链接: {skill_md}")
        if not skill_md.is_file():
            continue
        _reject_links(child)
        meta, _ = parse_skill_md(skill_md)
        package_dir = child.resolve()
        packages.append(
            SkillPackage(
                meta=meta,
                path=str(package_dir),
                scripts=_list_rel(package_dir, "scripts"),
                references=_list_rel(package_dir, "references"),
                assets=_list_rel(package_dir, "assets"),
                content_hash=content_hash(package_dir),
            )
        )
    return packages


def load_body(package: SkillPackage) -> str:
    """L2: 读 SKILL.md 正文。"""
    _, body = parse_skill_md(Path(package.path) / _SKILL_MD)
    return body


def load_resource(package: SkillPackage, rel_path: str) -> bytes:
    """L3: 读包内资源文件; resolve 后必须仍在包目录内, 否则拒绝 (防路径逃逸)。"""
    base = Path(package.path).resolve()
    resolved = (base / rel_path).resolve()
    if not resolved.is_relative_to(base):
        raise _invalid(
            f"资源路径逃逸被拒绝: {rel_path}",
            details={"package": package.meta.name, "rel_path": rel_path},
        )
    if not resolved.is_file():
        raise _invalid(
            f"资源不存在: {rel_path}", details={"package": package.meta.name, "rel_path": rel_path}
        )
    return resolved.read_bytes()
