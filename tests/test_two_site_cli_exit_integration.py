"""Hermetic integration tests for disposable CLI exit pin capture and verification."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import pathlib
import stat
import tempfile
import unittest
from typing import Any, Callable, Iterator

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
import tests.test_p1_00_characterization_network_isolation as fence

fence.install_p1_00_network_fence()

import scripts.two_site_cli_artifact as artifact_mod
from scripts.two_site_cli_artifact import (
    CliArtifactError,
    prepare_disposable_cli,
)
from scripts.two_site_cli_exit_pin import (
    CliExitPin,
    CliExitPinError,
)
import scripts.two_site_proposal_runner as runner_mod
from tests.test_two_site_cli_cleanup import _fault_scope
from tests.test_two_site_cli_exit_support import (
    assert_real_lock_contended,
    owned_cli_temporaries,
)
from tests.test_two_site_inode_fixture import hold_original_inode


@contextlib.contextmanager
def _pin_spy(
    *,
    capture_exc: BaseException | None = None,
    verify_exc: BaseException | None = None,
    on_verify: Callable[[CliExitPin], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Context manager to intercept and record CLI exit pin capture and verification calls."""
    orig_capture = artifact_mod._capture_cli_exit_pin
    orig_verify = artifact_mod._verify_cli_exit_pin
    info: dict[str, Any] = {
        "capture_calls": 0,
        "verify_calls": 0,
        "captured_pins": [],
        "verify_args": [],
    }

    def spy_capture(
        disposable: pathlib.Path,
        expected_sha256: str,
        expected_version: str | None = None,
    ) -> Any:
        info["capture_calls"] += 1
        if capture_exc is not None:
            raise capture_exc
        pin = orig_capture(disposable, expected_sha256, expected_version)
        info["captured_pins"].append(pin)
        return pin

    def spy_verify(pin: Any, version_probe: Any = None) -> Any:
        info["verify_calls"] += 1
        info["verify_args"].append((pin, version_probe))
        if on_verify is not None:
            on_verify(pin)
        if verify_exc is not None:
            raise verify_exc
        return orig_verify(pin, version_probe)

    artifact_mod._capture_cli_exit_pin = spy_capture
    artifact_mod._verify_cli_exit_pin = spy_verify
    try:
        yield info
    finally:
        artifact_mod._capture_cli_exit_pin = orig_capture
        artifact_mod._verify_cli_exit_pin = orig_verify


