"""loader: frontmatter 解析（含非法）、目录扫描、正文/资源加载、内容 hash。"""

from pathlib import Path

import pytest

from lubanagent.core import ErrorCode, LubanError
from lubanagent.skill import (
    content_hash,
    load_body,
    load_resource,
    parse_skill_md,
    scan_skills_dir,
)

from .conftest import write_skill


def test_parse_skill_md_ok(tmp_path: Path) -> None:
    package_dir = write_skill(
        tmp_path, "refund", description="退款技能", version="1.2.3", body="按规则退款。"
    )
    meta, body = parse_skill_md(package_dir / "SKILL.md")
    assert meta.name == "refund"
    assert meta.description == "退款技能"
    assert meta.version == "1.2.3"
    assert body == "按规则退款。"


def test_parse_skill_md_version_optional(tmp_path: Path) -> None:
    package_dir = tmp_path / "plain"
    package_dir.mkdir()
    (package_dir / "SKILL.md").write_text(
        "---\nname: plain\ndescription: 无版本\n---\n正文\n", encoding="utf-8"
    )
    meta, body = parse_skill_md(package_dir / "SKILL.md")
    assert meta.version == "0.0.0"
    assert body == "正文"


@pytest.mark.parametrize(
    "text",
    [
        "没有 frontmatter\n",
        "---\nname: x\ndescription: y\n",  # 未闭合
        "---\n- 不是映射\n---\n",  # frontmatter 非 dict
        "---\ndescription: 缺 name\n---\n",  # 缺 name
        "---\nname: 缺 description\n---\n",  # 缺 description
        "---\nname: [非法\nyaml:\n---\n",  # 非法 YAML
    ],
)
def test_parse_skill_md_invalid_raises(tmp_path: Path, text: str) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LubanError) as exc_info:
        parse_skill_md(path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_scan_skills_dir_multi_package(tmp_path: Path) -> None:
    write_skill(tmp_path, "a-skill", description="甲", scripts={"run.py": "print('a')"})
    write_skill(
        tmp_path,
        "b-skill",
        description="乙",
        scripts={"tool.sh": "echo b"},
        references={"guide.md": "指南"},
        assets={"logo.png": "fake-bytes"},
    )
    (tmp_path / "not-a-skill").mkdir()  # 无 SKILL.md 的目录被跳过

    packages = scan_skills_dir(tmp_path)
    assert [p.meta.name for p in packages] == ["a-skill", "b-skill"]

    b = packages[1]
    assert b.path == str((tmp_path / "b-skill").resolve())
    assert b.scripts == ["scripts/tool.sh"]
    assert b.references == ["references/guide.md"]
    assert b.assets == ["assets/logo.png"]
    assert b.content_hash  # 非空

    # L1 扫描不读正文: 正文只在 load_body 时取
    assert load_body(b) == "这是正文。"


def test_scan_skills_dir_missing_root_returns_empty(tmp_path: Path) -> None:
    assert scan_skills_dir(tmp_path / "ghost") == []


def test_scan_skills_dir_rejects_symlinked_package_and_resource(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    outside = write_skill(tmp_path, "outside", references={"private.txt": "sentinel"})
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LubanError) as exc:
        scan_skills_dir(root)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    (root / "linked").unlink()
    package = write_skill(root, "safe")
    (package / "references").mkdir()
    (package / "references" / "private.txt").symlink_to(outside / "references" / "private.txt")
    with pytest.raises(LubanError) as exc:
        scan_skills_dir(root)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_scan_skills_dir_invalid_package_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("没有 frontmatter\n", encoding="utf-8")
    with pytest.raises(LubanError) as exc_info:
        scan_skills_dir(tmp_path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_load_resource_ok(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo", references={"guide.md": "参考内容"})
    package = scan_skills_dir(tmp_path)[0]
    assert load_resource(package, "references/guide.md") == "参考内容".encode()


def test_load_resource_escape_rejected(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo")
    (tmp_path / "outside.txt").write_text("机密", encoding="utf-8")
    package = scan_skills_dir(tmp_path)[0]
    with pytest.raises(LubanError) as exc_info:
        load_resource(package, "../outside.txt")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_load_resource_missing_rejected(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo")
    package = scan_skills_dir(tmp_path)[0]
    with pytest.raises(LubanError) as exc_info:
        load_resource(package, "references/ghost.md")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_content_hash_changes_with_content(tmp_path: Path) -> None:
    package_dir = write_skill(tmp_path, "demo")
    before = content_hash(package_dir)
    assert content_hash(package_dir) == before  # 稳定

    (package_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 改了\n---\n新正文\n", encoding="utf-8"
    )
    assert content_hash(package_dir) != before
