#!/usr/bin/env python3
"""冷启动端到端冒烟（零密钥）：migrate → init --fill-missing → 起服务 → SSE 对话客服 Agent。

守卫 PRD §8 冷启动硬指标："零密钥路径 5 分钟内完成克隆 → 起服务 → 与可用 Agent 对话"，
任何 PR 破坏装配 / 路由 / SSE / 种子链路即在本脚本失败。CI 在 `.github/workflows/ci.yml`
的 `cold-start` job 调用；本地可直接 `python scripts/cold_start_smoke.py`。

零密钥口径：FakeLLM 固定台词 + SQLite 临时库 + noop 观测，验证的是链路不是智力。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_DIST_INDEX = REPO_ROOT / "lubanagent" / "ui" / "dist" / "index.html"

RUN_URL = "/v1/agents/customer-service-bot/runs"
SSE_REQUEST_BODY = json.dumps({"input": "你好", "session_id": "cold-start-smoke"}).encode()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _smoke_env(db_path: Path) -> dict[str, str]:
    """显式注入的隔离环境：临时库 + 鉴权/观测关闭（不依赖真实 .env）。"""
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["AUTH_ENABLED"] = "false"
    env["OBSERVABILITY_BACKEND"] = "none"
    return env


def _run_cli(env: dict[str, str], *args: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """独立子进程执行 CLI，与 tests/lubanagent/cli/test_real_assembly_subprocess.py 同口径。"""
    return subprocess.run(
        [sys.executable, "-c", "from lubanagent.cli.main import app; app()", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=timeout,
    )


def _http(method: str, url: str, deadline: float, body: bytes | None = None) -> tuple[int, str]:
    """带死线的最小 HTTP 客户端（stdlib）。返回 (status, 全量文本)。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"总死线耗尽：{method} {url}")
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=max(1, int(remaining))) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def _wait_health(
    base_url: str,
    proc: subprocess.Popen[bytes],
    deadline: float,
    readiness_timeout: int = 90,
) -> None:
    """轮询 /health 至 200（独立就绪死线，与总死线取小）；子进程提前退出立即失败。"""
    ready_deadline = min(deadline, time.monotonic() + readiness_timeout)
    while time.monotonic() < ready_deadline:
        if proc.poll() is not None:
            raw = proc.stderr.read() if proc.stderr else b""
            stderr_tail = raw.decode(errors="replace")[-1500:]
            raise RuntimeError(
                f"uvicorn 提前退出（rc={proc.returncode}）\n--- stderr 尾部 ---\n{stderr_tail}"
            )
        try:
            status, _ = _http("GET", f"{base_url}/health", deadline)
            if status == 200:
                return
        except (urllib.error.URLError, ConnectionError, OSError, TimeoutError):
            pass
        time.sleep(0.5)
    if time.monotonic() >= deadline:
        raise TimeoutError("总死线耗尽，/health 未就绪")
    raise TimeoutError(f"{readiness_timeout}s 内 /health 未就绪")


def smoke(timeout: int, state: dict[str, object]) -> None:
    """执行全部冒烟步骤；失败抛异常，state 记录当前步骤供失败摘要输出。"""
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="luban-cold-start-") as tmp:
        db_path = Path(tmp) / "smoke.db"
        env = _smoke_env(db_path)

        state["step"] = "migrate"
        print("[1/6] luban migrate", flush=True)
        migrated = _run_cli(env, "migrate", timeout=60)
        assert migrated.returncode == 0, f"migrate 失败:\n{migrated.stderr[-1500:]}"

        state["step"] = "init"
        print("[2/6] luban init --fill-missing", flush=True)
        seeded = _run_cli(env, "init", "--fill-missing", timeout=120)
        assert seeded.returncode == 0, f"init 失败:\n{seeded.stderr[-1500:]}"

        port = _free_port()
        state["port"] = port
        base_url = f"http://127.0.0.1:{port}"
        state["step"] = "server"
        print(f"[3/6] uvicorn 起服务 {base_url}", flush=True)
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:create_app",
                "--factory", "--host", "127.0.0.1", "--port", str(port),
                "--log-level", "warning",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            state["step"] = "health"
            _wait_health(base_url, proc, deadline)
            print("[4/6] /health 就绪", flush=True)

            if UI_DIST_INDEX.is_file():
                state["step"] = "workbench"
                for path in ("/", "/chat"):
                    status, text = _http("GET", f"{base_url}{path}", deadline)
                    assert status == 200, f"GET {path} = {status}"
                    assert 'id="root"' in text, f"GET {path} 非 SPA 入口页"
                state["ui"] = "checked"
                print("[5/6] 工作台 / 与 /chat 可达", flush=True)
            else:
                print("[5/6] ui/dist 未构建，跳过工作台断言（frontend job 已守构建）", flush=True)

            state["step"] = "run"
            print(f"[6/6] POST {RUN_URL}（FakeLLM SSE Run）", flush=True)
            status, stream = _http("POST", f"{base_url}{RUN_URL}", deadline, body=SSE_REQUEST_BODY)
            assert status == 200, f"POST runs = {status}: {stream[:500]}"
            assert "event: run.started" in stream, f"缺 run.started:\n{stream[:800]}"
            assert "event: run.completed" in stream, f"缺 run.completed:\n{stream[:800]}"
            assert "event: run.failed" not in stream, f"出现 run.failed:\n{stream[:800]}"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="零密钥冷启动端到端冒烟")
    parser.add_argument("--timeout", type=int, default=240, help="整体死线秒数（默认 240）")
    args = parser.parse_args()
    state: dict[str, object] = {"step": "migrate", "ui": "skipped", "port": None}
    try:
        smoke(args.timeout, state)
    except subprocess.TimeoutExpired as exc:
        print(f"FAIL: 子进程超时（{exc}）", flush=True)
        print(json.dumps(state), flush=True)
        return 1
    except Exception as exc:  # 冒烟脚本：任何失败都要带证据非 0 退出
        print(f"FAIL: {exc}", flush=True)
        print(json.dumps(state), flush=True)
        return 1
    state["step"] = "ok"
    print(json.dumps(state), flush=True)
    return 0


if __name__ == "__main__":
    # SIGTERM 直通默认杀进程语义：冒烟脚本自身被 CI 取消时不留孤儿 uvicorn
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    sys.exit(main())
