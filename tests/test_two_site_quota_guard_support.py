"""Reusable private boundary-guard support for AE03 tests."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
from typing import Any, Callable, Iterator
import pytest

import scripts.two_site_provider_broker as broker
import scripts.two_site_quota_continuation_terminal_storage as terminal_storage

TARGET_NAMES = ("submit", "submit_maintenance", "execute_pipeline", "publish")


class BoundaryTrap:
    """Private boundary trap fixture tracking the four canonical external boundaries."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch | None = None) -> None:
        self.monkeypatch = (
            monkeypatch if monkeypatch is not None else pytest.MonkeyPatch()
        )
        self.calls: dict[str, int] = {
            "submit": 0,
            "submit_maintenance": 0,
            "execute_pipeline": 0,
            "publish": 0,
        }
        self.traps: dict[str, Any] = {}
        self.originals: dict[str, Any] = {}
        self._install()

    def _trap(self, name: str) -> Callable[..., Any]:
        def _guarded(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            raise AssertionError(f"Boundary trap tripped: {name} was called")

        return _guarded

    def _install(self) -> None:
        for name in ("submit", "submit_maintenance", "execute_pipeline"):
            self.originals[name] = getattr(broker, name)
            fn = self._trap(name)
            self.traps[name] = fn
            self.monkeypatch.setattr(broker, name, fn)
        self.originals["publish"] = getattr(
            terminal_storage, "publish_terminal_continuation_record"
        )
        pub_fn = self._trap("publish")
        self.traps["publish"] = pub_fn
        self.monkeypatch.setattr(
            terminal_storage, "publish_terminal_continuation_record", pub_fn
        )

    def assert_active(self) -> None:
        for name in ("submit", "submit_maintenance", "execute_pipeline"):
            current = getattr(broker, name)
            if current is not self.traps[name]:
                raise AssertionError(f"Trap {name} identity mismatch")
            if self.calls[name] != 0:
                raise AssertionError(
                    f"Trap {name} was called {self.calls[name]} times"
                )
        current_pub = getattr(
            terminal_storage, "publish_terminal_continuation_record"
        )
        if current_pub is not self.traps["publish"]:
            raise AssertionError("Trap publish identity mismatch")
        if self.calls["publish"] != 0:
            raise AssertionError(
                f"Trap publish was called {self.calls['publish']} times"
            )

    def restore(self) -> None:
        self.monkeypatch.undo()
        for name in ("submit", "submit_maintenance", "execute_pipeline"):
            setattr(broker, name, self.originals[name])
        setattr(
            terminal_storage,
            "publish_terminal_continuation_record",
            self.originals["publish"],
        )


@contextmanager
def boundary_guards() -> Iterator[BoundaryTrap]:
    owner_mp = pytest.MonkeyPatch()
    trap = BoundaryTrap(owner_mp)
    try:
        trap.assert_active()
    except BaseException:
        trap.restore()
        raise

    try:
        yield trap
    finally:
        try:
            trap.assert_active()
        finally:
            trap.restore()


__all__ = ["BoundaryTrap", "boundary_guards", "TARGET_NAMES"]
