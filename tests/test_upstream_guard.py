"""H15 验收入口：`.lubanagent-upstream` 清单守卫（临时仓库内正/反例）。

覆盖计划 §5 专用验收入口契约：
- 临时 git 仓库内正例（framework/shared 变更放行）与反例（user-owned 命中拒绝）；
- `--include-worktree` 纳入未提交 tracked 修改与未跟踪新用户路径；
- 合法 scaffold-seed 更新不误拒绝（含 user-owned 与 scaffold-seed 重叠时以 seed 为准）；
- CI 缺省模式仍只审提交 diff。

隔离约定：全部在 `tmp_path` 临时仓库内进行，git 子进程使用最小 env
（独立 HOME、空全局/系统配置），不触碰真实仓库工作区，不访问网络。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_SCRIPT = REPO_ROOT / "scripts" / "check_upstream_guard.py"
REAL_MANIFEST = REPO_ROOT / ".lubanagent-upstream"

# 合成清单：覆盖 extglob 例外、目录前缀、user-owned/scaffold-seed 重叠三类形态。
SYNTH_MANIFEST = """\
[framework]
lib/

[shared]
docs/

[scaffold-seed]
app/main.py
app/config/seed.py
app/agents/customer_service.yaml
app/**/__init__.py

[user-owned]
app/agents/!(customer_service).yaml
app/config/!(.env.example)
app/tools/!(customer_service)/
app/extensions/
"""


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("check_upstream_guard", GUARD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.name", "guard-test")
    _git(repo, "config", "user.email", "guard-test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")


def _write(repo: Path, rel: str, content: str = "x\n") -> Path:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    result = _git(repo, "commit", "-m", message, check=False)
    assert result.returncode == 0, result.stderr


def _run_guard(
    repo: Path, manifest: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(repo / ".home"),
        "GIT_CONFIG_GLOBAL": str(repo / ".empty-gitconfig"),
        "GIT_CONFIG_SYSTEM": str(repo / ".empty-gitconfig"),
        "LC_ALL": "C",
    }
    return subprocess.run(
        [
            sys.executable,
            str(GUARD_SCRIPT),
            "--manifest",
            str(manifest),
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """带初始提交的临时仓库：合成清单 + 一个 framework 文件。"""
    root = tmp_path / "repo"
    _init_repo(root)
    (root / ".empty-gitconfig").write_text("", encoding="utf-8")
    (root / "manifest").write_text(SYNTH_MANIFEST, encoding="utf-8")
    _write(root, "lib/core.py")
    _commit_all(root, "init: framework + manifest")
    return root


class TestCommittedDiffMode:
    """CI 缺省语义：只审 `base...HEAD` 提交 diff。"""

    def test_framework_and_shared_changes_pass(self, repo: Path) -> None:
        _write(repo, "lib/extra.py")
        _write(repo, "docs/note.md")
        _commit_all(repo, "feat: framework + shared")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD~1")
        assert result.returncode == 0, result.stderr
        assert "diff 守卫通过" in result.stdout

    def test_user_owned_commit_rejected(self, repo: Path) -> None:
        _write(repo, "app/agents/my_agent.yaml")
        _commit_all(repo, "feat: user agent")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD~1")
        assert result.returncode == 1
        assert "app/agents/my_agent.yaml" in result.stderr

    def test_user_owned_directory_glob_rejected(self, repo: Path) -> None:
        _write(repo, "app/tools/my_tools/scan.py")
        _commit_all(repo, "feat: user tools dir")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD~1")
        assert result.returncode == 1

    def test_seed_update_not_rejected(self, repo: Path) -> None:
        _write(repo, "app/main.py", "seed v2\n")
        _commit_all(repo, "feat!: breaking seed change")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD~1")
        assert result.returncode == 0, result.stderr

    def test_seed_takes_precedence_over_overlapping_user_owned(
        self, repo: Path
    ) -> None:
        """app/config/seed.py 同时命中 user-owned 与 scaffold-seed，以 seed 为准。"""
        _write(repo, "app/config/seed.py", "seed v2\n")
        _commit_all(repo, "feat: legal seed update")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD~1")
        assert result.returncode == 0, result.stderr

    def test_uncommitted_changes_ignored_by_default(self, repo: Path) -> None:
        """缺省（CI 语义）不看工作树：未提交的 user-owned 修改不触发。"""
        _write(repo, "app/extensions/custom.py")
        result = _run_guard(repo, repo / "manifest", "--base", "HEAD")
        assert result.returncode == 0, result.stderr


class TestWorktreeMode:
    """`--include-worktree`：纳入未提交 tracked 修改与未跟踪新用户路径。"""

    def test_uncommitted_tracked_user_file_rejected(self, repo: Path) -> None:
        _write(repo, "app/agents/my_agent.yaml")
        _commit_all(repo, "feat: user agent")
        _write(repo, "app/agents/my_agent.yaml", "modified\n")  # 未提交修改
        result = _run_guard(
            repo, repo / "manifest", "--base", "HEAD", "--include-worktree"
        )
        assert result.returncode == 1
        assert "app/agents/my_agent.yaml" in result.stderr

    def test_new_untracked_user_path_rejected(self, repo: Path) -> None:
        _write(repo, "app/extensions/custom.py")
        result = _run_guard(
            repo, repo / "manifest", "--base", "HEAD", "--include-worktree"
        )
        assert result.returncode == 1
        assert "app/extensions/custom.py" in result.stderr

    def test_clean_worktree_with_worktree_flag_passes(self, repo: Path) -> None:
        result = _run_guard(
            repo, repo / "manifest", "--base", "HEAD", "--include-worktree"
        )
        assert result.returncode == 0, result.stderr

    def test_worktree_flag_with_unusable_base_still_guards(
        self, repo: Path
    ) -> None:
        """base ref 不存在但工作树可读时，仍按工作树守卫（不静默退化）。"""
        _write(repo, "app/extensions/custom.py")
        result = _run_guard(
            repo, repo / "manifest", "--base", "no-such-ref", "--include-worktree"
        )
        assert result.returncode == 1
        assert "app/extensions/custom.py" in result.stderr


class TestManifestFormat:
    def test_real_manifest_format_self_check(self, repo: Path) -> None:
        """真实清单拷入临时仓库：格式自检通过，零变更 diff 放行。"""
        (repo / "real-manifest").write_bytes(REAL_MANIFEST.read_bytes())
        result = _run_guard(repo, repo / "real-manifest", "--base", "HEAD")
        assert result.returncode == 0, result.stderr
        assert "清单格式自检通过" in result.stdout

    def test_unknown_section_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad-manifest"
        bad.write_text("[framework]\nlib/\n[bogus]\nx/\n", encoding="utf-8")
        module = _load_guard_module()
        with pytest.raises(SystemExit):
            module.parse_manifest(bad)

    def test_empty_user_owned_rejected(self, tmp_path: Path) -> None:
        module = _load_guard_module()
        text = "[framework]\nlib/\n[shared]\ndocs/\n[scaffold-seed]\nseed.py\n[user-owned]\n"
        path = tmp_path / "empty-user-owned"
        path.write_text(text, encoding="utf-8")
        parsed = module.parse_manifest(path)
        problems = module.validate_format(parsed)
        assert any("[user-owned] 节为空" in p for p in problems)


class TestMatchSemantics:
    """glob 判定纯函数：不落盘，直接断言匹配语义。"""

    @pytest.fixture()
    def manifest(self, tmp_path: Path):
        path = tmp_path / "synth-manifest"
        path.write_text(SYNTH_MANIFEST, encoding="utf-8")
        return _load_guard_module().parse_manifest(path)

    def test_user_owned_new_config_file(self, manifest) -> None:
        module = _load_guard_module()
        assert module.is_user_owned(manifest, "app/config/user_extra.py") is True

    def test_env_example_exception_is_seed(self, manifest) -> None:
        module = _load_guard_module()
        assert module.is_user_owned(manifest, "app/config/.env.example") is False
        assert module.is_user_owned(manifest, "app/config/seed.py") is False

    def test_config_init_is_seed(self, manifest) -> None:
        module = _load_guard_module()
        assert module.is_user_owned(manifest, "app/config/__init__.py") is False

    def test_framework_file_not_user_owned(self, manifest) -> None:
        module = _load_guard_module()
        assert module.is_user_owned(manifest, "lib/core.py") is False

    def test_directory_prefix_glob(self, manifest) -> None:
        module = _load_guard_module()
        assert module.is_user_owned(manifest, "app/tools/my_tools/deep/x.py") is True
        assert module.is_user_owned(manifest, "app/tools/customer_service/x.py") is False
