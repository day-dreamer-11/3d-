"""启动器端口探测测试（不启动实际服务）。"""

from __future__ import annotations

import socket

import launcher


def test_port_in_use_detects_wildcard_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        sock.listen(16)
        port = sock.getsockname()[1]
        assert launcher._port_in_use(port) is True
        free = launcher._free_port(preferred=port, low=port, high=port + 20)
        assert free != port
        assert launcher._port_in_use(free) is False
