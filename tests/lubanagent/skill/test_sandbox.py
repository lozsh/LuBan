"""sandbox: run_script 执行五态（stdout 捕获/exit_code/超时杀进程组/网络白名单/输出截断）
+ 逃逸拒绝 + 沙箱加固（env 白名单防密钥泄漏 / POSIX 资源限额杀失控脚本）。"""

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

import lubanagent.skill.sandbox as sandbox
from lubanagent.core import ErrorCode, LubanError
from lubanagent.skill import DEFAULT_ENV_WHITELIST, ResourceLimits, run_script, scan_skills_dir
from lubanagent.skill.sandbox import _rlimit_as_supported

from .conftest import write_skill


async def test_run_script_stdout_and_exit_code(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo", scripts={"hello.py": "print('hello skill')"})
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/hello.py")
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello skill"
    assert result.timed_out is False
    assert result.duration_ms > 0


async def test_run_script_nonzero_exit_and_stderr(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "demo",
        scripts={"fail.py": "import sys; print('oops', file=sys.stderr); sys.exit(3)"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/fail.py")
    assert result.exit_code == 3
    assert "oops" in result.stderr
    assert result.timed_out is False


async def test_run_script_timeout_kills_process_group(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo", scripts={"slow.sh": "sleep 10\necho done\n"})
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/slow.sh", timeout_seconds=0.5)
    assert result.timed_out is True
    assert result.exit_code != 0
    assert "done" not in result.stdout
    assert result.duration_ms < 5_000  # 确实被提前终止, 没跑满 sleep 10


async def test_run_script_network_whitelist_env(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "demo",
        scripts={"env.py": "import os; print(os.environ.get('SKILL_NET_WHITELIST', ''))"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package, "scripts/env.py", network_whitelist=["a.com", "b.com"]
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "a.com,b.com"


async def test_run_script_output_truncated(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo", scripts={"loud.py": "print('x' * 20000)"})
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/loud.py", max_output_chars=100)
    assert result.exit_code == 0
    assert len(result.stdout) == 100
    assert result.stdout == "x" * 100


async def test_run_script_small_quota_drains_both_streams(tmp_path: Path, monkeypatch) -> None:
    """小配额只保留前缀，丢弃洪泛内容但允许脚本完成并返回真实退出码。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "flood.py": (
                "import sys\n"
                "for _ in range(32):\n"
                "    sys.stdout.buffer.write(b'a' * 4096)\n"
                "    sys.stderr.buffer.write(b'b' * 4096)\n"
                "sys.stdout.flush(); sys.stderr.flush()\n"
                "sys.exit(7)\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]
    original_truncate = sandbox._truncate
    captured_lengths: list[int] = []

    def check_bounded(text: str, max_chars: int) -> str:
        captured_lengths.append(len(text.encode("utf-8")))
        assert len(text.encode("utf-8")) <= max_chars * 4
        return original_truncate(text, max_chars)

    monkeypatch.setattr(sandbox, "_truncate", check_bounded)
    result = await asyncio.wait_for(
        run_script(package, "scripts/flood.py", max_output_chars=7, timeout_seconds=5), 7
    )
    assert result.exit_code == 7
    assert result.timed_out is False
    assert result.stdout == "a" * 7
    assert result.stderr == "b" * 7
    assert len(captured_lengths) == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组专用行为")
@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_run_script_spawn_pending_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    write_skill(tmp_path, "demo", scripts={"wait.py": "print('never')"})
    package = scan_skills_dir(tmp_path)[0]
    original_spawn = asyncio.create_subprocess_exec
    entered = asyncio.Event()
    release = asyncio.Event()
    children = []

    async def delayed_spawn(*args, **kwargs):
        entered.set()
        await release.wait()
        child = await original_spawn(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(run_script(
        package, "scripts/wait.py", timeout_seconds=0.05 if mode == "timeout" else 5,
    ))
    await asyncio.wait_for(entered.wait(), 2)
    if mode == "cancel":
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    else:
        await asyncio.sleep(0.1)
        release.set()
        result = await asyncio.wait_for(task, 3)
        assert result.timed_out and result.exit_code != 0
    assert len(children) == 1 and children[0].returncode is not None


async def test_run_script_unicode_and_invalid_utf8_prefix(tmp_path: Path) -> None:
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "unicode.py": (
                "import sys\n"
                "sys.stdout.buffer.write(('猫' * 20).encode() + b'END')\n"
                "sys.stderr.buffer.write(b'\\xff' + ('🌟' * 20).encode())\n"
            )
        },
    )
    result = await run_script(
        scan_skills_dir(tmp_path)[0], "scripts/unicode.py", max_output_chars=3
    )
    assert result.stdout == "猫" * 3
    assert result.stderr == "�🌟🌟"
    assert result.exit_code == 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组专用行为")
async def test_run_script_timeout_drains_and_kills_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "timeout-child-marker"
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "timeout.py": (
                "import os, subprocess, sys, time\n"
                "sys.stdout.buffer.write(b'x' * 65536); sys.stdout.flush()\n"
                "sys.stderr.buffer.write(b'y' * 65536); sys.stderr.flush()\n"
                "subprocess.Popen([sys.executable, '-c', "
                "'import pathlib, sys, time; time.sleep(1); "
                "pathlib.Path(sys.argv[1]).write_text(\"escaped\")', "
                "os.environ['MARKER']])\n"
                "time.sleep(10)\n"
            )
        },
    )
    result = await asyncio.wait_for(
        run_script(scan_skills_dir(tmp_path)[0], "scripts/timeout.py", timeout_seconds=0.4,
                   max_output_chars=5, extra_env={"MARKER": str(marker)}),
        4,
    )
    assert result.timed_out is True
    assert result.exit_code != 0
    assert result.stdout == "x" * 5
    assert result.stderr == "y" * 5
    await asyncio.sleep(1.1)
    assert not marker.exists()


def _wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 3
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"脚本未写入启动标记: {path}")
        time.sleep(0.01)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 进程组专用行为")
async def test_run_script_cancel_kills_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "cancel-child-marker"
    ready = tmp_path / "child-ready"
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "cancel.py": (
                "import os, pathlib, subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', "
                "'import pathlib, sys, time; time.sleep(1); "
                "pathlib.Path(sys.argv[1]).write_text(\"escaped\")', "
                "os.environ['MARKER']])\n"
                "pathlib.Path(os.environ['READY']).write_text('ready')\n"
                "time.sleep(10)\n"
            )
        },
    )
    task = asyncio.create_task(
        run_script(scan_skills_dir(tmp_path)[0], "scripts/cancel.py",
                   extra_env={"MARKER": str(marker), "READY": str(ready)})
    )
    try:
        async with asyncio.timeout(3):
            await asyncio.to_thread(_wait_for_file, ready)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.sleep(1.1)
        assert not marker.exists()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_run_script_args_passthrough(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo", scripts={"echo.py": "import sys; print(sys.argv[1])"})
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/echo.py", args=["hi-arg"])
    assert result.stdout.strip() == "hi-arg"


@pytest.mark.parametrize(
    "rel_path",
    [
        "../outside.py",  # 逃逸出包目录
        "scripts/../../outside.py",  # 伪装成 scripts 的逃逸
        "references/guide.md",  # 包内但不在 scripts/ 子目录
        "SKILL.md",  # 包内但不在 scripts/ 子目录
    ],
)
async def test_run_script_path_rejected(tmp_path: Path, rel_path: str) -> None:
    write_skill(tmp_path, "demo", references={"guide.md": "参考"})
    (tmp_path / "outside.py").write_text("print('evil')", encoding="utf-8")
    package = scan_skills_dir(tmp_path)[0]

    with pytest.raises(LubanError) as exc_info:
        await run_script(package, rel_path)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


async def test_run_script_missing_rejected(tmp_path: Path) -> None:
    write_skill(tmp_path, "demo")
    package = scan_skills_dir(tmp_path)[0]

    with pytest.raises(LubanError) as exc_info:
        await run_script(package, "scripts/ghost.py")
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


# ---------- 沙箱加固: 环境变量白名单 ----------


async def test_run_script_host_secret_not_visible(tmp_path: Path, monkeypatch) -> None:
    """宿主密钥类 env (哨兵变量 / *_API_KEY) 默认不可见——子进程只继承白名单。"""
    monkeypatch.setenv("LUBAN_TEST_SECRET", "top-secret-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-host-leak")
    write_skill(
        tmp_path,
        "demo",
        scripts={"envdump.py": "import json, os; print(json.dumps(dict(os.environ)))"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/envdump.py")
    assert result.exit_code == 0
    assert "LUBAN_TEST_SECRET" not in result.stdout
    assert "top-secret-value" not in result.stdout
    assert "DEEPSEEK_API_KEY" not in result.stdout
    assert "sk-host-leak" not in result.stdout


async def test_run_script_whitelisted_env_visible(tmp_path: Path) -> None:
    """白名单内运行必需变量 (PATH/LANG 等) 正常透传, 脚本可跑。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "envcheck.py": (
                "import os\n"
                "print('PATH' in os.environ, bool(os.environ.get('PATH')))\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/envcheck.py")
    assert result.exit_code == 0
    assert result.stdout.strip() == "True True"


async def test_run_script_extra_env_injected(tmp_path: Path) -> None:
    """调用方可显式注入额外变量 (显式授权, 原样透传)。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={"token.py": "import os; print(os.environ.get('MY_TOOL_TOKEN', 'ABSENT'))"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/token.py", extra_env={"MY_TOOL_TOKEN": "tok-1"})
    assert result.exit_code == 0
    assert result.stdout.strip() == "tok-1"


async def test_run_script_env_whitelist_configurable(tmp_path: Path, monkeypatch) -> None:
    """白名单可配置: 自定义后按新名单筛选 (哨兵可见 / 未列入的 PATH 不可见)。"""
    monkeypatch.setenv("LUBAN_TEST_SECRET", "top-secret-value")
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "envspy.py": (
                "import os\n"
                "print(os.environ.get('LUBAN_TEST_SECRET', 'ABSENT'))\n"
                "print(os.environ.get('PATH', 'ABSENT'))\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/envspy.py", env_whitelist=["LUBAN_TEST_SECRET"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "top-secret-value"  # 白名单内的哨兵可见
    assert lines[1] == "ABSENT"  # 未列入白名单的 PATH 不可见


def test_default_env_whitelist_no_secret_patterns() -> None:
    """默认白名单不包含任何密钥类模式; 支持精确名与 ``*`` 前缀两种匹配。"""
    from lubanagent.skill.sandbox import _env_allowed

    for name in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"):
        assert _env_allowed(name, DEFAULT_ENV_WHITELIST), name
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LUBAN_TEST_SECRET", "ANTHROPIC_TOKEN"):
        assert not _env_allowed(name, DEFAULT_ENV_WHITELIST), name


# ---------- 沙箱加固: POSIX 资源限额 ----------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX rlimit 专用行为")
as_supported = pytest.mark.skipif(
    not _rlimit_as_supported(), reason="本平台内核不接受 RLIMIT_AS (macOS 全系拒绝)"
)


@posix_only
async def test_run_script_cpu_limit_kills_spinloop(tmp_path: Path) -> None:
    """CPU 限额: 死循环脚本被 RLIMIT_CPU (SIGXCPU) 杀, 而非靠外层超时。"""
    write_skill(tmp_path, "demo", scripts={"spin.py": "while True:\n    pass\n"})
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package,
        "scripts/spin.py",
        limits=ResourceLimits(cpu_seconds=1),
        timeout_seconds=20,  # 远大于 CPU 限额, 确保是 rlimit 先杀
    )
    assert result.timed_out is False
    assert result.exit_code != 0
    assert result.exit_code == -signal.SIGXCPU


@as_supported
async def test_run_script_memory_limit_kills_hog(tmp_path: Path) -> None:
    """内存限额: 吃内存脚本在 RLIMIT_AS 边界触发 MemoryError 被终止。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={"hog.py": "data = []\nwhile True:\n    data.append(b'x' * (8 * 1024 * 1024))\n"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package,
        "scripts/hog.py",
        limits=ResourceLimits(memory_mb=64),
        timeout_seconds=30,
    )
    assert result.timed_out is False
    assert result.exit_code != 0
    assert "MemoryError" in result.stderr


@posix_only
async def test_run_script_file_size_limit(tmp_path: Path) -> None:
    """文件大小限额: 超限写入被拒 (OSError EFBIG / SIGXFSZ), 全量内容未落盘。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "dump.py": (
                "import tempfile\n"
                "f = tempfile.NamedTemporaryFile(delete=False)\n"
                "for _ in range(8):\n"
                "    f.write(b'x' * (512 * 1024))\n"
                "f.close()\n"
                "print('written-all')\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package,
        "scripts/dump.py",
        limits=ResourceLimits(max_file_mb=1),
        timeout_seconds=30,
    )
    assert result.timed_out is False
    assert result.exit_code != 0
    assert "written-all" not in result.stdout


@posix_only
async def test_run_script_limits_off_by_explicit_none(tmp_path: Path) -> None:
    """显式关闭限额 (全 None): 长跑脚本不受 rlimit 影响, 仍由外层超时兜底。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={"spin.py": "n = 0\nwhile True:\n    n += 1\n"},
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package,
        "scripts/spin.py",
        limits=ResourceLimits(
            cpu_seconds=None, memory_mb=None, max_processes=None, max_file_mb=None
        ),
        timeout_seconds=0.5,
    )
    assert result.timed_out is True  # 没有被 rlimit 杀, 是外层超时杀的
    assert result.exit_code != 0


@posix_only
async def test_run_script_process_quota_blocks_fork_bomb(tmp_path: Path) -> None:
    """进程数配额: 并发 fork 远超配额时被拦截 (EAGAIN), 存量 fork 不受影响。

    RLIMIT_NPROC 按真实 UID 计数, 沙箱语义 = 启动时统计的 UID 进程数 + max_processes 配额。
    宿主机进程数自身有波动 (±数十), 故用 64 个并发 fork + 宽容阈值:
    配额 2 时正常应只放出个位数, 阈值放宽到 32 以吸收宿主噪声, 仍能证明限额生效。
    """
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "bomb.py": (
                "import subprocess, sys\n"
                "spawned = 0\n"
                "procs = []\n"
                "for _ in range(64):\n"
                "    try:\n"
                "        procs.append(subprocess.Popen(\n"
                "            [sys.executable, '-c', 'import time; time.sleep(5)']))\n"
                "        spawned += 1\n"
                "    except OSError:\n"
                "        pass\n"
                "print('spawned', spawned)\n"
                "for p in procs:\n"
                "    p.wait()\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(
        package,
        "scripts/bomb.py",
        limits=ResourceLimits(max_processes=2),
        timeout_seconds=60,
    )
    assert result.exit_code == 0
    assert result.timed_out is False
    spawned = int(result.stdout.split()[-1])
    assert spawned <= 32  # 64 个并发 fork 至少一半被配额拦截


@posix_only
async def test_run_script_default_limits_do_not_break_normal_script(tmp_path: Path) -> None:
    """默认限额 (CPU 10s / AS 512MB / NPROC 64 / FSIZE 64MB) 对正常脚本零影响。"""
    write_skill(
        tmp_path,
        "demo",
        scripts={
            "normal.py": (
                "import json\n"
                "data = [i * i for i in range(10000)]\n"
                "print(json.dumps({'sum': sum(data), 'len': len(data)}))\n"
            )
        },
    )
    package = scan_skills_dir(tmp_path)[0]

    result = await run_script(package, "scripts/normal.py")
    assert result.exit_code == 0
    assert '"len": 10000' in result.stdout
    assert result.timed_out is False
