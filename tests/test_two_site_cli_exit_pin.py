"""Hermetic regression tests for safe disposable CLI exit pin capture and verification."""

from __future__ import annotations

import collections.abc
import dataclasses
import errno
import hashlib
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

from scripts.two_site_cli_exit_pin import (
    CliExitPin,
    CliExitPinError,
    capture_cli_exit_pin,
    verify_cli_exit_pin,
    LABEL_CLOSE_FAILED,
    LABEL_COPY_MISMATCH,
    LABEL_HASH_MISMATCH,
    LABEL_INVALID_ARGUMENT,
    LABEL_OPEN_FAILED,
    LABEL_PARENT_MISMATCH,
    LABEL_PATH_NOT_FOUND,
    LABEL_PROBE_FAILED,
    LABEL_PROBE_INVALID,
    LABEL_PROBE_MISMATCH,
    LABEL_PROBE_RESULT_INVALID,
    LABEL_READ_FAILED,
    LABEL_SIZE_MISMATCH,
    LABEL_STAT_FAILED,
)
from tests.test_two_site_inode_fixture import hold_original_inode


class TestCliExitPin(unittest.TestCase):
    """Regression test suite for CliExitPin capture and verify contracts."""

    def setUp(self) -> None:
        self._tmp_outer = tempfile.TemporaryDirectory(prefix="cli_exit_pin_outer_")
        self.outer_path = pathlib.Path(self._tmp_outer.name).resolve()
        os.chmod(self.outer_path, 0o700)

        self.fixture_dir = self.outer_path / "pinned_dir"
        self.fixture_dir.mkdir(mode=0o700)

        self.disposable_path = self.fixture_dir / "sample_cli"
        self.content = b"#!/bin/sh\necho 'exit-pin-test-payload'\n"
        self.disposable_path.write_bytes(self.content)
        os.chmod(self.disposable_path, 0o700)

        self.digest = hashlib.sha256(self.content).hexdigest()
        self.version = "1.2.3-test"

    def tearDown(self) -> None:
        self._tmp_outer.cleanup()

    def _replace_file_inode(self) -> int:
        with hold_original_inode(self.disposable_path) as old_stat:
            self.disposable_path.unlink()
            self.disposable_path.write_bytes(self.content)
            os.chmod(self.disposable_path, 0o700)
            new_ino = os.lstat(self.disposable_path).st_ino
            self.assertNotEqual(old_stat.st_ino, new_ino)
            return new_ino

    def _replace_parent_inode(self) -> int:
        old_ino = os.lstat(self.fixture_dir).st_ino
        backup_dir = self.outer_path / "pinned_dir_backup"
        if backup_dir.exists():
            for child in backup_dir.iterdir():
                child.unlink()
            backup_dir.rmdir()
        self.fixture_dir.rename(backup_dir)
        self.fixture_dir.mkdir(mode=0o700)
        new_file = self.fixture_dir / "sample_cli"
        new_file.write_bytes(self.content)
        os.chmod(new_file, 0o700)
        new_ino = os.lstat(self.fixture_dir).st_ino
        self.assertNotEqual(old_ino, new_ino)
        return new_ino

    def test_stable_capture_and_verify_no_retained_descriptors(self) -> None:
        """PIN-IO-2: Immutable pin baseline, factual descriptor closure verified with EBADF."""
        opened_fds: list[int] = []
        real_open = os.open

        def tracking_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            fd = real_open(path, flags, *args, **kwargs)
            opened_fds.append(fd)
            return fd

        with mock.patch("os.open", side_effect=tracking_open):
            pin = capture_cli_exit_pin(
                self.disposable_path,
                expected_sha256=self.digest,
                expected_version=self.version,
            )

        self.assertIsInstance(pin, CliExitPin)
        self.assertEqual(pin.disposable, self.disposable_path)
        self.assertEqual(pin.expected_sha256, self.digest)
        self.assertEqual(pin.expected_version, self.version)
        self.assertEqual(pin.size, len(self.content))
        self.assertEqual(pin.mode, 0o700)
        self.assertEqual(pin.nlink, 1)
        self.assertEqual(pin.uid, os.geteuid())
        self.assertEqual(pin.parent_path, self.fixture_dir)
        self.assertEqual(pin.parent_mode, 0o700)
        self.assertEqual(pin.parent_uid, os.geteuid())

        with self.assertRaises(dataclasses.FrozenInstanceError):
            pin.size = 999  # type: ignore[misc]

        self.assertTrue(opened_fds)
        for fd in opened_fds:
            with self.assertRaises(OSError) as ctx:
                os.fstat(fd)
            self.assertEqual(ctx.exception.errno, errno.EBADF)

        verify_fds: list[int] = []

        def tracking_verify_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            fd = real_open(path, flags, *args, **kwargs)
            verify_fds.append(fd)
            return fd

        with mock.patch("os.open", side_effect=tracking_verify_open):
            verify_cli_exit_pin(pin)

        self.assertTrue(verify_fds)
        for fd in verify_fds:
            with self.assertRaises(OSError) as ctx:
                os.fstat(fd)
            self.assertEqual(ctx.exception.errno, errno.EBADF)

        self.assertEqual(self.disposable_path.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.disposable_path.stat().st_mode), 0o700)

    def test_capture_validations_and_empty_file(self) -> None:
        """PIN-IO-1: Capture defensive validations and empty file support."""
        empty_path = self.fixture_dir / "empty_cli"
        empty_path.write_bytes(b"")
        os.chmod(empty_path, 0o700)
        empty_digest = hashlib.sha256(b"").hexdigest()
        empty_pin = capture_cli_exit_pin(empty_path, expected_sha256=empty_digest)
        self.assertEqual(empty_pin.size, 0)
        verify_cli_exit_pin(empty_pin)

        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(str(self.disposable_path), self.digest)  # type: ignore[arg-type]
        self.assertEqual(str(ctx.exception), LABEL_INVALID_ARGUMENT)

        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(pathlib.Path("sample_cli"), self.digest)
        self.assertEqual(str(ctx.exception), LABEL_INVALID_ARGUMENT)

        unresolved = self.fixture_dir / "subdir" / ".." / self.disposable_path.name
        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(unresolved, self.digest)
        self.assertEqual(str(ctx.exception), LABEL_INVALID_ARGUMENT)

        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(self.fixture_dir / "absent", self.digest)
        self.assertEqual(str(ctx.exception), LABEL_PATH_NOT_FOUND)

        sym_path = self.fixture_dir / "sym_cli"
        sym_path.symlink_to(self.disposable_path)
        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(sym_path, self.digest)
        self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)

        os.chmod(self.disposable_path, 0o755)
        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(self.disposable_path, self.digest)
        self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)
        os.chmod(self.disposable_path, 0o700)

        os.chmod(self.fixture_dir, 0o755)
        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(self.disposable_path, self.digest)
        self.assertEqual(str(ctx.exception), LABEL_PARENT_MISMATCH)
        os.chmod(self.fixture_dir, 0o700)

        for bad_hash in ["", "not_64_hex", "0" * 63 + "Z"]:
            with self.assertRaises(CliExitPinError) as ctx:
                capture_cli_exit_pin(self.disposable_path, expected_sha256=bad_hash)
            self.assertEqual(str(ctx.exception), LABEL_INVALID_ARGUMENT)

        with self.assertRaises(CliExitPinError) as ctx:
            capture_cli_exit_pin(self.disposable_path, expected_sha256="0" * 64)
        self.assertEqual(str(ctx.exception), LABEL_HASH_MISMATCH)

        with mock.patch("os.geteuid", return_value=os.geteuid() + 1000):
            with self.assertRaises(CliExitPinError) as ctx:
                capture_cli_exit_pin(self.disposable_path, self.digest)
            self.assertEqual(str(ctx.exception), LABEL_PARENT_MISMATCH)

    def test_bounded_growth_rejection(self) -> None:
        """PIN-IO-3: Bounded read rejects file growth without infinite read loops."""
        pin = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        read_calls = 0
        bytes_provided = 0

        def infinite_growth_reader(fd: int, n: int) -> bytes:
            nonlocal read_calls, bytes_provided
            read_calls += 1
            chunk = b"G" * n
            bytes_provided += len(chunk)
            return chunk

        with mock.patch("os.read", side_effect=infinite_growth_reader):
            with self.assertRaises(CliExitPinError) as ctx:
                verify_cli_exit_pin(pin)
            self.assertEqual(str(ctx.exception), LABEL_SIZE_MISMATCH)
            self.assertLessEqual(bytes_provided, pin.size + 1)
            self.assertLessEqual(read_calls, 2)

    def test_io_and_close_failures_wrapped_in_both_phases(self) -> None:
        """PIN-IO-1/2: IO/close failures wrapped with fixed labels and other descriptor reached."""
        orig_close = os.close

        with mock.patch("os.open", side_effect=OSError("open failed")):
            with self.assertRaises(CliExitPinError) as ctx:
                capture_cli_exit_pin(self.disposable_path, self.digest)
            self.assertEqual(str(ctx.exception), LABEL_OPEN_FAILED)

        with mock.patch("os.read", side_effect=OSError("read failed")):
            with self.assertRaises(CliExitPinError) as ctx:
                capture_cli_exit_pin(self.disposable_path, self.digest)
            self.assertEqual(str(ctx.exception), LABEL_READ_FAILED)

        closed_in_capture: list[int] = []

        def close_hook_capture(fd: int) -> None:
            closed_in_capture.append(fd)
            orig_close(fd)
            if len(closed_in_capture) == 1:
                raise OSError("synthetic EIO on copy close")

        with mock.patch("os.close", side_effect=close_hook_capture):
            with self.assertRaises(CliExitPinError) as ctx:
                capture_cli_exit_pin(self.disposable_path, self.digest)
            self.assertEqual(str(ctx.exception), LABEL_CLOSE_FAILED)
            self.assertGreaterEqual(len(closed_in_capture), 2)

        for fd in closed_in_capture:
            with self.assertRaises(OSError) as c_ctx:
                os.fstat(fd)
            self.assertEqual(c_ctx.exception.errno, errno.EBADF)

        pin = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        closed_in_verify: list[int] = []

        def close_hook_verify(fd: int) -> None:
            closed_in_verify.append(fd)
            orig_close(fd)
            if len(closed_in_verify) == 1:
                raise OSError("synthetic EIO on verify close")

        with mock.patch("os.close", side_effect=close_hook_verify):
            with self.assertRaises(CliExitPinError) as ctx:
                verify_cli_exit_pin(pin)
            self.assertEqual(str(ctx.exception), LABEL_CLOSE_FAILED)
            self.assertGreaterEqual(len(closed_in_verify), 2)

        for fd in closed_in_verify:
            with self.assertRaises(OSError) as c_ctx:
                os.fstat(fd)
            self.assertEqual(c_ctx.exception.errno, errno.EBADF)

    def test_cancellation_ordering_and_earliest_identity_in_both_phases(self) -> None:
        """PIN-130-2/3: Earliest cancellation preserved across capture and verify phases."""
        orig_close = os.close

        for exc_cls in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(cancel_cls=exc_cls):
                primary_cancel = exc_cls()
                secondary_cancel = exc_cls()

                def read_cancel(fd: int, n: int) -> bytes:
                    raise primary_cancel

                # 1. Capture phase read cancel dominates close cancel
                capture_closed: list[int] = []

                def close_cancel_capture(fd: int) -> None:
                    capture_closed.append(fd)
                    orig_close(fd)
                    if len(capture_closed) == 1:
                        raise secondary_cancel

                with mock.patch("os.read", side_effect=read_cancel):
                    with mock.patch("os.close", side_effect=close_cancel_capture):
                        with self.assertRaises(exc_cls) as ctx:
                            capture_cli_exit_pin(self.disposable_path, self.digest)
                        self.assertIs(ctx.exception, primary_cancel)
                        self.assertGreaterEqual(len(capture_closed), 2)

                for fd in capture_closed:
                    with self.assertRaises(OSError) as c_ctx:
                        os.fstat(fd)
                    self.assertEqual(c_ctx.exception.errno, errno.EBADF)

                # 2. Verify phase read cancel dominates close cancel
                pin = capture_cli_exit_pin(
                    self.disposable_path,
                    expected_sha256=self.digest,
                    expected_version=self.version,
                )

                verify_closed: list[int] = []

                def close_cancel_verify(fd: int) -> None:
                    verify_closed.append(fd)
                    orig_close(fd)
                    if len(verify_closed) == 1:
                        raise secondary_cancel

                with mock.patch("os.read", side_effect=read_cancel):
                    with mock.patch("os.close", side_effect=close_cancel_verify):
                        with self.assertRaises(exc_cls) as v_ctx:
                            verify_cli_exit_pin(pin)
                        self.assertIs(v_ctx.exception, primary_cancel)
                        self.assertGreaterEqual(len(verify_closed), 2)

                for fd in verify_closed:
                    with self.assertRaises(OSError) as c_ctx:
                        os.fstat(fd)
                    self.assertEqual(c_ctx.exception.errno, errno.EBADF)

                # 3. Verify phase cleanup cancel dominates when no primary cancel
                clean_closed: list[int] = []

                def clean_cancel_verify(fd: int) -> None:
                    clean_closed.append(fd)
                    orig_close(fd)
                    if len(clean_closed) == 1:
                        raise secondary_cancel

                with mock.patch("os.close", side_effect=clean_cancel_verify):
                    with self.assertRaises(exc_cls) as co_ctx:
                        verify_cli_exit_pin(pin)
                    self.assertIs(co_ctx.exception, secondary_cancel)
                    self.assertGreaterEqual(len(clean_closed), 2)

                for fd in clean_closed:
                    with self.assertRaises(OSError) as c_ctx:
                        os.fstat(fd)
                    self.assertEqual(c_ctx.exception.errno, errno.EBADF)

    def test_initial_stat_failure_and_cancellation_fenced(self) -> None:
        """PIN-130-1/3: Initial disposable and parent os.stat failures fenced to fixed label."""
        orig_stat = os.stat
        marker = "CONTROLLED_INITIAL_STAT_MARKER"

        for target in (self.disposable_path, self.fixture_dir):
            target_str = str(target.resolve())

            fired = False

            def stat_eio_hook(path: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal fired
                follow = kwargs.get("follow_symlinks", args[2] if len(args) > 2 else True)
                try:
                    p_str = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
                except Exception:
                    p_str = ""
                if not follow and p_str == target_str and not fired:
                    fired = True
                    raise OSError(errno.EIO, marker)
                return orig_stat(path, *args, **kwargs)

            with mock.patch("os.stat", side_effect=stat_eio_hook):
                with self.assertRaises(CliExitPinError) as ctx:
                    capture_cli_exit_pin(self.disposable_path, self.digest)
                self.assertTrue(fired)
                self.assertEqual(str(ctx.exception), LABEL_STAT_FAILED)
                self.assertNotIn(marker, str(ctx.exception))

            for exc_cls in (KeyboardInterrupt, SystemExit, GeneratorExit):
                cancel_obj = exc_cls()
                cancel_fired = False

                def stat_cancel_hook(path: object, *args: object, **kwargs: object) -> os.stat_result:
                    nonlocal cancel_fired
                    follow = kwargs.get("follow_symlinks", args[2] if len(args) > 2 else True)
                    try:
                        p_str = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
                    except Exception:
                        p_str = ""
                    if not follow and p_str == target_str and not cancel_fired:
                        cancel_fired = True
                        raise cancel_obj
                    return orig_stat(path, *args, **kwargs)

                with mock.patch("os.stat", side_effect=stat_cancel_hook):
                    with self.assertRaises(exc_cls) as c_ctx:
                        capture_cli_exit_pin(self.disposable_path, self.digest)
                    self.assertTrue(cancel_fired)
                    self.assertIs(c_ctx.exception, cancel_obj)

    def test_probe_contracts_and_untrusted_prose_sanitization(self) -> None:
        """PIN-130-1/3: Probe contracts, untrusted error prose, Mapping iter/item access fenced."""
        pin = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        calls: list[pathlib.Path] = []

        def valid_probe(p: pathlib.Path) -> dict[str, str]:
            calls.append(p)
            return {
                "path": str(p),
                "version": self.version,
                "sha256": self.digest,
            }

        verify_cli_exit_pin(pin, version_probe=valid_probe)
        self.assertEqual(calls, [self.disposable_path])

        untrusted_marker = "CONTROLLED_UNTRUSTED_PROSE_MARKER"
        probe_err = RuntimeError(untrusted_marker)

        def error_probe(p: pathlib.Path) -> dict[str, str]:
            raise probe_err

        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin, version_probe=error_probe)
        self.assertEqual(str(ctx.exception), LABEL_PROBE_FAILED)
        self.assertNotIn(untrusted_marker, str(ctx.exception))
        self.assertIs(ctx.exception.__cause__, probe_err)

        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin, version_probe="not_callable")  # type: ignore[arg-type]
        self.assertEqual(str(ctx.exception), LABEL_PROBE_INVALID)

        pin_no_ver = capture_cli_exit_pin(self.disposable_path, expected_sha256=self.digest)
        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin_no_ver, version_probe=valid_probe)
        self.assertEqual(str(ctx.exception), LABEL_PROBE_INVALID)

        # Restored independent wrong-SHA case with valid path and version asserts LABEL_PROBE_MISMATCH:
        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(
                pin,
                version_probe=lambda p: {
                    "path": str(p),
                    "version": self.version,
                    "sha256": "0" * 64,
                },
            )
        self.assertEqual(str(ctx.exception), LABEL_PROBE_MISMATCH)

        # Structure/key/type invalid probes assert LABEL_PROBE_RESULT_INVALID:
        for probe in [
            lambda p: "not_a_mapping",
            lambda p: {"path": str(p), "version": self.version},
            lambda p: {"path": str(p), "version": "0" * 64},
            lambda p: {
                "path": str(p),
                "version": self.version,
                "sha256": self.digest,
                "extra": "k",
            },
        ]:
            with self.subTest(probe=probe):
                with self.assertRaises(CliExitPinError) as ctx:
                    verify_cli_exit_pin(pin, version_probe=probe)  # type: ignore[arg-type]
                self.assertEqual(str(ctx.exception), LABEL_PROBE_RESULT_INVALID)

        # Metadata mismatch probes assert LABEL_PROBE_MISMATCH:
        for probe in [
            lambda p: {
                "path": str(p) + "_other",
                "version": self.version,
                "sha256": self.digest,
            },
            lambda p: {
                "path": str(p),
                "version": "wrong_version",
                "sha256": self.digest,
            },
            lambda p: {
                "path": str(p),
                "version": self.version,
                "sha256": "0" * 64,
            },
        ]:
            with self.subTest(probe=probe):
                with self.assertRaises(CliExitPinError) as ctx:
                    verify_cli_exit_pin(pin, version_probe=probe)
                self.assertEqual(str(ctx.exception), LABEL_PROBE_MISMATCH)

        mapping_marker = "CONTROLLED_MAPPING_MARKER"
        iter_err = OSError(errno.EIO, mapping_marker)

        class FailingIterMapping(collections.abc.Mapping):
            def __iter__(self):
                raise iter_err
            def __len__(self) -> int:
                return 3
            def __getitem__(self, key: str) -> str:
                return ""

        with self.assertRaises(CliExitPinError) as iter_ctx:
            verify_cli_exit_pin(pin, version_probe=lambda p: FailingIterMapping())
        self.assertEqual(str(iter_ctx.exception), LABEL_PROBE_RESULT_INVALID)
        self.assertNotIn(mapping_marker, str(iter_ctx.exception))
        self.assertIs(iter_ctx.exception.__cause__, iter_err)

        item_err = OSError(errno.EIO, mapping_marker)

        class FailingItemMapping(collections.abc.Mapping):
            def __iter__(self):
                return iter(["path", "version", "sha256"])
            def __len__(self) -> int:
                return 3
            def __getitem__(self, key: str) -> str:
                raise item_err

        with self.assertRaises(CliExitPinError) as item_ctx:
            verify_cli_exit_pin(pin, version_probe=lambda p: FailingItemMapping())
        self.assertEqual(str(item_ctx.exception), LABEL_PROBE_RESULT_INVALID)
        self.assertNotIn(mapping_marker, str(item_ctx.exception))
        self.assertIs(item_ctx.exception.__cause__, item_err)

        for exc_cls in (KeyboardInterrupt, SystemExit, GeneratorExit):
            cancel_iter_obj = exc_cls()

            class CancelIterMapping(collections.abc.Mapping):
                def __iter__(self):
                    raise cancel_iter_obj
                def __len__(self) -> int:
                    return 3
                def __getitem__(self, key: str) -> str:
                    return ""

            with self.assertRaises(exc_cls) as c_iter_ctx:
                verify_cli_exit_pin(pin, version_probe=lambda p: CancelIterMapping())
            self.assertIs(c_iter_ctx.exception, cancel_iter_obj)

            cancel_item_obj = exc_cls()

            class CancelItemMapping(collections.abc.Mapping):
                def __iter__(self):
                    return iter(["path", "version", "sha256"])
                def __len__(self) -> int:
                    return 3
                def __getitem__(self, key: str) -> str:
                    raise cancel_item_obj

            with self.assertRaises(exc_cls) as c_item_ctx:
                verify_cli_exit_pin(pin, version_probe=lambda p: CancelItemMapping())
            self.assertIs(c_item_ctx.exception, cancel_item_obj)

    def test_probe_comparison_failure_and_cancellation(self) -> None:
        """PIN-131-1/3: Probe value comparison ordinary failure fenced and cancellation preserved."""
        pin = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        marker = "CONTROLLED_COMPARISON_MARKER"
        comp_err = OSError(errno.EIO, marker)

        class FailingCompareValue:
            def __init__(self, exc: BaseException) -> None:
                self._exc = exc
            def __eq__(self, other: object) -> bool:
                raise self._exc
            def __ne__(self, other: object) -> bool:
                raise self._exc

        # Ordinary comparison failure on path, version, or sha256 wrapped to LABEL_PROBE_RESULT_INVALID without prose leak
        for key in ("path", "version", "sha256"):
            with self.subTest(failing_field=key):
                payload: dict[str, object] = {
                    "path": str(self.disposable_path),
                    "version": self.version,
                    "sha256": self.digest,
                }
                payload[key] = FailingCompareValue(comp_err)

                with self.assertRaises(CliExitPinError) as ctx:
                    verify_cli_exit_pin(pin, version_probe=lambda p, pl=payload: pl)  # type: ignore[arg-type]
                self.assertEqual(str(ctx.exception), LABEL_PROBE_RESULT_INVALID)
                self.assertNotIn(marker, str(ctx.exception))
                self.assertIs(ctx.exception.__cause__, comp_err)

        # Comparison cancellations for all 3 cancel types preserve exact identity
        for exc_cls in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for key in ("path", "version", "sha256"):
                with self.subTest(cancel_cls=exc_cls, failing_field=key):
                    cancel_obj = exc_cls()
                    payload = {
                        "path": str(self.disposable_path),
                        "version": self.version,
                        "sha256": self.digest,
                    }
                    payload[key] = FailingCompareValue(cancel_obj)

                    with self.assertRaises(exc_cls) as c_ctx:
                        verify_cli_exit_pin(pin, version_probe=lambda p, pl=payload: pl)  # type: ignore[arg-type]
                    self.assertIs(c_ctx.exception, cancel_obj)

    def test_mutation_and_inode_replacement_before_probe(self) -> None:
        """PIN-IO-3: Real filesystem mutations and inode replacements reject before probe is called."""
        pin = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        probe_called = False

        def tracking_probe(p: pathlib.Path) -> dict[str, str]:
            nonlocal probe_called
            probe_called = True
            return {
                "path": str(p),
                "version": self.version,
                "sha256": self.digest,
            }

        os.chmod(self.disposable_path, 0o755)
        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin, version_probe=tracking_probe)
        self.assertFalse(probe_called)
        self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)
        os.chmod(self.disposable_path, 0o700)

        self._replace_file_inode()
        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin, version_probe=tracking_probe)
        self.assertFalse(probe_called)
        self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)

        self._replace_parent_inode()
        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin, version_probe=tracking_probe)
        self.assertFalse(probe_called)
        self.assertEqual(str(ctx.exception), LABEL_PARENT_MISMATCH)

    def test_eof_and_post_probe_real_inode_replacement_timing(self) -> None:
        """PIN-IO-3/4: Inode replacement timing at EOF and during probe caught with removal sensitivity."""
        pin1 = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )
        orig_read = os.read
        file_eof_hit = False

        def read_hook_file_eof(fd: int, n: int) -> bytes:
            nonlocal file_eof_hit
            data = orig_read(fd, n)
            if not data and not file_eof_hit:
                file_eof_hit = True
                self._replace_file_inode()
            return data

        with mock.patch("os.read", side_effect=read_hook_file_eof):
            with self.assertRaises(CliExitPinError) as ctx:
                verify_cli_exit_pin(pin1)
            self.assertTrue(file_eof_hit)
            self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)

        pin2 = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )
        parent_eof_hit = False

        def read_hook_parent_eof(fd: int, n: int) -> bytes:
            nonlocal parent_eof_hit
            data = orig_read(fd, n)
            if not data and not parent_eof_hit:
                parent_eof_hit = True
                self._replace_parent_inode()
            return data

        with mock.patch("os.read", side_effect=read_hook_parent_eof):
            with self.assertRaises(CliExitPinError) as ctx:
                verify_cli_exit_pin(pin2)
            self.assertTrue(parent_eof_hit)
            self.assertEqual(str(ctx.exception), LABEL_PARENT_MISMATCH)

        pin3 = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        def probe_mutating_file(p: pathlib.Path) -> dict[str, str]:
            self._replace_file_inode()
            return {
                "path": str(p),
                "version": self.version,
                "sha256": self.digest,
            }

        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin3, version_probe=probe_mutating_file)
        self.assertEqual(str(ctx.exception), LABEL_COPY_MISMATCH)

        pin4 = capture_cli_exit_pin(
            self.disposable_path,
            expected_sha256=self.digest,
            expected_version=self.version,
        )

        def probe_mutating_parent(p: pathlib.Path) -> dict[str, str]:
            self._replace_parent_inode()
            return {
                "path": str(p),
                "version": self.version,
                "sha256": self.digest,
            }

        with self.assertRaises(CliExitPinError) as ctx:
            verify_cli_exit_pin(pin4, version_probe=probe_mutating_parent)
        self.assertEqual(str(ctx.exception), LABEL_PARENT_MISMATCH)


if __name__ == "__main__":
    unittest.main()
