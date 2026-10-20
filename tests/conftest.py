"""测试进程的隔离边界：禁止读真实 dotenv / 出站网络 / 默认持久库。"""

import os
import socket
import tempfile
from pathlib import Path

import httpx
import pytest

from lubanagent.config import Settings, get_settings

_CONFIG_ENV_KEYS = {
    (field.validation_alias if isinstance(field.validation_alias, str) else name).upper()
    for name, field in Settings.model_fields.items()
}
_CONFIG_ENV_KEYS.update({
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LANGCHAIN_API_KEY",
    "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPUAI_API_KEY",
})


def _clear_sensitive_env(environ):
    for key in tuple(environ):
        if key.upper() in _CONFIG_ENV_KEYS or key.upper().endswith(
            ("_API_KEY", "_SECRET_KEY", "_TOKEN", "_PASSWORD", "_CREDENTIALS")
        ):
            environ.pop(key, None)


def pytest_configure(config):
    """测试模块收集前清除宿主配置；pytest 退出时恢复原进程环境。"""
    config._luban_original_env = dict(os.environ)
    config._luban_tmpdir = tempfile.TemporaryDirectory(prefix="luban-test-")
    config._luban_settings_config = Settings.model_config
    config._luban_settings_load = Settings.__dict__["load"]
    config._luban_socket_connect = socket.socket.connect
    config._luban_socket_connect_ex = socket.socket.connect_ex
    config._luban_socket_sendto = socket.socket.sendto
    config._luban_socket_sendmsg = getattr(socket.socket, "sendmsg", None)
    config._luban_getaddrinfo = socket.getaddrinfo
    config._luban_httpx_send = httpx.Client.send
    config._luban_httpx_async_send = httpx.AsyncClient.send
    Settings.model_config = {**Settings.model_config, "env_file": None}
    Settings.load = classmethod(lambda cls: cls(_env_file=None))

    def reject_socket(sock, address):
        raise OSError(f"测试收集阶段网络目标未授权: {address}")

    def reject_lookup(host, port, *args, **kwargs):
        raise OSError(f"测试收集阶段网络目标未授权: {host}:{port}")

    def reject_sendto(sock, data, *args):
        raise OSError("测试收集阶段 UDP 出站未授权")

    socket.socket.connect = reject_socket
    socket.socket.connect_ex = reject_socket
    socket.socket.sendto = reject_sendto
    if config._luban_socket_sendmsg is not None:
        socket.socket.sendmsg = reject_sendto
    socket.getaddrinfo = reject_lookup

    def reject_httpx(client, request, *args, **kwargs):
        raise OSError(f"测试收集阶段 HTTP 目标未授权: {request.url}")

    async def reject_async_httpx(client, request, *args, **kwargs):
        raise OSError(f"测试收集阶段 HTTP 目标未授权: {request.url}")

    httpx.Client.send = reject_httpx
    httpx.AsyncClient.send = reject_async_httpx
    _clear_sensitive_env(os.environ)
    os.environ.update(
        DATABASE_URL=f"sqlite+aiosqlite:///{Path(config._luban_tmpdir.name) / 'session.db'}",
        OBSERVABILITY_BACKEND="none",
        LUBAN_ENV="dev",
    )
    get_settings.cache_clear()


def pytest_unconfigure(config):
    if hasattr(config, "_luban_original_env"):
        Settings.model_config = config._luban_settings_config
        Settings.load = config._luban_settings_load
        socket.socket.connect = config._luban_socket_connect
        socket.socket.connect_ex = config._luban_socket_connect_ex
        socket.socket.sendto = config._luban_socket_sendto
        if config._luban_socket_sendmsg is not None:
            socket.socket.sendmsg = config._luban_socket_sendmsg
        socket.getaddrinfo = config._luban_getaddrinfo
        httpx.Client.send = config._luban_httpx_send
        httpx.AsyncClient.send = config._luban_httpx_async_send
        get_settings.cache_clear()
        os.environ.clear()
        os.environ.update(config._luban_original_env)
        config._luban_tmpdir.cleanup()