class TestTwoSiteCliExitIntegration(unittest.TestCase):
    """Hermetic integration suite for CLI exit pin verification, priority, and lock order."""

    def setUp(self) -> None:
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._fixture_dir.name).resolve()
        self.content = b"#!/bin/sh\necho 'cli-exit-integration-payload'\n"
        self.source_path = self.tmp_path / "sample_cli"
        self.source_path.write_bytes(self.content)
        os.chmod(self.source_path, 0o500)
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.init_mode = stat.S_IMODE(self.source_path.stat().st_mode)

    def tearDown(self) -> None:
        self._fixture_dir.cleanup()

    def test_successful_capture_and_verification_contract(self) -> None:
        with owned_cli_temporaries() as outer_path:
            with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                with _pin_spy() as info:
                    with prepare_disposable_cli(
                        self.source_path,
                        expected_sha256=self.digest,
                    ) as (disp_path, evidence):
                        self.assertTrue(disp_path.exists())
                        self.assertEqual(disp_path.read_bytes(), self.content)
                        self.assertEqual(evidence["sha256"], self.digest)
                        self.assertEqual(evidence["source_path"], str(self.source_path.resolve()))
                        self.assertEqual(info["capture_calls"], 1)
                        self.assertEqual(info["verify_calls"], 0)

                self.assertEqual(info["verify_calls"], 1)
                pin = info["captured_pins"][0]
                self.assertIsInstance(pin, CliExitPin)
                self.assertIsNone(pin.expected_version)
                self.assertEqual(pin.expected_sha256, self.digest)
                self.assertEqual(pin.disposable, paths[0])

                verify_pin, version_probe = info["verify_args"][0]
                self.assertIs(verify_pin, pin)
                self.assertIsNone(version_probe)

                self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(paths[0].exists())
                self.assertFalse(paths[0].parent.exists())

        self.assertFalse(outer_path.exists())
        self.assertEqual(self.source_path.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.source_path.stat().st_mode), self.init_mode)

    def test_capture_failure_before_yield_never_runs_body_or_verify(self) -> None:
        body_entered = False
        with owned_cli_temporaries() as outer_path:
            with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                with _pin_spy(capture_exc=CliExitPinError("simulated capture error")) as info:
                    with self.assertRaises(CliArtifactError) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            body_entered = True

                self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
                self.assertFalse(body_entered)
                self.assertEqual(info["capture_calls"], 1)
                self.assertEqual(info["verify_calls"], 0)
                self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(paths[0].parent.exists())
        self.assertFalse(outer_path.exists())

    def test_capture_cancellation_propagates_exact_and_cleans_up(self) -> None:
        cancels = [KeyboardInterrupt("capture ki"), SystemExit(140), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel=type(cancel_obj).__name__):
                body_entered = False
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                        with _pin_spy(capture_exc=cancel_obj) as info:
                            with self.assertRaises(type(cancel_obj)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    body_entered = True

                        self.assertIs(ctx.exception, cancel_obj)
                        self.assertFalse(body_entered)
                        self.assertEqual(info["capture_calls"], 1)
                        self.assertEqual(info["verify_calls"], 0)
                        self.assertEqual(records, ["unlink", "td_cleanup"])
                        self.assertFalse(paths[0].parent.exists())
                self.assertFalse(outer_path.exists())

    def test_pre_yield_capture_cancellation_wins_over_cleanup_faults(self) -> None:
        cancels = [KeyboardInterrupt("preyield ki"), SystemExit(141), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel=type(cancel_obj).__name__):
                body_entered = False
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(
                        source_name=self.source_path.name,
                        unlink_exc=SystemExit(150),
                        td_exc=OSError("td fail during pre-yield cancel"),
                    ) as (records, _, paths):
                        with _pin_spy(capture_exc=cancel_obj) as info:
                            with self.assertRaises(type(cancel_obj)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    body_entered = True

                        self.assertIs(ctx.exception, cancel_obj)
                        self.assertFalse(body_entered)
                        self.assertEqual(info["capture_calls"], 1)
                        self.assertEqual(info["verify_calls"], 0)
                        self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

    def test_real_fs_mutations_fail_on_exit(self) -> None:
        backup_paths: list[pathlib.Path] = []
        inode_records: list[dict[str, Any]] = []

        def mutate_content(disp: pathlib.Path) -> None:
            disp.write_bytes(b"tampered content")

        def mutate_size(disp: pathlib.Path) -> None:
            disp.write_bytes(self.content + b"additional")

        def mutate_chmod(disp: pathlib.Path) -> None:
            os.chmod(disp, 0o755)

        def mutate_file_inode(disp: pathlib.Path) -> None:
            with hold_original_inode(disp) as old_st:
                disp.unlink()
                disp.write_bytes(self.content)
                os.chmod(disp, 0o700)
                self.assertNotEqual(old_st.st_ino, disp.stat().st_ino)

        def mutate_parent_inode(disp: pathlib.Path) -> None:
            parent = disp.parent
            backup = parent.parent / (parent.name + "_old_ino")
            backup_paths.append(backup)
            old_ino = parent.stat().st_ino
            parent.rename(backup)
            parent.mkdir(mode=0o700)
            new_ino = parent.stat().st_ino
            inode_records.append({
                "backup": backup,
                "parent": parent,
                "old_ino": old_ino,
                "new_ino": new_ino,
            })
            new_disp = parent / disp.name
            new_disp.write_bytes(self.content)
            os.chmod(new_disp, 0o700)

        def mutate_missing(disp: pathlib.Path) -> None:
            disp.unlink()

        mutations = [
            ("content", mutate_content),
            ("size", mutate_size),
            ("chmod", mutate_chmod),
            ("file_inode", mutate_file_inode),
            ("parent_inode", mutate_parent_inode),
            ("missing", mutate_missing),
        ]

        for name, mutator in mutations:
            with self.subTest(mutation=name, path="normal"):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                        with _pin_spy() as info:
                            with self.assertRaises(CliArtifactError) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ) as (disp_path, _):
                                    mutator(disp_path)

                            self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
                            self.assertEqual(info["verify_calls"], 1)
                            self.assertEqual(records, ["unlink", "td_cleanup"])
                            self.assertFalse(paths[0].parent.exists())

                    if name == "parent_inode":
                        for rec in inode_records:
                            self.assertTrue(rec["backup"].resolve().is_relative_to(outer_path))
                            self.assertNotEqual(rec["old_ino"], rec["new_ino"])
                            self.assertEqual(rec["backup"].stat().st_ino, rec["old_ino"])
                            self.assertTrue(rec["backup"].exists())

                self.assertFalse(outer_path.exists())
                if name == "parent_inode":
                    for rec in inode_records:
                        self.assertFalse(rec["backup"].exists())
                        self.assertFalse(rec["parent"].exists())
                    inode_records.clear()

            with self.subTest(mutation=name, path="ordinary_error"):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                        with _pin_spy() as info:
                            with self.assertRaises(CliArtifactError) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ) as (disp_path, _):
                                    mutator(disp_path)
                                    raise RuntimeError("ordinary body error during mutation")

                            self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
                            self.assertEqual(info["verify_calls"], 1)
                            self.assertEqual(records, ["unlink", "td_cleanup"])
                            self.assertFalse(paths[0].parent.exists())

                    if name == "parent_inode":
                        for rec in inode_records:
                            self.assertTrue(rec["backup"].resolve().is_relative_to(outer_path))
                            self.assertNotEqual(rec["old_ino"], rec["new_ino"])
                            self.assertEqual(rec["backup"].stat().st_ino, rec["old_ino"])
                            self.assertTrue(rec["backup"].exists())

                self.assertFalse(outer_path.exists())
                if name == "parent_inode":
                    for rec in inode_records:
                        self.assertFalse(rec["backup"].exists())
                        self.assertFalse(rec["parent"].exists())
                    inode_records.clear()

    def test_stable_ordinary_error_propagates_exact_and_verifies(self) -> None:
        body_err = RuntimeError("stable ordinary error")
        with owned_cli_temporaries() as outer_path:
            with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                with _pin_spy() as info:
                    with self.assertRaises(RuntimeError) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            raise body_err

                self.assertIs(ctx.exception, body_err)
                self.assertEqual(info["verify_calls"], 1)
                self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(paths[0].parent.exists())
        self.assertFalse(outer_path.exists())

    def test_body_cancellations_preserve_exact_identity_on_stable_and_mutating(self) -> None:
        cancels = [KeyboardInterrupt("body cancel ki"), SystemExit(141), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel=type(cancel_obj).__name__, mode="stable"):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, _, _):
                        with _pin_spy() as info:
                            with self.assertRaises(type(cancel_obj)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    raise cancel_obj

                            self.assertIs(ctx.exception, cancel_obj)
                            self.assertEqual(info["verify_calls"], 1)
                            self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

            with self.subTest(cancel=type(cancel_obj).__name__, mode="mutating"):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, _, _):
                        with _pin_spy() as info:
                            with self.assertRaises(type(cancel_obj)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ) as (disp_path, _):
                                    disp_path.write_bytes(b"tampered payload before cancel")
                                    raise cancel_obj

                            self.assertIs(ctx.exception, cancel_obj)
                            self.assertEqual(info["verify_calls"], 1)
                            self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

    def test_verification_ordinary_failure_converts_to_fixed_label(self) -> None:
        with owned_cli_temporaries() as outer_path:
            with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
                with _pin_spy(verify_exc=CliExitPinError("injected verify error")) as info:
                    with self.assertRaises(CliArtifactError) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            pass

                self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
                self.assertEqual(info["verify_calls"], 1)
                self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(paths[0].parent.exists())
        self.assertFalse(outer_path.exists())

    def test_verification_cancellations_win_over_later_cleanup_faults(self) -> None:
        cancels = [KeyboardInterrupt("verify ki"), SystemExit(142), GeneratorExit()]
        for v_cancel in cancels:
            with self.subTest(cancel=type(v_cancel).__name__):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(
                        source_name=self.source_path.name,
                        unlink_exc=SystemExit(143),
                        td_exc=OSError("td failed"),
                    ) as (records, _, _):
                        with _pin_spy(verify_exc=v_cancel) as info:
                            with self.assertRaises(type(v_cancel)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    pass

                        self.assertIs(ctx.exception, v_cancel)
                        self.assertEqual(info["verify_calls"], 1)
                        self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

    def test_body_cancellations_override_verification_and_cleanup_faults(self) -> None:
        cancels = [KeyboardInterrupt("body cancel"), SystemExit(144), GeneratorExit()]
        for b_cancel in cancels:
            with self.subTest(cancel=type(b_cancel).__name__):
                verify_cancel = KeyboardInterrupt("secondary verify cancel")
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(
                        source_name=self.source_path.name,
                        unlink_exc=SystemExit(145),
                        td_exc=OSError("td cancel"),
                    ) as (records, _, _):
                        with _pin_spy(verify_exc=verify_cancel) as info:
                            with self.assertRaises(type(b_cancel)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    raise b_cancel

                        self.assertIs(ctx.exception, b_cancel)
                        self.assertEqual(info["verify_calls"], 1)
                        self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

    def test_unlink_cancellations_win_over_td_cleanup_faults(self) -> None:
        cancels = [KeyboardInterrupt("unlink ki"), SystemExit(146), GeneratorExit()]
        for u_cancel in cancels:
            with self.subTest(cancel=type(u_cancel).__name__):
                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(
                        source_name=self.source_path.name,
                        unlink_exc=u_cancel,
                        td_exc=OSError("td cleanup fail"),
                    ) as (records, _, _):
                        with _pin_spy() as info:
                            with self.assertRaises(type(u_cancel)) as ctx:
                                with prepare_disposable_cli(
                                    self.source_path,
                                    expected_sha256=self.digest,
                                ):
                                    pass

                        self.assertIs(ctx.exception, u_cancel)
                        self.assertEqual(info["verify_calls"], 1)
                        self.assertEqual(records, ["unlink", "td_cleanup"])
                self.assertFalse(outer_path.exists())

    def test_ordinary_verification_and_cleanup_fault_keeps_cleanup_label(self) -> None:
        with owned_cli_temporaries() as outer_path:
            with _fault_scope(
                source_name=self.source_path.name,
                unlink_exc=OSError("unlink failed"),
            ) as (records, _, _):
                with _pin_spy(verify_exc=CliExitPinError("verify failed")) as info:
                    with self.assertRaises(CliArtifactError) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            pass

                self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
                self.assertEqual(info["verify_calls"], 1)
                self.assertEqual(records, ["unlink", "td_cleanup"])
        self.assertFalse(outer_path.exists())

    def test_proposal_cli_context_lock_order_and_reacquisition(self) -> None:
        scenarios = [
            ("normal", None, False),
            ("mutating_ordinary", RuntimeError("runner body error"), True),
            ("cancellation", KeyboardInterrupt("runner body cancel"), False),
        ]

        for name, err, mutate in scenarios:
            with self.subTest(scenario=name):
                lock_root = self.tmp_path / f"runner_root_{name}"
                lock_root.mkdir(parents=True, exist_ok=True)
                lock_file = lock_root / ".local/two-site-refactor/proposals/.runner.lock"

                lock_checks: list[str] = []
                orig_runner_release = runner_mod.release_proposal_lock
                release_called = False
                both_cleaned_before_release = False

                with owned_cli_temporaries() as outer_path:
                    with _fault_scope(source_name=self.source_path.name) as (records, created_tds, paths):
                        orig_scope_unlink = os.unlink

                        def lock_checking_unlink(path: Any, *args: Any, **kwargs: Any) -> Any:
                            p_str = os.fspath(path)
                            if paths and p_str == os.fspath(paths[0]):
                                assert_real_lock_contended(lock_file)
                                lock_checks.append("unlink")
                            return orig_scope_unlink(path, *args, **kwargs)

                        def on_verify_cb(_pin: Any) -> None:
                            assert_real_lock_contended(lock_file)
                            lock_checks.append("verify")
                            if created_tds:
                                td = created_tds[0]
                                scope_td_clean = td.cleanup

                                def lock_checking_td_cleanup() -> None:
                                    assert_real_lock_contended(lock_file)
                                    lock_checks.append("td_cleanup")
                                    scope_td_clean()

                                td.cleanup = lock_checking_td_cleanup

                        def tracking_release(descriptor: int) -> None:
                            nonlocal release_called, both_cleaned_before_release
                            both_cleaned_before_release = (records == ["unlink", "td_cleanup"])
                            release_called = True
                            orig_runner_release(descriptor)

                        os.unlink = lock_checking_unlink
                        runner_mod.release_proposal_lock = tracking_release
                        try:
                            with _pin_spy(on_verify=on_verify_cb) as info:
                                if err is None:
                                    with runner_mod.proposal_cli_context(lock_root, cli=self.source_path) as (local, disp, ev):
                                        self.assertTrue(disp.exists())
                                        self.assertEqual(ev["sha256"], self.digest)
                                elif mutate:
                                    with self.assertRaises(CliArtifactError) as ctx:
                                        with runner_mod.proposal_cli_context(lock_root, cli=self.source_path) as (_, disp, _):
                                            disp.write_bytes(b"mutated runner cli")
                                            raise err
                                    self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
                                else:
                                    with self.assertRaises(type(err)) as ctx:
                                        with runner_mod.proposal_cli_context(lock_root, cli=self.source_path):
                                            raise err
                                    self.assertIs(ctx.exception, err)
                        finally:
                            os.unlink = orig_scope_unlink
                            runner_mod.release_proposal_lock = orig_runner_release

                        self.assertEqual(info["verify_calls"], 1)
                        self.assertEqual(lock_checks, ["verify", "unlink", "td_cleanup"])
                        self.assertTrue(release_called)
                        self.assertTrue(both_cleaned_before_release)

                self.assertFalse(outer_path.exists())

                desc, _ = runner_mod.acquire_proposal_lock(lock_root)
                runner_mod.release_proposal_lock(desc)

    def test_parent_backup_resource_leak_regression(self) -> None:
        """Regression test ensuring parent mutation backup is strictly confined to owned outer."""
        with owned_cli_temporaries() as outer_path:
            parent = outer_path / "disposable_cli_test"
            parent.mkdir(mode=0o700)
            disp = parent / "cli"
            disp.write_bytes(self.content)

            backup = parent.parent / (parent.name + "_old_ino")
            self.assertTrue(backup.resolve().is_relative_to(outer_path))
            parent.rename(backup)
            old_ino = backup.stat().st_ino

            parent.mkdir(mode=0o700)
            new_ino = parent.stat().st_ino
            self.assertNotEqual(old_ino, new_ino)
            self.assertEqual(backup.stat().st_ino, old_ino)
            self.assertTrue(backup.exists())

        self.assertFalse(backup.exists())
        self.assertFalse(parent.exists())
        self.assertFalse(outer_path.exists())

        outside_backup = self.tmp_path / "leaked_backup"
        outside_backup.mkdir()
        with owned_cli_temporaries() as another_outer:
            pass
        self.assertTrue(outside_backup.exists())
        outside_backup.rmdir()


if __name__ == "__main__":
    unittest.main()
