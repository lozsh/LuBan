#!/usr/bin/env python3
"""覆盖率分项门槛（开发计划 D1-9：核心 ≥85%、整体 ≥70%）。

coverage 原生 fail_under 不支持子包分项，故用本脚本读 coverage json 报告断言：
- 整体覆盖率 ≥ --total（默认 70）
- 核心子包（CORE_PACKAGES）各自 ≥ --core（默认 85）
不达标打印缺口清单并 exit 1。

前置：pytest --cov=lubanagent --cov-report=json（生成 coverage.json）。

用法：
    python scripts/check_coverage.py [--report coverage.json] [--total 70] [--core 85]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 核心子包：基座成色所在，门槛 85%（契约 D1-9）
CORE_PACKAGES = (
    "core",
    "extension",
    "config",
    "model",
    "runtime",
    "tool",
    "persistence",
    "observability",
)


def load_report(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(
            f"覆盖率报告不存在: {path}（先跑 pytest --cov=lubanagent --cov-report=json）"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def package_key(file_path: str) -> str | None:
    """lubanagent/core/x.py → core；lubanagent/x.py → <root>。"""
    parts = Path(file_path).parts
    if "lubanagent" not in parts:
        return None
    idx = parts.index("lubanagent")
    rest = parts[idx + 1 :]
    if not rest:
        return None
    return rest[0] if len(rest) > 1 else "<root>"


def aggregate(report: dict) -> tuple[dict[str, float], float]:
    """按子包聚合覆盖率（语句加权），返回 (子包 → 覆盖率, 整体覆盖率)。"""
    stats: dict[str, list[int]] = {}
    for file_path, data in report["files"].items():
        key = package_key(file_path)
        if key is None:
            continue
        summary = data["summary"]
        bucket = stats.setdefault(key, [0, 0])
        bucket[0] += summary["covered_lines"]
        bucket[1] += summary["num_statements"]
    packages = {
        key: (covered / total * 100 if total else 100.0) for key, (covered, total) in stats.items()
    }
    total_summary = report["totals"]
    overall = (
        total_summary["covered_lines"] / total_summary["num_statements"] * 100
        if total_summary["num_statements"]
        else 100.0
    )
    return packages, overall


def main() -> int:
    parser = argparse.ArgumentParser(description="覆盖率分项门槛断言")
    parser.add_argument("--report", default="coverage.json", help="coverage json 报告路径")
    parser.add_argument("--total", type=float, default=70.0, help="整体门槛（默认 70%%）")
    parser.add_argument("--core", type=float, default=85.0, help="核心子包门槛（默认 85%%）")
    args = parser.parse_args()

    report = load_report(Path(args.report))
    packages, overall = aggregate(report)

    failures: list[str] = []
    if overall < args.total:
        failures.append(f"整体覆盖率 {overall:.1f}% < {args.total:.1f}%")
    for package in CORE_PACKAGES:
        covered = packages.get(package)
        if covered is None:
            failures.append(f"核心子包 {package} 无覆盖率数据")
        elif covered < args.core:
            failures.append(f"核心子包 {package} 覆盖率 {covered:.1f}% < {args.core:.1f}%")

    print(f"整体覆盖率: {overall:.1f}%（门槛 {args.total:.1f}%）")
    for package in CORE_PACKAGES:
        covered = packages.get(package)
        mark = "N/A " if covered is None else f"{covered:.1f}%"
        print(f"  {package:<14} {mark}（核心门槛 {args.core:.1f}%）")

    if failures:
        print("\n覆盖率门槛未达标：", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("覆盖率门槛全部达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