@pytest.fixture(autouse=True)
def isolated_test_process(monkeypatch, tmp_path, request):
    """每个测试显式指定临时库和无外部观测；配置契约测试自行构造临时 dotenv。"""
    config_contract = "tests/lubanagent/config/test_settings.py" in request.node.nodeid
    if config_contract:
        # 配置契约测试只在 tmp cwd 测试真实分层语义。
        monkeypatch.setattr(Settings, "model_config", request.config._luban_settings_config)
        monkeypatch.setattr(Settings, "load", request.config._luban_settings_load)
    else:
        # 非配置测试的 Settings 不得读宿主 dotenv。
        for key in tuple(os.environ):
            if key.upper() in _CONFIG_ENV_KEYS or key.upper().endswith(
                ("_API_KEY", "_SECRET_KEY", "_TOKEN", "_PASSWORD", "_CREDENTIALS")
            ):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/isolated.db")
        monkeypatch.setenv("OBSERVABILITY_BACKEND", "none")
        monkeypatch.setenv("LUBAN_ENV", "dev")
        monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
        monkeypatch.setattr(Settings, "load", classmethod(lambda cls: cls(_env_file=None)))

    original_connect = request.config._luban_socket_connect
    original_connect_ex = request.config._luban_socket_connect_ex
    original_getaddrinfo = request.config._luban_getaddrinfo
    original_sendto = request.config._luban_socket_sendto
    original_sendmsg = request.config._luban_socket_sendmsg
    original_httpx_send = request.config._luban_httpx_send
    original_httpx_async_send = request.config._luban_httpx_async_send
    allowed_ports: set[int] = set()
    network_attempts: list[tuple[str, int]] = []
    http_requests: list[tuple[str, str]] = []

    def check(address):
        if not isinstance(address, tuple) or len(address) < 2:
            return  # Unix sockets are local and have no remote host.
        host, port = address[:2]
        network_attempts.append((host, port))
        if host not in ("127.0.0.1", "::1") or port not in allowed_ports:
            raise OSError(f"测试网络目标未授权: {host}:{port}")

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in ("127.0.0.1", "::1"):
            raise OSError(f"测试网络目标未授权: {host}:{port}")
        return original_getaddrinfo(host, port, *args, **kwargs)

    def sendto(sock, data, *args):
        if args and isinstance(args[-1], tuple):
            check(args[-1])
        return original_sendto(sock, data, *args)

    def audit_request(http_request):
        http_requests.append((http_request.method, str(http_request.url)))
        if http_request.url.host not in ("test", "127.0.0.1", "::1") or (
            http_request.url.host != "test" and http_request.url.port not in allowed_ports
        ):
            raise OSError(f"测试 HTTP 目标未授权: {http_request.url}")

    def audited_httpx(client, http_request, *args, **kwargs):
        audit_request(http_request)
        return original_httpx_send(client, http_request, *args, **kwargs)

    async def audited_async_httpx(client, http_request, *args, **kwargs):
        audit_request(http_request)
        return await original_httpx_async_send(client, http_request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", audited_httpx)
    monkeypatch.setattr(httpx.AsyncClient, "send", audited_async_httpx)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    if original_sendmsg is not None:
        def sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
            if address is not None:
                check(address)
            return original_sendmsg(sock, buffers, ancdata, flags, address)

        monkeypatch.setattr(socket.socket, "sendmsg", sendmsg)
    get_settings.cache_clear()
    try:
        yield allowed_ports, network_attempts, http_requests
    finally:
        get_settings.cache_clear()



@pytest.fixture
def isolated_subprocess_env(tmp_path):
    """显式提供 CLI 子进程可用的合成配置，不把调用者的密钥/DB 传下去。"""
    safe = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "SYSTEMROOT", "PYTHONPATH")
        if key in os.environ
    }
    safe.update(
        DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/child.db",
        OBSERVABILITY_BACKEND="none",
        LUBAN_ENV="dev",
        BIZ_BASE_URL="http://unreachable.invalid:1",
    )
    return safe
