"""Hermetic regression tests for all-path disposable CLI cleanup mechanism."""

from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import stat
import tempfile
import unittest
from typing import Any, Iterator

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
import tests.test_p1_00_characterization_network_isolation as fence

fence.install_p1_00_network_fence()

from scripts.two_site_cli_artifact import (
    CliArtifactError,
    prepare_disposable_cli,
)
import scripts.two_site_proposal_runner as runner_mod


@contextlib.contextmanager
def _fault_scope(
    *,
    source_name: str,
    unlink_exc: BaseException | None = None,
    td_exc: BaseException | None = None,
) -> Iterator[tuple[list[str], list[tempfile.TemporaryDirectory[str]], list[pathlib.Path]]]:
    """Scoped fault-injection harness saving original callables and patching TD instance only."""
    real_unlink = os.unlink
    real_td_init = tempfile.TemporaryDirectory
    records: list[str] = []
    created_tds: list[tempfile.TemporaryDirectory[str]] = []
    orig_cleanups: dict[tempfile.TemporaryDirectory[str], Any] = {}
    target_path_holder: list[pathlib.Path | None] = [None]
    captured_paths: list[pathlib.Path] = []

    def spy_unlink(path: Any, *args: Any, **kwargs: Any) -> Any:
        p_str = os.fspath(path)
        tgt = target_path_holder[0]
        if tgt is not None and p_str == os.fspath(tgt) and kwargs.get("dir_fd") is None:
            target_path_holder[0] = None
            records.append("unlink")
            if unlink_exc is not None:
                raise unlink_exc
        return real_unlink(path, *args, **kwargs)

    def hooked_td(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
        td = real_td_init(*args, **kwargs)
        created_tds.append(td)
        disp_path = pathlib.Path(td.name) / source_name
        target_path_holder[0] = disp_path
        captured_paths.append(disp_path)

        orig_clean = td.cleanup
        orig_cleanups[td] = orig_clean

        def spy_td_cleanup() -> None:
            records.append("td_cleanup")
            if td_exc is not None:
                raise td_exc
            orig_clean()

        td.cleanup = spy_td_cleanup
        return td

    os.unlink = spy_unlink
    tempfile.TemporaryDirectory = hooked_td  # type: ignore[misc]
    try:
        yield records, created_tds, captured_paths
    finally:
        os.unlink = real_unlink
        tempfile.TemporaryDirectory = real_td_init
        for td in created_tds:
            td.cleanup = orig_cleanups[td]
            if os.path.exists(td.name):
                try:
                    orig_cleanups[td]()
                except Exception:
                    pass


class TestTwoSiteCliCleanup(unittest.TestCase):
    """Hermetic test suite for all-path cleanup, cancellation, and lock ordering."""

    def setUp(self) -> None:
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._fixture_dir.name).resolve()
        self.content = b"#!/bin/sh\necho 'disposable-cleanup-test'\n"
        self.source_path = self.tmp_path / "sample_cli"
        self.source_path.write_bytes(self.content)
        os.chmod(self.source_path, 0o500)
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.init_mode = stat.S_IMODE(self.source_path.stat().st_mode)

    def tearDown(self) -> None:
        self._fixture_dir.cleanup()

    def test_clean_success_and_unchanged_source_and_evidence(self) -> None:
        with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
            with prepare_disposable_cli(
                self.source_path,
                expected_sha256=self.digest,
            ) as (disp_path, evidence):
                self.assertTrue(disp_path.exists())
                self.assertEqual(disp_path.read_bytes(), self.content)
                self.assertEqual(stat.S_IMODE(disp_path.stat().st_mode), 0o700)
                self.assertEqual(
                    evidence,
                    {"source_path": str(self.source_path.resolve()), "sha256": self.digest},
                )
            self.assertEqual(records, ["unlink", "td_cleanup"])
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[0].parent.exists())

        self.assertEqual(self.source_path.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.source_path.stat().st_mode), self.init_mode)

    def test_harmless_already_missing_disposable(self) -> None:
        with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(
                    self.source_path,
                    expected_sha256=self.digest,
                ) as (disp_path, _):
                    os.unlink(disp_path)
            self.assertEqual(str(ctx.exception), "Disposable CLI identity validation failed")
            self.assertEqual(records, ["unlink", "td_cleanup"])
            self.assertFalse(paths[0].parent.exists())

    def test_ordinary_body_error_unchanged_when_cleanup_succeeds(self) -> None:
        body_err = RuntimeError("ordinary body failure")
        with _fault_scope(source_name=self.source_path.name) as (records, _, paths):
            with self.assertRaises(RuntimeError) as ctx:
                with prepare_disposable_cli(
                    self.source_path,
                    expected_sha256=self.digest,
                ):
                    raise body_err
            self.assertIs(ctx.exception, body_err)
            self.assertEqual(records, ["unlink", "td_cleanup"])
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[0].parent.exists())

    def test_body_cancellation_preserves_exact_identity_and_runs_both_cleanups(self) -> None:
        cancels = [KeyboardInterrupt("body ki"), SystemExit(42), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel_type=type(cancel_obj).__name__):
                with _fault_scope(source_name=self.source_path.name) as (records, _, _):
                    with self.assertRaises(type(cancel_obj)) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            raise cancel_obj
                    self.assertIs(ctx.exception, cancel_obj)
                    self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_unlink_oserror_replaces_success_and_body_error(self) -> None:
        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=OSError("unlink failed"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    pass
            self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
            self.assertEqual(records, ["unlink", "td_cleanup"])

        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=OSError("unlink failed"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    raise RuntimeError("user body failure")
            self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
            self.assertNotIsInstance(ctx.exception, RuntimeError)
            self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_parent_cleanup_oserror_replaces_success_and_body_error(self) -> None:
        with _fault_scope(
            source_name=self.source_path.name,
            td_exc=OSError("td failed"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    pass
            self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
            self.assertEqual(records, ["unlink", "td_cleanup"])

        with _fault_scope(
            source_name=self.source_path.name,
            td_exc=OSError("td failed"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    raise RuntimeError("user body failure")
            self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
            self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_both_cleanup_failures_fixed_error_and_factual_retention(self) -> None:
        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=OSError("unlink failed"),
            td_exc=OSError("td failed"),
        ) as (records, _, paths):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    pass
            self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
            self.assertEqual(records, ["unlink", "td_cleanup"])
            self.assertTrue(paths[0].parent.exists())

    def test_first_unlink_cancel_preserves_first_cancel_and_runs_second_cleanup(self) -> None:
        cancels = [KeyboardInterrupt("unlink ki"), SystemExit(130), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel_type=type(cancel_obj).__name__):
                with _fault_scope(
                    source_name=self.source_path.name,
                    unlink_exc=cancel_obj,
                    td_exc=OSError("subsequent cleanup failure"),
                ) as (records, _, _):
                    with self.assertRaises(type(cancel_obj)) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            pass
                    self.assertIs(ctx.exception, cancel_obj)
                    self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_parent_cleanup_cancel_preserves_cancel_identity(self) -> None:
        cancels = [KeyboardInterrupt("td ki"), SystemExit(131), GeneratorExit()]
        for cancel_obj in cancels:
            with self.subTest(cancel_type=type(cancel_obj).__name__):
                with _fault_scope(
                    source_name=self.source_path.name,
                    unlink_exc=None,
                    td_exc=cancel_obj,
                ) as (records, _, _):
                    with self.assertRaises(type(cancel_obj)) as ctx:
                        with prepare_disposable_cli(
                            self.source_path,
                            expected_sha256=self.digest,
                        ):
                            pass
                    self.assertIs(ctx.exception, cancel_obj)
                    self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_preexisting_body_cancel_overrides_cleanup_cancel(self) -> None:
        body_cancel = KeyboardInterrupt("preexisting body cancel")
        cleanup_cancel = KeyboardInterrupt("cleanup cancel")
        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=cleanup_cancel,
        ) as (records, _, _):
            with self.assertRaises(KeyboardInterrupt) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    raise body_cancel
            self.assertIs(ctx.exception, body_cancel)
            self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_pre_yield_sha_mismatch_preserves_original_error(self) -> None:
        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=KeyboardInterrupt("unlink cancel during pre-yield"),
            td_exc=OSError("td error during pre-yield"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256="0" * 64):
                    self.fail("Should not reach yield on SHA mismatch")
            self.assertIn("SHA-256 mismatch", str(ctx.exception))
            self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_removal_sensitivity_negative_proof(self) -> None:
        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=OSError("unlink failed"),
        ) as (records, _, _):
            with self.assertRaises(CliArtifactError) as ctx:
                with prepare_disposable_cli(self.source_path, expected_sha256=self.digest):
                    raise RuntimeError("original body error")
            self.assertNotIsInstance(ctx.exception, RuntimeError)
            self.assertIn("td_cleanup", records)
            self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_proposal_cli_context_lock_order_real_flock(self) -> None:
        lock_root = self.tmp_path / "lock_root"
        lock_root.mkdir()
        real_release = runner_mod.release_proposal_lock
        state: dict[str, Any] = {
            "both_cleaned_before_release": False,
            "release_called": False,
        }

        with _fault_scope(
            source_name=self.source_path.name,
            unlink_exc=OSError("injected lock order unlink fault"),
        ) as (records, _, _):
            def tracking_release(descriptor: int) -> None:
                state["both_cleaned_before_release"] = (records == ["unlink", "td_cleanup"])
                state["release_called"] = True
                real_release(descriptor)

            orig_runner_release = runner_mod.release_proposal_lock
            runner_mod.release_proposal_lock = tracking_release
            try:
                with self.assertRaises(CliArtifactError):
                    with runner_mod.proposal_cli_context(lock_root, cli=self.source_path):
                        pass
            finally:
                runner_mod.release_proposal_lock = orig_runner_release

            self.assertTrue(state["release_called"])
            self.assertTrue(state["both_cleaned_before_release"])

        desc, _ = runner_mod.acquire_proposal_lock(lock_root)
        runner_mod.release_proposal_lock(desc)


if __name__ == "__main__":
    unittest.main()
