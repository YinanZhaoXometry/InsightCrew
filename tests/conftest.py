"""pytest 共享夹具（BLUEPRINT 19.16 第一条）。

未标记 ``integration`` 的测试一律不允许访问网络：``socket.socket.connect``
与 ``socket.getaddrinfo`` 会抛出 RuntimeError。默认命令行（``addopts``）也
不运行标记了 ``integration`` 的测试。
"""
import socket

import pytest

_BLOCKED_MESSAGE = "测试不允许访问网络；需要联网的测试必须标记 @pytest.mark.integration"


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """未标记 integration 的测试里，把两个联网入口换成一句报错。"""
    if request.node.get_closest_marker("integration") is not None:
        return

    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError(_BLOCKED_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
