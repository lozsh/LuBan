#!/usr/bin/env python3
"""`.lubanagent-upstream` 清单守卫（架构 §4.4 升级契约）。

三种模式：
1. 格式自检（总是执行）：四节齐全、glob 合法、user-owned 非空。
2. diff 守卫（git 可用时）：`git diff --name-only <base>...HEAD` 的变更文件中，
   命中 user-owned（且不属于 scaffold-seed）即视为上游越界 → exit 1。
   零 git 上下文（无仓库/无 base ref）时退化为仅格式自检并打印提示。
3. 工作树守卫（`--include-worktree`）：在提交 diff 之外，纳入未提交的 tracked
   修改与未跟踪新文件（`git status --porcelain -z --untracked-files=all`）；
   base ref 不可用但工作树可读时仍按工作树守卫，不静默退化。

用法：
    python scripts/check_upstream_guard.py [--base HEAD~1] [--manifest .lubanagent-upstream]
    python scripts/check_upstream_guard.py --include-worktree  # 并入未提交变更
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

SECTIONS = ("framework", "shared", "scaffold-seed", "user-owned")
_EXTGLOB_RE = re.compile(r"!\(([^)]+)\)")


def parse_manifest(path: Path) -> dict[str, list[str]]:
    """解析 ini 风格清单（裸 glob 行，无 key=value）。"""
    manifest: dict[str, list[str]] = {s: [] for s in SECTIONS}
    current: str | None = None
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if current not in manifest:
                raise SystemExit(f"清单第 {lineno} 行：未知节 [{current}]（合法节：{SECTIONS}）")
            continue
        if current is None:
            raise SystemExit(f"清单第 {lineno} 行：glob 出现在任何节之前")
        manifest[current].append(line)
    return manifest


def validate_format(manifest: dict[str, list[str]]) -> list[str]:
    """格式自检：返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    for section in SECTIONS:
        if not manifest[section]:
            problems.append(f"[{section}] 节为空")
    for section, globs in manifest.items():
        for glob in globs:
            if glob.startswith("/") or ".." in glob.split("/"):
                problems.append(f"[{section}] 非法 glob（绝对路径或越界）: {glob}")
            if _EXTGLOB_RE.search(glob):
                alts = _EXTGLOB_RE.search(glob).group(1).split("|")  # type: ignore[union-attr]
                if not all(alts):
                    problems.append(f"[{section}] extglob 备选项为空: {glob}")
    return problems


def _match_one(pattern: str, path: str) -> bool:
    """单 glob 匹配：支持尾斜杠目录前缀、**、*、? 与 !(a|b) 段内否定。"""
    ext = _EXTGLOB_RE.search(pattern)
    if ext:
        include = pattern[: ext.start()] + "*" + pattern[ext.end() :]
        if not _match_one(include, path):
            return False
        return not any(
            _match_one(pattern[: ext.start()] + alt + pattern[ext.end() :], path)
            for alt in ext.group(1).split("|")
        )
    if pattern.endswith("/"):
        return fnmatch.fnmatchcase(path, pattern + "**") or path == pattern[:-1]
    return fnmatch.fnmatchcase(path, pattern)


def matches(globs: list[str], path: str) -> bool:
    return any(_match_one(g, path) for g in globs)


def is_user_owned(manifest: dict[str, list[str]], path: str) -> bool:
    """user-owned 生效判定：命中 user-owned 且不属于 scaffold-seed（种子上游可动）。"""
    return matches(manifest["user-owned"], path) and not matches(
        manifest["scaffold-seed"], path
    )


def changed_files(base: str) -> list[str] | None:
    """git diff 变更文件；git 不可用或 base 不存在返回 None。"""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def worktree_changes() -> list[str] | None:
    """未提交变更（tracked 修改 + 未跟踪新文件）；git 不可用返回 None。

    用 `-z --untracked-files=all` 保证路径不被引号包裹、逐条 NUL 分隔；
    重命名条目（`orig -> new`）取新路径。
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    files: list[str] = []
    for entry in result.stdout.split("\0"):
        # -z 格式无引号无换行：前两字符是状态码，第 3 字符是空格，路径从第 4 字符起。
        if len(entry) < 4:
            continue
        path = entry[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=".lubanagent-upstream 清单守卫")
    parser.add_argument("--base", default="HEAD~1", help="diff 基准 ref（默认 HEAD~1）")
    parser.add_argument("--manifest", default=".lubanagent-upstream", help="清单文件路径")
    parser.add_argument(
        "--include-worktree",
        action="store_true",
        help="纳入未提交 tracked 修改与未跟踪新文件（上游本地开发自检用）",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"清单文件不存在: {manifest_path}", file=sys.stderr)
        return 1
    manifest = parse_manifest(manifest_path)

    problems = validate_format(manifest)
    if problems:
        print("清单格式自检失败：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("清单格式自检通过（四节齐全、glob 合法、user-owned 非空）")

    files = changed_files(args.base)
    source = "diff"
    if args.include_worktree:
        wt_files = worktree_changes()
        if wt_files is not None:
            files = sorted(set(files or []) | set(wt_files))
            source = "提交 diff + 工作树"
        elif files is not None:
            # git 可用但工作树状态读不到：硬错误，不静默当作零变更。
            print(
                "--include-worktree：git status 读取失败，无法纳入未提交变更",
                file=sys.stderr,
            )
            return 1

    if files is None:
        print(f"无 git 上下文（base={args.base} 不可用），退化为仅格式自检")
        return 0

    violations = [f for f in files if is_user_owned(manifest, f)]
    if violations:
        scope = "上游提交或工作树" if args.include_worktree else "上游提交"
        print(f"{scope}触碰了用户领地（user-owned）：", file=sys.stderr)
        for f in violations:
            print(f"  - {f}", file=sys.stderr)
        print(
            "上游维护者不得改动用户领地；若是 scaffold-seed 初始文件请加入清单对应节。",
            file=sys.stderr,
        )
        return 1
    print(f"{source} 守卫通过（{len(files)} 个变更文件均未触碰 user-owned）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
