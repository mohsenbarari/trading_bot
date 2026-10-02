"""Hermetic test-support contracts for disposable CLI temporary trees and strict flock contention."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import pathlib
import stat
import tempfile
import unittest
from typing import Any, Iterator


@contextlib.contextmanager
def owned_cli_temporaries() -> Iterator[pathlib.Path]:
    """Context manager owning an isolated 0700 outer directory for disposable CLI calls."""
    outer_td = tempfile.TemporaryDirectory()
    os.chmod(outer_td.name, 0o700)
    outer_path = pathlib.Path(outer_td.name).resolve()
    real_td_cls = tempfile.TemporaryDirectory

    def scoped_td(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
        prefix = kwargs.get("prefix")
        if prefix == "disposable_cli_":
            passed_dir = kwargs.get("dir")
            if passed_dir is None and len(args) < 3:
                routed_kwargs = dict(kwargs)
                routed_kwargs["dir"] = str(outer_path)
                return real_td_cls(*args, **routed_kwargs)
            raw_dir = passed_dir if passed_dir is not None else args[2]
            target_dir = pathlib.Path(raw_dir).resolve()
            try:
                target_dir.relative_to(outer_path)
            except ValueError:
                raise ValueError(
                    f"Explicit dir escapes owned outer {outer_path}: {target_dir}"
                )
            return real_td_cls(*args, **kwargs)
        return real_td_cls(*args, **kwargs)

    tempfile.TemporaryDirectory = scoped_td  # type: ignore[misc]
    try:
        yield outer_path
    finally:
        tempfile.TemporaryDirectory = real_td_cls
        outer_td.cleanup()


def assert_real_lock_contended(lock_path: os.PathLike[str] | str) -> None:
    """Assert that lock_path is actively held by attempting a non-blocking exclusive flock."""
    fd = os.open(lock_path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return
            raise
        else:
            raise AssertionError(
                f"Expected lock at {lock_path!r} to be contended, but flock acquired successfully"
            )
    finally:
        os.close(fd)


class TestOwnedCliTemporaries(unittest.TestCase):
    """Lifecycle and isolation tests for owned_cli_temporaries context manager."""

    def setUp(self) -> None:
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.fixture_path = pathlib.Path(self._fixture_dir.name).resolve()

    def tearDown(self) -> None:
        self._fixture_dir.cleanup()

    def test_normal_exit_routes_cli_and_cleans_backups_and_preserves_sentinel(self) -> None:
        sentinel_path = self.fixture_path / "unrelated_sentinel.txt"
        sentinel_path.write_text("keep_unrelated")
        orig_td = tempfile.TemporaryDirectory
        orig_td_cleanup = tempfile.TemporaryDirectory.cleanup
        outer_cleaned_with_restored_factory = False
        outer_ref: list[pathlib.Path] = []

        def spy_cleanup(td_self: Any) -> None:
            nonlocal outer_cleaned_with_restored_factory
            td_resolved = pathlib.Path(td_self.name).resolve()
            if outer_ref and td_resolved == outer_ref[0]:
                self.assertIs(tempfile.TemporaryDirectory, orig_td)
                outer_cleaned_with_restored_factory = True
            orig_td_cleanup(td_self)

        tempfile.TemporaryDirectory.cleanup = spy_cleanup  # type: ignore[misc]
        try:
            with owned_cli_temporaries() as outer:
                outer_ref.append(outer)
                self.assertTrue(outer.is_dir())
                self.assertEqual(stat.S_IMODE(outer.stat().st_mode), 0o700)

                # Matching disposable CLI TemporaryDirectory call
                cli_td = tempfile.TemporaryDirectory(prefix="disposable_cli_")
                parent_path = pathlib.Path(cli_td.name).resolve()
                self.assertEqual(parent_path.parent, outer)

                child_file = parent_path / "sample_cli"
                child_file.write_bytes(b"cli-executable")
                old_ino = parent_path.stat().st_ino

                # Simulate parent replacement: rename to backup sibling
                backup_path = parent_path.with_name(parent_path.name + "_old_ino")
                parent_path.rename(backup_path)
                self.assertEqual(backup_path.stat().st_ino, old_ino)

                # Recreate original named parent with 0700 mode and child
                parent_path.mkdir(mode=0o700)
                os.chmod(parent_path, 0o700)
                recreated_child = parent_path / "sample_cli"
                recreated_child.write_bytes(b"cli-executable")
                new_ino = parent_path.stat().st_ino

                self.assertNotEqual(old_ino, new_ino)
                self.assertTrue(backup_path.exists())
                self.assertTrue(parent_path.exists())
                self.assertTrue((backup_path / "sample_cli").exists())
                self.assertTrue(recreated_child.exists())

                # Foreign non-target TD call remains outside outer
                foreign_td = tempfile.TemporaryDirectory(prefix="foreign_td_")
                foreign_path = pathlib.Path(foreign_td.name).resolve()
                self.assertNotEqual(foreign_path.parent, outer)
                foreign_td.cleanup()
        finally:
            tempfile.TemporaryDirectory.cleanup = orig_td_cleanup

        self.assertTrue(outer_cleaned_with_restored_factory)
        self.assertFalse(outer.exists())
        self.assertFalse(parent_path.exists())
        self.assertFalse(backup_path.exists())
        self.assertIs(tempfile.TemporaryDirectory, orig_td)
        self.assertIs(tempfile.TemporaryDirectory.cleanup, orig_td_cleanup)
        self.assertTrue(sentinel_path.exists())
        self.assertEqual(sentinel_path.read_text(), "keep_unrelated")

    def test_exact_prefix_routing_and_similar_nontarget_forwarding_regression(self) -> None:
        orig_td = tempfile.TemporaryDirectory
        forwarded_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def spy_orig_td(*args: Any, **kwargs: Any) -> tempfile.TemporaryDirectory[str]:
            forwarded_calls.append((args, kwargs))
            return orig_td(*args, **kwargs)

        tempfile.TemporaryDirectory = spy_orig_td  # type: ignore[misc]
        try:
            with owned_cli_temporaries() as outer:
                self.assertEqual(stat.S_IMODE(outer.stat().st_mode), 0o700)
                target_td = tempfile.TemporaryDirectory(prefix="disposable_cli_")
                target_path = pathlib.Path(target_td.name).resolve()
                self.assertEqual(target_path.parent, outer)

                calls_before = len(forwarded_calls)
                td_unrelated = tempfile.TemporaryDirectory(prefix="disposable_cli_unrelated_")
                path_unrelated = pathlib.Path(td_unrelated.name).resolve()
                self.assertNotEqual(path_unrelated.parent, outer)

                td_double_under = tempfile.TemporaryDirectory(prefix="disposable_cli__")
                path_double_under = pathlib.Path(td_double_under.name).resolve()
                self.assertNotEqual(path_double_under.parent, outer)

                self.assertEqual(forwarded_calls[calls_before][1].get("prefix"), "disposable_cli_unrelated_")
                self.assertEqual(forwarded_calls[calls_before + 1][1].get("prefix"), "disposable_cli__")

                outside_dir = self.fixture_path / "outside_unrelated_dir"
                outside_dir.mkdir()
                unrelated_outside_td = tempfile.TemporaryDirectory(
                    prefix="disposable_cli_unrelated_", dir=str(outside_dir)
                )
                self.assertEqual(pathlib.Path(unrelated_outside_td.name).resolve().parent, outside_dir)
                unrelated_outside_td.cleanup()

                td_unrelated.cleanup()
                td_double_under.cleanup()
                self.assertFalse(path_unrelated.exists())
                self.assertFalse(path_double_under.exists())
                self.assertTrue(outer.exists())
                self.assertTrue(target_path.exists())
        finally:
            tempfile.TemporaryDirectory = orig_td

    def test_escaping_dir_rejected_and_constrained_dir_allowed(self) -> None:
        outside_dir = self.fixture_path / "outside_dir"
        outside_dir.mkdir()

        with owned_cli_temporaries() as outer:
            with self.assertRaises(ValueError) as ctx:
                tempfile.TemporaryDirectory(prefix="disposable_cli_", dir=str(outside_dir))
            self.assertIn("escapes owned outer", str(ctx.exception))

            allowed_sub = outer / "allowed_sub"
            allowed_sub.mkdir()
            sub_td = tempfile.TemporaryDirectory(prefix="disposable_cli_", dir=str(allowed_sub))
            sub_path = pathlib.Path(sub_td.name).resolve()
            self.assertTrue(sub_path.is_relative_to(outer))
            sub_td.cleanup()

    def test_body_errors_and_cancellations_restore_hooks_and_clean_all(self) -> None:
        test_exceptions = [
            RuntimeError("ordinary body fault"),
            KeyboardInterrupt("body ki"),
            SystemExit(42),
            GeneratorExit(),
        ]
        orig_td = tempfile.TemporaryDirectory
        orig_td_cleanup = tempfile.TemporaryDirectory.cleanup

        for exc in test_exceptions:
            with self.subTest(exc_type=type(exc).__name__):
                outer_ref: list[pathlib.Path] = []
                parent_ref: list[pathlib.Path] = []
                backup_ref: list[pathlib.Path] = []
                outer_cleaned_with_restored_factory = False

                def spy_cleanup(td_self: Any) -> None:
                    nonlocal outer_cleaned_with_restored_factory
                    td_resolved = pathlib.Path(td_self.name).resolve()
                    if outer_ref and td_resolved == outer_ref[0]:
                        self.assertIs(tempfile.TemporaryDirectory, orig_td)
                        outer_cleaned_with_restored_factory = True
                    orig_td_cleanup(td_self)

                tempfile.TemporaryDirectory.cleanup = spy_cleanup  # type: ignore[misc]
                try:
                    with self.assertRaises(type(exc)) as ctx:
                        with owned_cli_temporaries() as outer:
                            outer_ref.append(outer)
                            cli_td = tempfile.TemporaryDirectory(prefix="disposable_cli_")
                            parent_path = pathlib.Path(cli_td.name).resolve()
                            parent_ref.append(parent_path)

                            child_file = parent_path / "sample_cli"
                            child_file.write_bytes(b"content")

                            backup_path = parent_path.with_name(parent_path.name + "_old_ino")
                            parent_path.rename(backup_path)
                            backup_ref.append(backup_path)

                            parent_path.mkdir(mode=0o700)
                            os.chmod(parent_path, 0o700)
                            (parent_path / "sample_cli").write_bytes(b"content")

                            raise exc
                finally:
                    tempfile.TemporaryDirectory.cleanup = orig_td_cleanup

                self.assertIs(ctx.exception, exc)
                self.assertTrue(outer_cleaned_with_restored_factory)
                self.assertIs(tempfile.TemporaryDirectory, orig_td)
                self.assertIs(tempfile.TemporaryDirectory.cleanup, orig_td_cleanup)
                self.assertFalse(outer_ref[0].exists())
                self.assertFalse(parent_ref[0].exists())
                self.assertFalse(backup_ref[0].exists())


class TestAssertRealLockContended(unittest.TestCase):
    """Hermetic validation of assert_real_lock_contended helper."""

    def setUp(self) -> None:
        self._fixture_dir = tempfile.TemporaryDirectory()
        self.fixture_path = pathlib.Path(self._fixture_dir.name).resolve()
        self.lock_file = self.fixture_path / "test_target.lock"
        self.lock_file.write_bytes(b"")

    def tearDown(self) -> None:
        self._fixture_dir.cleanup()

    def test_held_lock_returns_success_closes_probe_fd_and_preserves_holder(self) -> None:
        holder_fd = os.open(self.lock_file, os.O_RDWR)
        try:
            fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            captured_fds: list[int] = []
            real_flock = fcntl.flock

            def spy_flock(fd: int, operation: int) -> None:
                captured_fds.append(fd)
                real_flock(fd, operation)

            saved_flock = fcntl.flock
            fcntl.flock = spy_flock  # type: ignore[misc]
            try:
                assert_real_lock_contended(self.lock_file)
            finally:
                fcntl.flock = saved_flock

            self.assertEqual(len(captured_fds), 1)
            probe_fd = captured_fds[0]

            # Physical probeFD closed via saved-real fstat EBADF
            with self.assertRaises(OSError) as ctx:
                os.fstat(probe_fd)
            self.assertEqual(ctx.exception.errno, errno.EBADF)

            # Holder is still held
            probe2_fd = os.open(self.lock_file, os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe2_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe2_fd)
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_unheld_lock_raises_assertion_closes_fd_and_allows_reacquisition(self) -> None:
        captured_fds: list[int] = []
        real_flock = fcntl.flock

        def spy_flock(fd: int, operation: int) -> None:
            captured_fds.append(fd)
            real_flock(fd, operation)

        saved_flock = fcntl.flock
        fcntl.flock = spy_flock  # type: ignore[misc]
        try:
            with self.assertRaises(AssertionError) as ctx:
                assert_real_lock_contended(self.lock_file)
            self.assertIn("to be contended, but flock acquired successfully", str(ctx.exception))
        finally:
            fcntl.flock = saved_flock

        self.assertEqual(len(captured_fds), 1)
        probe_fd = captured_fds[0]
        with self.assertRaises(OSError) as ctx_stat:
            os.fstat(probe_fd)
        self.assertEqual(ctx_stat.exception.errno, errno.EBADF)

        # Subsequent real reacquisition
        reacquired_fd = os.open(self.lock_file, os.O_RDWR)
        try:
            fcntl.flock(reacquired_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(reacquired_fd, fcntl.LOCK_UN)
        finally:
            os.close(reacquired_fd)

    def test_injected_eio_and_ebadf_propagate_exact_and_close_probe_fd(self) -> None:
        for fault_errno in (errno.EIO, errno.EBADF):
            with self.subTest(fault_errno=fault_errno):
                injected_err = OSError(fault_errno, f"Fault {fault_errno}")
                captured_fds: list[int] = []

                def faulty_flock(fd: int, operation: int) -> None:
                    captured_fds.append(fd)
                    raise injected_err

                saved_flock = fcntl.flock
                fcntl.flock = faulty_flock  # type: ignore[misc]
                try:
                    with self.assertRaises(OSError) as ctx:
                        assert_real_lock_contended(self.lock_file)
                    self.assertIs(ctx.exception, injected_err)
                finally:
                    fcntl.flock = saved_flock

                self.assertEqual(len(captured_fds), 1)
                probe_fd = captured_fds[0]
                with self.assertRaises(OSError) as ctx_stat:
                    os.fstat(probe_fd)
                self.assertEqual(ctx_stat.exception.errno, errno.EBADF)

    def test_probe_cancellations_propagate_exact_and_close_probe_fd(self) -> None:
        cancellations = [
            KeyboardInterrupt("probe ki"),
            SystemExit(77),
            GeneratorExit(),
        ]
        for cancel_obj in cancellations:
            with self.subTest(cancel_type=type(cancel_obj).__name__):
                captured_fds: list[int] = []

                def cancel_flock(fd: int, operation: int) -> None:
                    captured_fds.append(fd)
                    raise cancel_obj

                saved_flock = fcntl.flock
                fcntl.flock = cancel_flock  # type: ignore[misc]
                try:
                    with self.assertRaises(type(cancel_obj)) as ctx:
                        assert_real_lock_contended(self.lock_file)
                    self.assertIs(ctx.exception, cancel_obj)
                finally:
                    fcntl.flock = saved_flock

                self.assertEqual(len(captured_fds), 1)
                probe_fd = captured_fds[0]
                with self.assertRaises(OSError) as ctx_stat:
                    os.fstat(probe_fd)
                self.assertEqual(ctx_stat.exception.errno, errno.EBADF)

    def test_open_failure_closes_nothing_and_probe_not_called(self) -> None:
        nonexistent = self.fixture_path / "does_not_exist.lock"
        flock_called = False
        close_called_fds: list[int] = []
        real_close = os.close

        def spy_flock(fd: int, operation: int) -> None:
            nonlocal flock_called
            flock_called = True

        def spy_close(fd: int) -> None:
            close_called_fds.append(fd)
            real_close(fd)

        saved_flock = fcntl.flock
        saved_close = os.close
        fcntl.flock = spy_flock  # type: ignore[misc]
        os.close = spy_close  # type: ignore[misc]
        try:
            with self.assertRaises(FileNotFoundError):
                assert_real_lock_contended(nonexistent)
        finally:
            fcntl.flock = saved_flock
            os.close = saved_close

        self.assertFalse(flock_called)
        self.assertEqual(close_called_fds, [])


if __name__ == "__main__":
    unittest.main()
