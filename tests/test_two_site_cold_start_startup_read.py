#!/usr/bin/env python3
"""Hermetic unit tests for dormant cold-start startup read gate under AE03."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Iterator
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_cold_start_broker_quiescence import (
        ColdBrokerQuiescenceError,
        ColdBrokerQuiescencePostBodyError,
    )
except ImportError:
    from two_site_cold_start_broker_quiescence import (  # type: ignore[no-redef]
        ColdBrokerQuiescenceError,
        ColdBrokerQuiescencePostBodyError,
    )

try:
    from scripts.two_site_cold_start_pending_scan import PendingColdAdoptionError
except ImportError:
    from two_site_cold_start_pending_scan import PendingColdAdoptionError  # type: ignore[no-redef]

try:
    from scripts.two_site_cold_start_adoption_storage import ColdStartAdoptionStorageError
except ImportError:
    from two_site_cold_start_adoption_storage import ColdStartAdoptionStorageError  # type: ignore[no-redef]

try:
    from scripts.two_site_cold_start_startup_read import (
        ColdStartStartupReadError,
        require_terminal_cold_adoption_startup_locked,
    )
except ImportError:
    from two_site_cold_start_startup_read import (  # type: ignore[no-redef]
        ColdStartStartupReadError,
        require_terminal_cold_adoption_startup_locked,
    )

MOD = "scripts.two_site_cold_start_startup_read"


def _setup_tree(td: str, state: str = "IDLE", age: float = 1.0) -> tuple[Path, Path]:
    root = Path(td).resolve(strict=True)
    state_root = root / ".local" / "two-site-refactor" / "autopilot"
    broker_root = root / ".local" / "two-site-refactor" / "provider-broker"
    for p in (state_root, broker_root, broker_root / "requests", broker_root / "claimed"):
        p.mkdir(parents=True, mode=0o700)
        os.chmod(p, 0o700)
    lock_file = broker_root / "client.lock"
    lock_file.touch(mode=0o600)
    os.chmod(lock_file, 0o600)
    now = time.time()
    status_file = broker_root / "status.json"
    status_file.write_text(
        json.dumps({
            "schema_version": "1.0",
            "state": state,
            "pid": os.getpid(),
            "started_at": now - 100.0,
            "updated_at": now - age,
        }),
        encoding="utf-8",
    )
    os.chmod(status_file, 0o600)
    return root, state_root


class TestTwoSiteColdStartStartupRead(unittest.TestCase):
    """Unit test suite for require_terminal_cold_adoption_startup_locked."""

    def setUp(self) -> None:
        self.epoch = "rescope-" + "a" * 64
        self.repo = {"repo_id": "repo-1", "head": "0" * 40}
        self.record = {
            "schema_version": "1.0",
            "kind": "TERMINAL_EPOCH_COLD_ADOPTION",
            "old_epoch_basename": self.epoch,
            "new_repository": copy.deepcopy(self.repo),
        }

    def _call(
        self,
        root: Any,
        state_root: Any,
        repo: Any = None,
        epoch: Any = None,
    ) -> dict[str, Any]:
        return require_terminal_cold_adoption_startup_locked(
            root=root,
            state_root=state_root,
            new_repository=self.repo if repo is None else repo,
            expected_old_epoch_basename=self.epoch if epoch is None else epoch,
        )

    def test_01_success_and_exact_order_under_broker_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)
            events: list[str] = []

            @contextmanager
            def mock_lock(r: Path) -> Iterator[None]:
                self.assertEqual(r, root)
                events.append("lock_enter")
                yield
                events.append("lock_exit")

            def mock_pending(sr: Path) -> None:
                self.assertEqual(sr, state_root)
                events.append("scan_pending")

            def mock_read(sr: Path, nr: dict[str, Any]) -> dict[str, Any]:
                self.assertEqual(sr, state_root)
                self.assertEqual(nr, self.repo)
                events.append("read_record")
                return copy.deepcopy(self.record)

            with (
                patch(f"{MOD}.hold_idle_broker_client_lock_for_cold_adoption", side_effect=mock_lock),
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", side_effect=mock_pending),
                patch(f"{MOD}.read_terminal_adoption_record_locked", side_effect=mock_read),
            ):
                result = self._call(root, state_root)

            self.assertEqual(
                events,
                ["lock_enter", "scan_pending", "read_record", "scan_pending", "lock_exit"],
            )
            self.assertEqual(result, self.record)
            self.assertIsNot(result, self.record)

    def test_02_real_broker_gate_positive_and_negative(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td, state="IDLE")
            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=self.record),
            ):
                self.assertEqual(self._call(root, state_root), self.record)

        for st, age in (("RUNNING", 1.0), ("IDLE", 99999.0)):
            with tempfile.TemporaryDirectory() as td:
                root, state_root = _setup_tree(td, state=st, age=age)
                with (
                    patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                    patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=self.record),
                    self.assertRaises(ColdBrokerQuiescenceError),
                ):
                    self._call(root, state_root)

    def test_03_invalid_inputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)
            bad_cases = [
                ("not-a-path", state_root, self.repo, self.epoch),
                (Path("rel/path"), state_root, self.repo, self.epoch),
                (root / "missing", state_root, self.repo, self.epoch),
                (root, root / ".local" / "wrong", self.repo, self.epoch),
                (root, state_root, "not-a-dict", self.epoch),
                (root, state_root, self.repo, "bad-epoch"),
                (root, state_root, self.repo, "rescope-" + "g" * 64),
                (root, state_root, self.repo, 12345),
            ]
            for r, sr, repo, ep in bad_cases:
                with self.subTest(root=r, state_root=sr, repo=repo, epoch=ep):
                    with self.assertRaises(ColdStartStartupReadError):
                        self._call(r, sr, repo=repo, epoch=ep)

            sym_root = root / "sym_root"
            os.symlink(root, sym_root)
            with self.assertRaises(ColdStartStartupReadError):
                self._call(sym_root, sym_root / ".local" / "two-site-refactor" / "autopilot")

            os.chmod(state_root, 0o755)
            with self.assertRaises(ColdStartStartupReadError):
                self._call(root, state_root)
            os.chmod(state_root, 0o700)

    def test_04_stop_file_detected_pre_and_post(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)
            read_mock = MagicMock()

            stop_state = state_root / "STOP"
            stop_state.touch()
            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", read_mock),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)
            self.assertEqual(read_mock.call_count, 0)
            stop_state.unlink()

            os.symlink(state_root / "missing_target", stop_state)
            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", read_mock),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)
            self.assertEqual(read_mock.call_count, 0)
            stop_state.unlink()

            stop_root = root / "STOP"
            stop_root.touch()
            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", read_mock),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)
            self.assertEqual(read_mock.call_count, 0)
            stop_root.unlink()

            lock_exited = False

            @contextmanager
            def mock_lock(r: Path) -> Iterator[None]:
                nonlocal lock_exited
                try:
                    yield
                finally:
                    lock_exited = True

            def mock_read_creates_stop(sr: Path, nr: dict[str, Any]) -> dict[str, Any]:
                (sr / "STOP").touch()
                return copy.deepcopy(self.record)

            with (
                patch(f"{MOD}.hold_idle_broker_client_lock_for_cold_adoption", side_effect=mock_lock),
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", side_effect=mock_read_creates_stop),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)
            self.assertTrue(lock_exited)

    def test_stop_stat_error_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)
            real_lstat = os.lstat

            def denied_stop(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
                if str(path).endswith("/STOP"):
                    raise PermissionError("injected")
                return real_lstat(path, *args, **kwargs)

            with patch("os.lstat", side_effect=denied_stop):
                with self.assertRaises(ColdStartStartupReadError):
                    self._call(root, state_root)

    def test_05_record_missing_and_mismatch_and_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)

            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=None),
                self.assertRaises(ColdStartStartupReadError) as ctx,
            ):
                self._call(root, state_root)
            self.assertNotIn(self.epoch, str(ctx.exception))

            mismatched = copy.deepcopy(self.record)
            mismatched["old_epoch_basename"] = "rescope-" + "b" * 64
            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=mismatched),
                self.assertRaises(ColdStartStartupReadError) as ctx,
            ):
                self._call(root, state_root)
            self.assertNotIn("rescope-", str(ctx.exception))

            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=["not", "dict"]),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)

            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(
                    f"{MOD}.read_terminal_adoption_record_locked",
                    side_effect=ColdStartAdoptionStorageError("storage parse failed"),
                ),
                self.assertRaises(ColdStartStartupReadError),
            ):
                self._call(root, state_root)

    def test_06_pending_and_broker_errors_propagated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)

            with (
                patch(
                    f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked",
                    side_effect=PendingColdAdoptionError("quota-wait.json found"),
                ),
                self.assertRaises(PendingColdAdoptionError),
            ):
                self._call(root, state_root)

            with (
                patch(
                    f"{MOD}.hold_idle_broker_client_lock_for_cold_adoption",
                    side_effect=ColdBrokerQuiescenceError("Broker is RUNNING"),
                ),
                self.assertRaises(ColdBrokerQuiescenceError),
            ):
                self._call(root, state_root)

            @contextmanager
            def mock_post_body_fail(r: Path) -> Iterator[None]:
                yield
                raise ColdBrokerQuiescencePostBodyError("Post-body queue not empty")

            with (
                patch(f"{MOD}.hold_idle_broker_client_lock_for_cold_adoption", side_effect=mock_post_body_fail),
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=self.record),
                self.assertRaises(ColdBrokerQuiescencePostBodyError),
            ):
                self._call(root, state_root)

    def test_07_interrupts_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)

            with patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None):
                with (
                    patch(f"{MOD}.read_terminal_adoption_record_locked", side_effect=KeyboardInterrupt),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self._call(root, state_root)

                with (
                    patch(f"{MOD}.read_terminal_adoption_record_locked", side_effect=SystemExit(2)),
                    self.assertRaises(SystemExit) as ctx,
                ):
                    self._call(root, state_root)
                self.assertEqual(ctx.exception.code, 2)

    def test_08_zero_mutation_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = _setup_tree(td)
            repo_copy = copy.deepcopy(self.repo)
            state_entries_before = sorted(os.listdir(state_root))

            with (
                patch(f"{MOD}.assert_no_pending_cold_adoption_artifacts_locked", return_value=None),
                patch(f"{MOD}.read_terminal_adoption_record_locked", return_value=self.record),
            ):
                result = self._call(root, state_root, repo=repo_copy)

            self.assertEqual(repo_copy, self.repo)
            self.assertEqual(sorted(os.listdir(state_root)), state_entries_before)

            result["mutated_field"] = "taint"
            self.assertNotIn("mutated_field", self.record)


if __name__ == "__main__":
    unittest.main()
