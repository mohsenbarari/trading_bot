"""Suite-wide fail-closed network fence for P1-00 characterization tests."""

from __future__ import annotations

import socket
import unittest
from typing import Any

BLOCKED_REDIS_HOSTS = {"127.0.0.1", "localhost", "::1"}
BLOCKED_REDIS_PORTS = {6379}

_FENCE: dict[str, Any] = {
    "installed": False,
    "denied_connects": [],
    "live_redis_constructs": [],
    "real_socket_connect": socket.socket.connect,
    "real_create_connection": socket.create_connection,
}


def _blocked_address(address: object) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host = str(address[0])
    try:
        port = int(address[1])
    except (TypeError, ValueError):
        return False
    return port in BLOCKED_REDIS_PORTS and host in BLOCKED_REDIS_HOSTS


def _guarded_connect(self, address):
    if _blocked_address(address):
        _FENCE["denied_connects"].append(("socket.connect", address))
        raise OSError("P1-00 fail-closed: localhost Redis TCP denied")
    return _FENCE["real_socket_connect"](self, address)


def _guarded_create_connection(address, *args, **kwargs):
    if _blocked_address(address):
        _FENCE["denied_connects"].append(("socket.create_connection", address))
        raise OSError("P1-00 fail-closed: localhost Redis TCP denied")
    return _FENCE["real_create_connection"](address, *args, **kwargs)


class ForbiddenRedis:
    """Fail-closed stand-in for a live Redis client."""

    def __init__(self, *args, **kwargs):
        _FENCE["live_redis_constructs"].append({"args": args, "kwargs": kwargs})
        raise RuntimeError("P1-00 fail-closed: live Redis client construction denied")


def install_p1_00_network_fence() -> dict[str, Any]:
    if _FENCE["installed"]:
        return _FENCE
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
    try:
        import redis as redis_sync

        redis_sync.Redis = ForbiddenRedis
    except Exception:
        pass
    try:
        import redis.asyncio as redis_async

        redis_async.Redis = ForbiddenRedis
    except Exception:
        pass
    try:
        import core.utils as core_utils

        if hasattr(core_utils, "redis"):
            core_utils.redis.Redis = ForbiddenRedis
    except Exception:
        pass
    _FENCE["installed"] = True
    return _FENCE


install_p1_00_network_fence()


class P100NetworkIsolationCharacterizationTests(unittest.TestCase):
    def test_fence_denies_localhost_6379_tcp(self):
        self.assertIs(socket.socket.connect, _guarded_connect)
        before = len(_FENCE["denied_connects"])
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", 6379), timeout=0.2)
        fake_socket = object()
        with self.assertRaises(OSError):
            socket.socket.connect(fake_socket, ("localhost", 6379))
        self.assertGreaterEqual(len(_FENCE["denied_connects"]), before + 2)

    def test_live_redis_constructor_is_denied(self):
        import redis

        before = len(_FENCE["live_redis_constructs"])
        with self.assertRaisesRegex(RuntimeError, "live Redis client construction denied"):
            redis.Redis(host="127.0.0.1", port=6379)
        self.assertGreater(len(_FENCE["live_redis_constructs"]), before)


if __name__ == "__main__":
    unittest.main()
