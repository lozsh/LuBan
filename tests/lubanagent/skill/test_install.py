"""registry.install/delete: zip 解压安装 + zip slip 防御 + 同名覆盖 + 删除。"""

import glob
import io
import os
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ErrorCode, LubanError
from lubanagent.core.zip_quota import ZipQuota, ZipTracker, check_zip_quota, stream_zip_member
from lubanagent.persistence.repositories import SkillRepository
from lubanagent.skill import SkillRegistry
from lubanagent.skill import registry as registry_module

from .conftest import write_skill

_SKILL_MD = '---\nname: {name}\ndescription: {desc}\nversion: "{ver}"\n---\n{body}\n'


def _zip_bytes(files: dict[str, str]) -> bytes:
    """{相对路径: 内容} → zip bytes（install 测试夹具）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def _skill_zip(name: str, *, desc: str = "x", ver: str = "1", body: str = "x") -> bytes:
    return _zip_bytes(
        {f"{name}/SKILL.md": _SKILL_MD.format(name=name, desc=desc, ver=ver, body=body)}
    )


async def test_install_zip_success(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    package = await registry.install(
        _skill_zip("my-skill", desc="测试 Skill", ver="1.0", body="正文。")
    )
    assert package.meta.name == "my-skill"
    assert package.meta.version == "1.0"
    assert (tmp_path / "my-skill" / "SKILL.md").is_file()
    assert [p.meta.name for p in registry.list_packages()] == ["my-skill"]


@pytest.mark.parametrize(
    ("files", "quota"),
    [
        ({"pkg/SKILL.md": "x", "pkg/extra.txt": "x"}, ZipQuota(1, 128, 128)),
        ({"pkg/SKILL.md": "x" * 64}, ZipQuota(4, 32, 128)),
        ({"pkg/SKILL.md": "x" * 40, "pkg/extra.txt": "y" * 40}, ZipQuota(4, 64, 70)),
    ],
    ids=["members", "member-bytes", "total-bytes"],
)
async def test_install_rejects_header_quota_before_writing(
    tmp_path: Path, files: dict[str, str], quota: ZipQuota
) -> None:
    root = tmp_path / "skills"
    registry = SkillRegistry(skills_root=root)
    await registry.install(_skill_zip("pkg", ver="1"))
    old = registry.get("pkg")
    original = (root / "pkg" / "SKILL.md").read_bytes()
    with patch.object(registry_module, "SKILL_ZIP_QUOTA", quota):
        with pytest.raises(LubanError) as exc:
            await registry.install(_zip_bytes(files))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert (root / "pkg" / "SKILL.md").read_bytes() == original
    assert registry.get("pkg") is old
    assert not glob.glob(str(tmp_path / "luban-skill-*"))


async def test_install_rejects_small_compressed_high_expansion(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    registry = SkillRegistry(skills_root=root)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pkg/SKILL.md", _SKILL_MD.format(name="pkg", desc="x", ver="1", body="x"))
        zf.writestr("pkg/large.txt", "z" * 4096)
    assert len(data.getvalue()) < 4096
    with patch.object(registry_module, "SKILL_ZIP_QUOTA", ZipQuota(4, 128, 256)):
        with pytest.raises(LubanError) as exc:
            await registry.install(data.getvalue())
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert not (root / "pkg").exists()
    assert not glob.glob(str(tmp_path / "luban-skill-*"))


def test_stream_zip_member_rejects_more_bytes_than_header_claims() -> None:
    data = _zip_bytes({"file.txt": "abc"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.infolist()[0]
        quota = ZipQuota(2, 16, 16)
        check_zip_quota(zf, quota)
        with patch.object(zf, "open", return_value=io.BytesIO(b"abcdef")):
            with pytest.raises(LubanError) as exc:
                b"".join(stream_zip_member(zf, info, quota, ZipTracker()))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_stream_zip_member_limits_actual_total_bytes() -> None:
    data = _zip_bytes({"a": "aaa", "b": "bbb"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        first, second = zf.infolist()
        quota = ZipQuota(2, 4, 5)
        # Header claims exactly the total budget; the second reader yields one byte more.
        second.file_size = 2
        check_zip_quota(zf, quota)
        tracker = ZipTracker()
        with patch.object(zf, "open", side_effect=[io.BytesIO(b"aaa"), io.BytesIO(b"bbb")]):
            assert b"".join(stream_zip_member(zf, first, quota, tracker)) == b"aaa"
            with pytest.raises(LubanError) as exc:
                list(stream_zip_member(zf, second, quota, tracker))
        assert exc.value.code == ErrorCode.VALIDATION_ERROR
        assert tracker.total_bytes == 3


async def test_install_spoofed_open_keeps_old_package_and_cleans_staging(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    registry = SkillRegistry(skills_root=root)
    await registry.install(_skill_zip("pkg", ver="1"))
    old = registry.get("pkg")
    original = (root / "pkg" / "SKILL.md").read_bytes()
    real_open = zipfile.ZipFile.open

    def oversized_reader(zf, name, *args, **kwargs):
        if isinstance(name, zipfile.ZipInfo) and name.filename == "pkg/SKILL.md":
            return io.BytesIO(b"x" * (name.file_size + 1))
        return real_open(zf, name, *args, **kwargs)

    with patch.object(zipfile.ZipFile, "open", oversized_reader):
        with pytest.raises(LubanError) as exc:
            await registry.install(_skill_zip("pkg", ver="2"))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert (root / "pkg" / "SKILL.md").read_bytes() == original
    assert registry.get("pkg") is old
    assert not glob.glob(str(tmp_path / "luban-skill-*"))


async def test_install_rejects_directory_member_with_data_and_keeps_old_package(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    registry = SkillRegistry(skills_root=root)
    await registry.install(_skill_zip("pkg", ver="1"))
    before = (root / "pkg" / "SKILL.md").read_bytes()
    original_open = zipfile.ZipFile.open

    def directory_with_data(zf, name, *args, **kwargs):
        if isinstance(name, zipfile.ZipInfo) and name.filename == "pkg/":
            return io.BytesIO(b"unexpected")
        return original_open(zf, name, *args, **kwargs)

    archive = _zip_bytes({
        "pkg/": "", "pkg/SKILL.md": _SKILL_MD.format(name="pkg", desc="x", ver="2", body="x")
    })
    with patch.object(zipfile.ZipFile, "open", directory_with_data):
        with pytest.raises(LubanError, match="实际展开大小: pkg/"):
            await registry.install(archive)
    assert (root / "pkg" / "SKILL.md").read_bytes() == before
    assert registry.get("pkg").meta.version == "1"
    assert not glob.glob(str(tmp_path / "luban-skill-*"))


async def test_install_accepts_explicit_directory_member(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/", "")
        zf.writestr("pkg/SKILL.md", _SKILL_MD.format(name="pkg", desc="x", ver="1", body="x"))
    installed = await SkillRegistry(skills_root=tmp_path).install(buf.getvalue())
    assert installed.meta.name == "pkg"


def test_stream_zip_member_rejects_actual_total_over_quota() -> None:
    data = _zip_bytes({"a": "ab"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        quota = ZipQuota(1, 4, 5)
        check_zip_quota(zf, quota)
        tracker = ZipTracker(total_bytes=4)
        with pytest.raises(LubanError) as exc:
            list(stream_zip_member(zf, zf.infolist()[0], quota, tracker))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert tracker.total_bytes == 4


async def test_install_zip_slip_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    data = _zip_bytes(
        {
            "evil/SKILL.md": _SKILL_MD.format(name="evil", desc="x", ver="1", body="x"),
            "../../evil-marker.txt": "pwned",
        }
    )
    with pytest.raises(LubanError) as exc:
        await registry.install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    # 逃逸文件未写出，skills_root 仅空目录
    assert not os.listdir(tmp_path)


async def test_install_bad_zip_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    with pytest.raises(LubanError) as exc:
        await registry.install(b"not a zip")
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


async def test_install_bad_crc_keeps_existing_package(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.install(_skill_zip("my", ver="1"))
    original = (tmp_path / "my" / "SKILL.md").read_bytes()
    data = bytearray(_skill_zip("my", ver="2"))
    start = data.index(b"description: x")
    data[start] = ord("D")
    with pytest.raises(LubanError) as exc:
        await registry.install(bytes(data))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert (tmp_path / "my" / "SKILL.md").read_bytes() == original


async def test_install_no_skill_md_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    data = _zip_bytes({"orphan/README.md": "no skill md"})
    with pytest.raises(LubanError) as exc:
        await registry.install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


async def test_install_multiple_packages_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    data = _zip_bytes(
        {
            "a/SKILL.md": _SKILL_MD.format(name="a", desc="x", ver="1", body="x"),
            "b/SKILL.md": _SKILL_MD.format(name="b", desc="y", ver="1", body="y"),
        }
    )
    with pytest.raises(LubanError) as exc:
        await registry.install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


async def test_install_overwrites_same_name(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.install(_skill_zip("my", desc="v1", ver="1.0", body="v1"))
    await registry.install(_skill_zip("my", desc="v2", ver="2.0", body="v2"))
    assert registry.get("my").meta.version == "2.0"
    assert "v2" in (tmp_path / "my" / "SKILL.md").read_text(encoding="utf-8")


async def test_install_invalid_name_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    data = _zip_bytes(
        {"ok/SKILL.md": _SKILL_MD.format(name="bad name!", desc="x", ver="1", body="x")}
    )
    with pytest.raises(LubanError) as exc:
        await registry.install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


async def test_delete_removes_dir(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.install(_skill_zip("my"))
    assert (tmp_path / "my").is_dir()
    registry.delete("my")
    assert not (tmp_path / "my").exists()


async def test_delete_path_escape_rejected(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    with pytest.raises(LubanError) as exc:
        registry.delete("../escape")
    assert exc.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize("name", ["", ".", "..", "/tmp/outside", "a/b", "a\\\\b"])
def test_delete_rejects_invalid_names_without_touching_root(tmp_path: Path, name: str) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("safe")
    registry = SkillRegistry(skills_root=root)
    with pytest.raises(LubanError) as exc:
        registry.delete(name)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert marker.read_text() == "safe"


def test_delete_rejects_symlink_to_outside(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("safe")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LubanError) as exc:
        SkillRegistry(skills_root=root).delete("linked")
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert (outside / "keep.txt").read_text() == "safe"
    assert (root / "linked").is_symlink()


@pytest.mark.parametrize("name", [".", "..", "/tmp/outside", "a/b", "a\\\\b"])
async def test_install_rejects_invalid_metadata_name(tmp_path: Path, name: str) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("safe")
    data = _zip_bytes({"pkg/SKILL.md": _SKILL_MD.format(name=name, desc="x", ver="1", body="x")})
    with pytest.raises(LubanError) as exc:
        await SkillRegistry(skills_root=root).install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert marker.read_text() == "safe"
    assert sorted(p.name for p in root.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize(
    "member",
    ["pkg/./note.txt", "pkg/../note.txt", "./pkg/note.txt", "/absolute.txt", "C:/absolute.txt"],
)
async def test_install_rejects_special_zip_members(tmp_path: Path, member: str) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    marker = tmp_path / "keep.txt"
    marker.write_text("safe")
    data = _zip_bytes(
        {
            "pkg/SKILL.md": _SKILL_MD.format(name="pkg", desc="x", ver="1", body="x"),
            member: "bad",
        }
    )
    with pytest.raises(LubanError) as exc:
        await SkillRegistry(skills_root=root).install(data)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert marker.read_text() == "safe"
    assert list(root.iterdir()) == []


async def test_install_rejects_zip_symlink(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/SKILL.md", _SKILL_MD.format(name="pkg", desc="x", ver="1", body="x"))
        link = zipfile.ZipInfo("pkg/scripts/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "../../outside")
    with pytest.raises(LubanError) as exc:
        await SkillRegistry(skills_root=tmp_path).install(buf.getvalue())
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert not (tmp_path / "pkg").exists()


async def test_install_rejects_existing_symlink_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    outside = write_skill(tmp_path, "outside")
    (root / "outside").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LubanError) as exc:
        await SkillRegistry(skills_root=root).install(_skill_zip("outside"))
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert (outside / "SKILL.md").is_file()
    assert (root / "outside").is_symlink()


async def test_invalid_overlay_keeps_old_package_and_version(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    registry = SkillRegistry(skills_root=root)
    await registry.install(_skill_zip("my", ver="1"))
    old = registry.get("my")
    old_text = (root / "my" / "SKILL.md").read_text()
    marker = tmp_path / "keep.txt"
    marker.write_text("safe")
    for data in (b"not a zip", _zip_bytes({"orphan/README.md": "invalid"})):
        with pytest.raises(LubanError) as exc:
            await registry.install(data)
        assert exc.value.code == ErrorCode.VALIDATION_ERROR
        assert (root / "my" / "SKILL.md").read_text() == old_text
        assert registry.get("my") is old
        assert marker.read_text() == "safe"


async def test_install_scan_failure_restores_old_disk_and_memory(tmp_path: Path) -> None:
    registry = SkillRegistry(skills_root=tmp_path)
    await registry.install(_skill_zip("my", ver="1"))
    before = registry.get("my")
    old_text = (tmp_path / "my" / "SKILL.md").read_text()
    marker = tmp_path.parent / "keep.txt"
    marker.write_text("safe")
    from lubanagent.skill.registry import scan_skills_dir

    calls = 0

    def fail_after_staging(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("scan failed")
        return scan_skills_dir(path)

    with patch("lubanagent.skill.registry.scan_skills_dir", side_effect=fail_after_staging):
        with pytest.raises(RuntimeError, match="scan failed"):
            await registry.install(_skill_zip("my", ver="2"))
    assert calls == 2
    assert (tmp_path / "my" / "SKILL.md").read_text() == old_text
    assert registry.get("my") is before
    assert marker.read_text() == "safe"


async def test_install_repository_failure_restores_disk_memory_and_db(
    tmp_path: Path, session: AsyncSession
) -> None:
    class FailingRepository(SkillRepository):
        async def upsert(self, skill, *, enabled=True):
            await super().upsert(skill, enabled=enabled)
            if skill.meta.name == "my" and skill.meta.version == "2":
                raise RuntimeError("repo failed")

    root = tmp_path / "skills"
    repo = FailingRepository(session)
    registry = SkillRegistry(skills_root=root, repository=repo)
    await registry.install(_skill_zip("my", ver="1"))
    old_package = registry.get("my")
    old_text = (root / "my" / "SKILL.md").read_text()
    with pytest.raises(RuntimeError, match="repo failed"):
        await registry.install(_skill_zip("my", ver="2"))
    assert (root / "my" / "SKILL.md").read_text() == old_text
    assert registry.get("my") is old_package
    assert (await repo.get("my")).meta.version == "1"
