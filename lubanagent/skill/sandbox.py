"""Skill 受控执行沙箱: scripts/ 内脚本的超时强制终止、环境变量白名单、资源限额、输出捕获截断。

加固边界（如实声明, 不假装隔离）:

已加固:
- 环境变量白名单: 子进程不再继承宿主完整环境, 只透传运行必需项
  (PATH/HOME/TMPDIR/LANG/LC_*/Windows 系统变量), 宿主密钥 (如 *_API_KEY) 默认不可见;
  白名单可经 ``env_whitelist`` 参数配置, 调用方可经 ``extra_env`` 显式注入额外变量。
- 资源限额 (POSIX): 经 preexec_fn + resource.setrlimit 限制 CPU 时间 / 地址空间 (AS) /
  进程数 (NPROC) / 单文件写入大小 (FSIZE)。非 POSIX 平台 (Windows) 整组跳过并记日志,
  不做假隔离; macOS 内核对 RLIMIT_AS 一律返回 EINVAL, 内存限额在该平台同样跳过并记
  warning (运行期探测, 不假装生效)。RLIMIT_NPROC 按真实 UID 计数全部进程 (非仅本进程树),
  故 ``max_processes`` 语义为 "在当前 UID 已有进程数之上允许新增的配额"——
  限额 = 启动时统计的 UID 进程数 + 配额, 既防 fork 炸弹又零影响存量脚本。
- 超时杀进程组 (timed_out=True)、路径逃逸拒绝 (脚本必须在包 scripts/ 内)、输出截断。

未隔离 (剩余风险面, 部署期由容器/网关兜底):
- **网络仍可达**: 进程级网络隔离需要 iptables/netns 等操作系统级沙箱 (通常要 root),
  本模块不假装隔离; ``SKILL_NET_WHITELIST`` env 仅是传入子进程的白名单标记,
  由脚本/网关侧约定遵守, 沙箱本身不拦截任何网络请求。
  ⚠️ 未来开放非受信 Skill 包 (第三方上传) 前, 必须先补 OS 级网络隔离 (容器/网络命名空间),
  否则恶意脚本可读取内网、外传数据——白名单 env 挡不住。
- 文件系统仍可读写宿主可达路径 (仅有 scripts/ 路径校验防逃逸, 无 chroot/只读挂载)。
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:  # POSIX only: Windows 等平台无 resource 模块, 限额整组跳过 (见模块 docstring)
    import resource
except ImportError:  # pragma: no cover - 非 POSIX 平台
    resource = None  # type: ignore[assignment]

from lubanagent.core import ErrorCode, LubanError

from .loader import SkillPackage

logger = logging.getLogger(__name__)

_WHITELIST_ENV = "SKILL_NET_WHITELIST"

#: 默认环境变量白名单: 精确名匹配, 尾缀 ``*`` 表示前缀匹配 (如 ``LC_*``)。
#: 取证范围: Python/sh 脚本启动与常见库仅依赖以下运行必需项;
#: 宿主密钥类变量 (*_API_KEY / TOKEN 等) 一律不在默认白名单内。
DEFAULT_ENV_WHITELIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",  # Windows 临时目录
    "LANG",
    "LC_*",  # 所有 locale 类别 (LC_CTYPE/LC_ALL/...)
    # Windows 子进程运行必需 (POSIX 上不存在, 自动跳过)
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
)


@dataclass(frozen=True)
class ResourceLimits:
    """子进程资源限额 (POSIX preexec_fn + setrlimit); ``None`` 表示不限制该项。

    全字段置 ``None`` 即整体关闭限额 (等价于非 POSIX 平台行为)。
    """

    cpu_seconds: int | None = 10  # RLIMIT_CPU: CPU 时间上限, 超限收 SIGXCPU
    memory_mb: int | None = 512  # RLIMIT_AS: 地址空间上限 (macOS 内核不支持, 自动跳过)
    max_processes: int | None = 64  # RLIMIT_NPROC: 在当前 UID 进程数之上允许新增的进程配额
    max_file_mb: int | None = 64  # RLIMIT_FSIZE: 单文件写入上限, 超限收 SIGXFSZ


@dataclass(frozen=True)
class ScriptResult:
    """一次脚本执行的规范化结果; timed_out=True 表示硬超时触发且进程组已被杀。"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool


def _resolve_script(package: SkillPackage, rel_path: str) -> Path:
    """脚本必须落在包 scripts/ 子目录内 (resolve 后校验, 防路径逃逸)。"""
    base = Path(package.path).resolve()
    resolved = (base / rel_path).resolve()
    scripts_root = (base / "scripts").resolve()
    if not resolved.is_relative_to(base) or not resolved.is_relative_to(scripts_root):
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"脚本路径不被允许 (必须在包 scripts/ 内): {rel_path}",
            details={"package": package.meta.name, "rel_path": rel_path},
        )
    if not resolved.is_file():
        raise LubanError(
            ErrorCode.VALIDATION_ERROR,
            f"脚本不存在: {rel_path}",
            details={"package": package.meta.name, "rel_path": rel_path},
        )
    return resolved


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars]


async def _drain_output(reader: asyncio.StreamReader, byte_limit: int) -> bytes:
    """持续排空管道，只保留 UTF-8 字符截断所需的前缀字节。"""
    kept = bytearray()
    while chunk := await reader.read(8192):
        remaining = byte_limit - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
    return bytes(kept)


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        try:
            # start_new_session=True: pgid 等于启动时的 pid；即使组长已退出也要杀其后代。
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.returncode is None:  # pragma: no cover - Windows
        proc.kill()


async def _finish_after_kill(
    tasks: Sequence[asyncio.Task], proc: asyncio.subprocess.Process
) -> None:
    """杀组后有界等待管道 EOF 和进程回收；异常时也回收 pump task。"""
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1.0)
    except (TimeoutError, asyncio.CancelledError):
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except TimeoutError:
                logger.warning("sandbox: SIGKILL 后进程回收超时 (pid=%s)", proc.pid)


def _env_allowed(name: str, whitelist: Sequence[str]) -> bool:
    """白名单匹配: 精确名相等, 或尾缀 ``*`` 的前缀匹配 (大小写敏感, 与 POSIX env 语义一致)。"""
    for pattern in whitelist:
        if pattern == name:
            return True
        if pattern.endswith("*") and name.startswith(pattern[:-1]):
            return True
    return False


def _build_child_env(
    whitelist: Sequence[str] | None,
    extra_env: Mapping[str, str] | None,
    network_whitelist: list[str] | None,
) -> dict[str, str]:
    """按白名单从宿主环境筛选子进程 env, 叠加显式注入项与网络白名单标记。

    - 宿主密钥 (白名单外的一切变量, 如 *_API_KEY) 默认不可见;
    - ``extra_env`` 是调用方的显式授权, 原样透传 (含覆盖白名单同名变量);
    - ``SKILL_NET_WHITELIST`` 为沙箱标记, 始终设置 (脚本侧约定读取)。
    """
    patterns = tuple(whitelist) if whitelist is not None else DEFAULT_ENV_WHITELIST
    env = {k: v for k, v in os.environ.items() if _env_allowed(k, patterns)}
    if extra_env:
        env.update(dict(extra_env))
    env[_WHITELIST_ENV] = ",".join(network_whitelist or [])
    return env


@lru_cache(maxsize=1)
def _rlimit_as_supported() -> bool:
    """探测内核是否接受 setrlimit(RLIMIT_AS); macOS 全系返回 EINVAL (无法限制地址空间)。

    只瞬态下探 soft 限额并立即还原 (不动 hard, 不可逆), 对宿主进程无副作用。
    探测失败即如实视为不支持——调用方跳过内存限额并记 warning, 不假装隔离。
    """
    if resource is None:
        return False
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        target = 256 * 1024 * 1024 if soft == resource.RLIM_INFINITY else soft
        resource.setrlimit(resource.RLIMIT_AS, (target, hard))
        if target != soft:  # 还原
            resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    except (ValueError, OSError):
        return False
    return True


