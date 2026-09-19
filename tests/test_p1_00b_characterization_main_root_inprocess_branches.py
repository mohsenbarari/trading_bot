"""Characterization tests for main.py:root static-directory branches in-process.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: API-OPS-GET-MAIN-ROOT-L614
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: api_ops_get_main_root_inprocess_static_dir_branches

This module characterizes the two source-backed branches of the async `root` handler
at `main.py:614`:
1. Present static_dir branch returning a FileResponse for index.html with canonical
   document headers.
2. Absent static_dir branch returning the plain health-message dictionary.

Explicit exclusions:
- subprocess execution and module reloads
- get_public_config, get_metrics, and serve_frontend handlers
- conditional route registration, SPA fallback, and stale-JS behavior
- API/docs probe behavior, authentication/routing middleware, and product mutation
- origin SUCCESS/policy/notification/retry evidence, inventory reconciliation,
  blocker closure, and Stage closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import tests.test_p1_00_characterization_network_isolation
from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()
tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()

from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse
import main


class _PresentStaticDirSentinel:
    """Sentinel for present static_dir where exists() is True and only index.html join is allowed."""

    def __init__(self, index_path: Path) -> None:
        self.index_path = index_path
        self.exists_calls = 0
        self.join_calls: list[str] = []

    def exists(self) -> bool:
        self.exists_calls += 1
        return True

    def __truediv__(self, other: object) -> Path:
        self.join_calls.append(str(other))
        if other != "index.html":
            raise AssertionError(f"Present sentinel only accepts join with 'index.html', received: {other!r}")
        return self.index_path


class _AbsentStaticDirSentinel:
    """Sentinel for absent static_dir where exists() is False and any join fails."""

    def __init__(self, index_path: Path | None = None) -> None:
        self.exists_calls = 0
        self.join_calls: list[str] = []

    def exists(self) -> bool:
        self.exists_calls += 1
        return False

    def __truediv__(self, other: object) -> Path:
        self.join_calls.append(str(other))
        raise AssertionError(f"Absent sentinel fails on any join; received __truediv__ with: {other!r}")


class MainRootInProcessBranchesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_freezes_present_and_absent_static_dir_branches_in_process(self) -> None:
        original_static_dir = main.static_dir
        sentinel_index_path = Path("sentinel_dist/index.html")

        # Present branch subcase
        present_sentinel = _PresentStaticDirSentinel(sentinel_index_path)
        denied_before_present = len(_FENCE["denied_connects"])
        redis_before_present = len(_FENCE["live_redis_constructs"])

        with patch.object(main, "static_dir", present_sentinel):
            present_result = await main.root()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before_present)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before_present)
        self.assertIs(main.static_dir, original_static_dir)

        self.assertIs(type(present_result), FileResponse)
        self.assertIsInstance(present_result, FileResponse)
        self.assertIs(present_result.path, sentinel_index_path)
        self.assertEqual(present_result.path, sentinel_index_path)
        self.assertEqual(present_result.status_code, 200)

        self.assertEqual(main.frontend_document_headers, {"Referrer-Policy": "no-referrer"})
        self.assertEqual(
            present_result.headers.get("Referrer-Policy"),
            main.frontend_document_headers["Referrer-Policy"],
        )
        self.assertEqual(present_result.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(present_sentinel.exists_calls, 1)
        self.assertEqual(present_sentinel.join_calls, ["index.html"])

        # Absent branch subcase
        absent_sentinel = _AbsentStaticDirSentinel()
        denied_before_absent = len(_FENCE["denied_connects"])
        redis_before_absent = len(_FENCE["live_redis_constructs"])

        with patch.object(main, "static_dir", absent_sentinel):
            absent_result = await main.root()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before_absent)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before_absent)
        self.assertIs(main.static_dir, original_static_dir)

        self.assertIs(type(absent_result), dict)
        self.assertEqual(absent_result, {"message": "Trading Bot API is running 🚀"})
        self.assertEqual(absent_sentinel.exists_calls, 1)
        self.assertEqual(len(absent_sentinel.join_calls), 0)


if __name__ == "__main__":
    unittest.main()
