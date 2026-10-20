"""测试隔离闸门：只使用合成配置和测试专属网络目标。"""

import os
import socket

import pytest

from lubanagent.config import Settings, get_settings
from tests.demos.customer_service.conftest import mock_biz  # noqa: F401  # 本模块复用 fixture


def test_settings_never_load_parent_env(tmp_path, monkeypatch):
    """即使当前目录存在敏感 dotenv，测试读取仍是显式隔离配置。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=mysql+asyncmy://sentinel:never@unreachable.invalid/real\n"
        "BIZ_BASE_URL=http://unreachable.invalid:9100\n"
        "OBSERVABILITY_BACKEND=langfuse\n"
        "API_KEY=synthetic-never-send\n"
        "OPENAI_API_KEY=synthetic-model-never-send\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert str(tmp_path) in settings.database_url
    assert settings.biz_base_url is None
    assert settings.observability_backend == "none"
    assert Settings().biz_base_url is None
    assert settings.api_key is None
    assert "OPENAI_API_KEY" not in os.environ
    assert "DEEPSEEK_API_KEY" not in os.environ
    assert "DASHSCOPE_API_KEY" not in os.environ
    assert "ZHIPUAI_API_KEY" not in os.environ


def test_lowercase_host_settings_removed_during_collection():
    assert "biz_base_url" not in os.environ
    assert "deepseek_api_key" not in os.environ
    assert get_settings().biz_base_url is None


def test_subprocess_receives_only_synthetic_configuration(isolated_subprocess_env, tmp_path):
    import subprocess
    import sys

    assert set(isolated_subprocess_env) <= {
        "PATH", "HOME", "SYSTEMROOT", "PYTHONPATH", "DATABASE_URL",
        "OBSERVABILITY_BACKEND", "LUBAN_ENV", "BIZ_BASE_URL",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['DATABASE_URL'])"],
        cwd=tmp_path,
        env=isolated_subprocess_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"sqlite+aiosqlite:///{tmp_path}/child.db"


def test_business_client_preserves_httpx_environment_without_injection(monkeypatch):
    import httpx

    from app.tools.customer_service.order import _request

    observed = []
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        observed.append(kwargs)
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    monkeypatch.setattr("app.tools.customer_service.order.httpx.Client", fake_client)
    assert _request("GET", "http://test/orders/ORD-1001").status_code == 200
    assert observed == [{"transport": None, "trust_env": True}]


def test_rejected_http_request_is_audited(isolated_test_process):
    import httpx

    with pytest.raises(OSError, match="测试 HTTP 目标未授权"):
        httpx.get("https://unreachable.invalid/model")
    assert isolated_test_process[2] == [("GET", "https://unreachable.invalid/model")]


def test_network_guard_rejects_synthetic_external_target():
    with pytest.raises(OSError, match="测试网络目标未授权"):
        socket.create_connection(("127.0.0.1", 9100), timeout=0.1)
    with pytest.raises(OSError, match="测试网络目标未授权"):
        socket.create_connection(("unreachable.invalid", 443), timeout=0.1)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(OSError, match="测试网络目标未授权"):
            udp.sendto(b"nope", ("127.0.0.1", 9100))
        if hasattr(udp, "sendmsg"):
            with pytest.raises(OSError, match="测试网络目标未授权"):
                udp.sendmsg([b"nope"], [], 0, ("127.0.0.1", 9100))
    finally:
        udp.close()


@pytest.mark.usefixtures("mock_biz")
def test_mock_uses_owned_dynamic_port(request, isolated_test_process):
    server = request.getfixturevalue("mock_biz")
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_address[1] not in (9100, 8000, 5173)
    assert get_settings().biz_base_url == f"http://127.0.0.1:{server.server_port}"
    from app.tools.customer_service.order import query_order

    assert query_order("ORD-1001")["order_id"] == "ORD-1001"
    assert [(r.method, r.url.path) for r in server.requests] == [
        ("GET", "/orders/ORD-1001")
    ]
    assert isolated_test_process[1] == [("127.0.0.1", server.server_port)]
    assert isolated_test_process[2] == [
        ("GET", f"http://127.0.0.1:{server.server_port}/orders/ORD-1001")
    ]