def _uid_process_count() -> int | None:
    """统计当前真实 UID 的进程数 (RLIMIT_NPROC 按 UID 计数, 需以此为基线); None = 无法统计。

    Linux 走 /proc 目录属主统计; macOS/BSD 退回 ``ps`` 计数。
    """
    uid = os.getuid()
    if Path("/proc").is_dir():  # Linux
        try:
            return sum(
                1
                for entry in os.scandir("/proc")
                if entry.name.isdigit() and entry.stat(follow_symlinks=False).st_uid == uid
            )
        except OSError:
            return None
    try:  # macOS/BSD 兜底
        proc = subprocess.run(
            ["ps", "-u", str(uid), "-o", "pid="],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return len(proc.stdout.split())
    except (OSError, subprocess.SubprocessError):
        return None


def _make_preexec_fn(
    cpu_seconds: int | None,
    as_bytes: int | None,
    nproc: int | None,
    fsize: int | None,
):
    """构造子进程 preexec 闭包: fork 后、exec 前逐项 setrlimit。

    仅调用 resource.setrlimit (纯 syscall, 无锁/无信号处理), 满足 preexec_fn 的
    async-signal-safe 要求; 各参数为 None 表示该项不限制。全 None 时调用方不会构造。
    """

    def _apply() -> None:  # 在子进程内执行, 只准做 syscall 级操作
        if cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if as_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        if nproc is not None:
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        if fsize is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))

    return _apply


def _limits_active(limits: ResourceLimits) -> bool:
    return any(
        v is not None
        for v in (limits.cpu_seconds, limits.memory_mb, limits.max_processes, limits.max_file_mb)
    )


