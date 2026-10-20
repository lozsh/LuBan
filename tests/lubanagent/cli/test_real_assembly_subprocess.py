"""H14 真实装配验收：独立子进程跑 CLI（migrate + init --fill-missing）+ 真实 app 装配。

与 in-process CliRunner 测试（test_main.py）互补：这里是完整解释器、真实 Settings
分层加载（环境变量 > .env）、真实 YAML 种子扫描的真实交付路径——证明"克隆后
migrate → init → create_app 三条命令可用"。POST runs 返回 SSE 流（D4-1），
以 run.completed 帧为验收断言。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _subprocess_env(db_url: str) -> dict[str, str]:
    """显式注入的隔离环境：DATABASE_URL 指向临时库，鉴权/观测关闭（不依赖真实 .env）。"""
    env = dict(os.environ)
    env["DATABASE_URL"] = db_url
    env["AUTH_ENABLED"] = "false"
    env["OBSERVABILITY_BACKEND"] = "none"
    return env


def _run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """独立子进程执行 CLI：python -c 'from lubanagent.cli.main import app; app()' <args>。"""
    return subprocess.run(
        [sys.executable, "-c", "from lubanagent.cli.main import app; app()", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
    )


def _run_assembly(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """独立子进程真实装配：app.main.create_app()（get_settings 走真实环境变量路径）。"""
    script = '''
import asyncio
import json


async def main():
    import app.main as user_main
    from httpx import ASGITransport, AsyncClient

    application = user_main.create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    ) as client:
        agents_resp = await client.get("/v1/agents")
        assert agents_resp.status_code == 200, (
            "GET /v1/agents %s: %s" % (agents_resp.status_code, agents_resp.text[:500])
        )
        keys = [a["key"] for a in agents_resp.json()]
        assert "customer-service-bot" in keys, keys

        # POST runs = SSE 事件流（D4-1）：以 run.completed 帧为真实 Run 验收
        run = await client.post(
            "/v1/agents/customer-service-bot/runs",
            json={"input": "你好", "session_id": "assembly-smoke"},
        )
        assert run.status_code == 200, "POST runs %s: %s" % (run.status_code, run.text[:500])
        assert "event: run.completed" in run.text, run.text[:800]
        assert "event: run.failed" not in run.text, run.text[:800]
        print(json.dumps({"agents": len(keys), "run": "completed"}))


asyncio.run(main())
'''
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
    )


async def test_real_assembly_subprocess_migrate_init_run(tmp_path) -> None:
    """独立进程：migrate → init --fill-missing（YAML 种子）→ 真实 app 装配后 SSE Run 完成。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}"
    env = _subprocess_env(db_url)

    migrated = _run_cli(env, "migrate")
    assert migrated.returncode == 0, migrated.stderr

    seeded = _run_cli(env, "init", "--fill-missing")
    assert seeded.returncode == 0, seeded.stderr

    assembled = _run_assembly(env)
    assert assembled.returncode == 0, assembled.stderr
    # 子进程 stdout 可能混入库级日志行：按 JSON 前缀定位结果行
    result_lines = [line for line in assembled.stdout.splitlines() if line.startswith('{"agents"')]
    assert result_lines, f"子进程无结果输出: stdout={assembled.stdout[-2000:]}"
    summary = json.loads(result_lines[-1])
    assert summary["agents"] > 0
    assert summary["run"] == "completed"
