"""pytest 共享夹具（BLUEPRINT 19.16 第一条、20.12 第一条）。

未标记 ``integration`` 的测试一律不允许访问网络：``socket.socket.connect``、
``socket.socket.connect_ex`` 与 ``socket.getaddrinfo`` 会抛出 RuntimeError。默认
命令行（``addopts``）也不运行标记了 ``integration`` 的测试。

另一个 autouse 夹具在每条测试期间删掉所有以 ``_API_KEY`` 结尾的环境变量
（BLUEPRINT 20.12）：测试不得依赖这台机器上恰好配置了哪个密钥。
"""
import os
import socket

import pytest

_BLOCKED_MESSAGE = "测试不允许访问网络；需要联网的测试必须标记 @pytest.mark.integration"


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """未标记 integration 的测试里，把三个联网入口换成一句报错。"""
    if request.node.get_closest_marker("integration") is not None:
        return

    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError(_BLOCKED_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def _no_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """每条测试期间移除所有 ``*_API_KEY`` 环境变量；测试结束由 monkeypatch 原样恢复。"""
    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)