async def run_script(
    package: SkillPackage,
    rel_path: str,
    *,
    args: list[str] | None = None,
    timeout_seconds: float = 30.0,
    network_whitelist: list[str] | None = None,
    max_output_chars: int = 10_000,
    env_whitelist: Sequence[str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    limits: ResourceLimits | None = None,
) -> ScriptResult:
    """执行包 scripts/ 内脚本: .py→sys.executable, .sh→/bin/sh, 其他→直接执行。

    沙箱行为:
    - env 白名单: 子进程只见白名单内运行必需变量 (默认 ``DEFAULT_ENV_WHITELIST``,
      可传 ``env_whitelist`` 覆盖); ``extra_env`` 显式注入的变量原样透传; 宿主密钥默认不可见。
      网络白名单经 ``SKILL_NET_WHITELIST`` env 传入子进程 (仅标记, 见模块 docstring)。
    - 资源限额: POSIX 上按 ``limits`` (默认 ``ResourceLimits()``) 限 CPU/AS/NPROC/FSIZE;
      非 POSIX 平台与 macOS (内存项) 跳过并记 warning, 不做假隔离;
      传全 None 的 ``ResourceLimits`` 可关闭。
    - 超时杀进程组 (timed_out=True); stdout/stderr 捕获并按 max_output_chars 截断。
    """
    script = _resolve_script(package, rel_path)
    suffix = script.suffix.lower()
    if suffix == ".py":
        cmd = [sys.executable, str(script)]
    elif suffix == ".sh":
        cmd = ["/bin/sh", str(script)]
    else:
        cmd = [str(script)]

    env = _build_child_env(env_whitelist, extra_env, network_whitelist)

    effective_limits = limits if limits is not None else ResourceLimits()
    preexec_fn = None
    if _limits_active(effective_limits):
        if resource is None:
            # 如实声明: 非 POSIX 平台无 rlimit, 跳过限额而不是假装隔离
            logger.warning(
                "sandbox: 当前平台不支持 POSIX resource 限额, 已跳过 "
                "(CPU/内存/进程数/文件大小未受限)"
            )
        else:
            as_bytes = None
            if effective_limits.memory_mb is not None:
                if _rlimit_as_supported():
                    as_bytes = effective_limits.memory_mb * 1024 * 1024
                else:
                    # 如实声明: macOS 等内核拒绝 RLIMIT_AS, 内存限额无法生效
                    logger.warning(
                        "sandbox: 本平台内核不接受 RLIMIT_AS (macOS 全系拒绝), "
                        "内存限额已跳过; CPU/进程数/文件大小限额不受影响"
                    )
            nproc = None
            if effective_limits.max_processes is not None:
                # RLIMIT_NPROC 按真实 UID 计数全部进程 (非仅本进程树), 直接用绝对值
                # 会在多进程宿主上误杀存量 fork; 语义定为 "在当前 UID 进程数之上允许新增的配额"
                base = _uid_process_count()
                if base is None:
                    logger.warning(
                        "sandbox: 无法统计当前 UID 进程数, 进程数限额已跳过 "
                        "(CPU/内存/文件大小不受影响)"
                    )
                else:
                    nproc = base + effective_limits.max_processes
            fsize = (
                effective_limits.max_file_mb * 1024 * 1024
                if effective_limits.max_file_mb is not None
                else None
            )
            preexec_fn = _make_preexec_fn(effective_limits.cpu_seconds, as_bytes, nproc, fsize)

    start = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *cmd,
        *(args or []),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=script.parent.parent.as_posix(),  # 包目录 (script 必在 scripts/ 内)
        env=env,
        start_new_session=True,  # 独立进程组, 超时可整组杀
        preexec_fn=preexec_fn,
    ))
    try:
        async with asyncio.timeout_at(deadline):
            proc = await asyncio.shield(spawn)
    except BaseException as exc:
        # 启动已派发但调用者未取得句柄：完成启动后杀整组并回收。
        try:
            spawned = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            spawned = await spawn
        except Exception:
            raise
        _kill_process_group(spawned)
        await spawned.wait()
        if isinstance(exc, TimeoutError):
            return ScriptResult(
                exit_code=spawned.returncode if spawned.returncode is not None else -1,
                stdout="", stderr="", duration_ms=(time.perf_counter() - start) * 1000.0,
                timed_out=True,
            )
        raise

    # UTF-8 每字符至多 4 字节；分别截取前缀，越额仍要持续读取以免堵住子进程。
    byte_limit = max(0, max_output_chars) * 4
    stdout_task = asyncio.create_task(_drain_output(proc.stdout, byte_limit))
    stderr_task = asyncio.create_task(_drain_output(proc.stderr, byte_limit))
    wait_task = asyncio.create_task(proc.wait())
    tasks = (stdout_task, stderr_task, wait_task)
    timed_out = False
    try:
        try:
            await asyncio.wait_for(asyncio.shield(asyncio.gather(*tasks)),
                                   max(0, deadline - asyncio.get_running_loop().time()))
        except TimeoutError:
            timed_out = True
            _kill_process_group(proc)
            await _finish_after_kill(tasks, proc)
        stdout_b = stdout_task.result() if not stdout_task.cancelled() else b""
        stderr_b = stderr_task.result() if not stderr_task.cancelled() else b""
        duration_ms = (time.perf_counter() - start) * 1000.0
        return ScriptResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=_truncate(stdout_b.decode("utf-8", errors="replace"), max_output_chars),
            stderr=_truncate(stderr_b.decode("utf-8", errors="replace"), max_output_chars),
            duration_ms=duration_ms,
            timed_out=timed_out,
        )
    except BaseException:
        _kill_process_group(proc)
        await _finish_after_kill(tasks, proc)
        raise
