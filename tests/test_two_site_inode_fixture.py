"""Hermetic test fixture for holding an original file inode open during replacement."""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import errno
import os
import pathlib
import stat
import tempfile
import unittest


@contextlib.contextmanager
def hold_original_inode(
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
) -> Iterator[os.stat_result]:
    """Hold a read-only descriptor to an original file inode until context exit.

    Yields the real os.fstat stat_result of the descriptor, and closes the
    descriptor in finally on all normal, exception, and cancellation exits.
    Does not read file contents, mutate paths, unlink, rename, or chmod.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        yield os.fstat(fd)
    finally:
        os.close(fd)


class TestHoldOriginalInodeFixture(unittest.TestCase):
    """Test suite validating hold_original_inode lifecycle and inode keepalive."""

    def setUp(self) -> None:
        self._real_open = os.open
        self._real_close = os.close
        self._real_fstat = os.fstat

        self._fixture_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._fixture_dir.name).resolve()
        os.chmod(self.tmp_path, 0o700)

        self.nested_dir = self.tmp_path / "nested"
        self.nested_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.nested_dir, 0o700)

        self.target_content = b"#!/bin/sh\necho 'hold-original-inode-payload'\n"
        self.target_file = self.nested_dir / "target_cli"
        self.target_file.write_bytes(self.target_content)
        os.chmod(self.target_file, 0o700)

        init_st = self.target_file.stat()
        self.target_dev = init_st.st_dev
        self.target_ino = init_st.st_ino
        self.target_mode = stat.S_IMODE(init_st.st_mode)

    def tearDown(self) -> None:
        os.open = self._real_open
        os.close = self._real_close
        os.fstat = self._real_fstat
        self._fixture_dir.cleanup()

    @contextlib.contextmanager
    def _scoped_tracking(self) -> Iterator[tuple[list[int], list[int]]]:
        opened_fds: list[int] = []
        closed_fds: list[int] = []
        real_open = self._real_open
        real_close = self._real_close

        def tracking_open(*args: object, **kwargs: object) -> int:
            fd = real_open(*args, **kwargs)  # type: ignore[arg-type]
            opened_fds.append(fd)
            return fd

        def tracking_close(fd: int, *args: object, **kwargs: object) -> None:
            closed_fds.append(fd)
            real_close(fd, *args, **kwargs)

        os.open = tracking_open  # type: ignore[assignment]
        os.close = tracking_close  # type: ignore[assignment]
        try:
            yield opened_fds, closed_fds
        finally:
            os.open = real_open
            os.close = real_close

    def _assert_target_unmutated(self) -> None:
        self.assertTrue(self.target_file.is_file())
        self.assertEqual(self.target_file.read_bytes(), self.target_content)
        self.assertEqual(stat.S_IMODE(self.target_file.stat().st_mode), self.target_mode)
        self.assertEqual(self.target_file.stat().st_dev, self.target_dev)
        self.assertEqual(self.target_file.stat().st_ino, self.target_ino)
        self.assertEqual(self.target_file.parent, self.nested_dir)
        self.assertEqual(sorted(os.listdir(self.nested_dir)), ["target_cli"])
        self.assertEqual(sorted(os.listdir(self.tmp_path)), ["nested"])

    def _assert_fd_closed(self, fd: int) -> None:
        with self.assertRaises(OSError) as ctx_fstat:
            self._real_fstat(fd)
        self.assertEqual(ctx_fstat.exception.errno, errno.EBADF)
        with self.assertRaises(OSError) as ctx_close:
            self._real_close(fd)
        self.assertEqual(ctx_close.exception.errno, errno.EBADF)

    def test_hold_original_inode_prevents_inode_reuse_during_replacement(self) -> None:
        """Proves distinct inode for replacement while original descriptor holds old inode."""
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with hold_original_inode(self.target_file) as orig_stat:
                self.assertIsInstance(orig_stat, os.stat_result)
                self.assertEqual(orig_stat.st_dev, self.target_dev)
                self.assertEqual(orig_stat.st_ino, self.target_ino)
                self.assertEqual(stat.S_IMODE(orig_stat.st_mode), 0o700)

                self.assertTrue(opened_fds)
                tracked_fd = opened_fds[-1]

                # Test caller unlinks original file and recreates same filename with same content/mode
                self.target_file.unlink()
                self.target_file.write_bytes(self.target_content)
                os.chmod(self.target_file, 0o700)

                new_stat = self.target_file.stat()
                # Replacement path has distinct inode from held original inode
                self.assertEqual(new_stat.st_dev, orig_stat.st_dev)
                self.assertNotEqual(new_stat.st_ino, orig_stat.st_ino)
                self.assertNotEqual(
                    (new_stat.st_dev, new_stat.st_ino),
                    (orig_stat.st_dev, orig_stat.st_ino),
                )

                # Tracked original descriptor still reflects old inode, dev, and unlinked nlink=0
                held_stat = self._real_fstat(tracked_fd)
                self.assertEqual(held_stat.st_dev, orig_stat.st_dev)
                self.assertEqual(held_stat.st_ino, orig_stat.st_ino)
                self.assertEqual(held_stat.st_size, orig_stat.st_size)
                self.assertEqual(held_stat.st_nlink, 0)
                self.assertEqual(new_stat.st_nlink, 1)

            # After context exit: descriptor closed exactly once and enters EBADF
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

            # Helper leaves caller-controlled replacement file intact
            self.assertTrue(self.target_file.is_file())
            self.assertEqual(self.target_file.read_bytes(), self.target_content)
            self.assertEqual(stat.S_IMODE(self.target_file.stat().st_mode), 0o700)
            self.assertEqual(self.target_file.stat().st_ino, new_stat.st_ino)

    def test_hold_original_inode_leaves_unreplaced_target_unmutated(self) -> None:
        """Normal context execution without caller mutation leaves target unmutated."""
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with hold_original_inode(self.target_file) as orig_stat:
                self.assertEqual(orig_stat.st_ino, self.target_ino)
                tracked_fd = opened_fds[-1]

            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_body_runtime_error_propagates_exact_identity_and_cleans_up(self) -> None:
        """Ordinary body exception propagates exact identity and cleans up descriptor."""
        injected = RuntimeError("injected body runtime failure")
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with self.assertRaises(RuntimeError) as ctx:
                with hold_original_inode(self.target_file):
                    tracked_fd = opened_fds[-1]
                    raise injected

            self.assertIs(ctx.exception, injected)
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_body_keyboard_interrupt_propagates_exact_identity_and_cleans_up(self) -> None:
        """Body KeyboardInterrupt cancellation propagates exact identity and cleans up."""
        injected = KeyboardInterrupt("injected body interrupt")
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with self.assertRaises(KeyboardInterrupt) as ctx:
                with hold_original_inode(self.target_file):
                    tracked_fd = opened_fds[-1]
                    raise injected

            self.assertIs(ctx.exception, injected)
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_body_system_exit_propagates_exact_identity_and_cleans_up(self) -> None:
        """Body SystemExit cancellation propagates exact identity and cleans up."""
        injected = SystemExit(42)
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with self.assertRaises(SystemExit) as ctx:
                with hold_original_inode(self.target_file):
                    tracked_fd = opened_fds[-1]
                    raise injected

            self.assertIs(ctx.exception, injected)
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_body_generator_exit_propagates_exact_identity_and_cleans_up(self) -> None:
        """Body GeneratorExit cancellation propagates exact identity and cleans up."""
        injected = GeneratorExit("injected body generator exit")
        with self._scoped_tracking() as (opened_fds, closed_fds):
            with self.assertRaises(GeneratorExit) as ctx:
                with hold_original_inode(self.target_file):
                    tracked_fd = opened_fds[-1]
                    raise injected

            self.assertIs(ctx.exception, injected)
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_before_yield_fstat_ordinary_failure_closes_fd_and_propagates_identity(self) -> None:
        """Ordinary failure in fstat before yield closes descriptor and propagates identity."""
        injected = OSError(errno.EIO, "injected fstat io failure")
        body_entered = False

        def failing_fstat(fd: int) -> os.stat_result:
            raise injected

        with self._scoped_tracking() as (opened_fds, closed_fds):
            os.fstat = failing_fstat  # type: ignore[assignment]
            with self.assertRaises(OSError) as ctx:
                with hold_original_inode(self.target_file):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertIs(ctx.exception, injected)
            self.assertTrue(opened_fds)
            tracked_fd = opened_fds[-1]
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_before_yield_fstat_keyboard_interrupt_closes_fd_and_propagates_identity(self) -> None:
        """KeyboardInterrupt in fstat before yield closes descriptor and propagates identity."""
        injected = KeyboardInterrupt("injected fstat interrupt")
        body_entered = False

        def failing_fstat(fd: int) -> os.stat_result:
            raise injected

        with self._scoped_tracking() as (opened_fds, closed_fds):
            os.fstat = failing_fstat  # type: ignore[assignment]
            with self.assertRaises(KeyboardInterrupt) as ctx:
                with hold_original_inode(self.target_file):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertIs(ctx.exception, injected)
            self.assertTrue(opened_fds)
            tracked_fd = opened_fds[-1]
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_before_yield_fstat_system_exit_closes_fd_and_propagates_identity(self) -> None:
        """SystemExit in fstat before yield closes descriptor and propagates identity."""
        injected = SystemExit(99)
        body_entered = False

        def failing_fstat(fd: int) -> os.stat_result:
            raise injected

        with self._scoped_tracking() as (opened_fds, closed_fds):
            os.fstat = failing_fstat  # type: ignore[assignment]
            with self.assertRaises(SystemExit) as ctx:
                with hold_original_inode(self.target_file):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertIs(ctx.exception, injected)
            self.assertTrue(opened_fds)
            tracked_fd = opened_fds[-1]
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_before_yield_fstat_generator_exit_closes_fd_and_propagates_identity(self) -> None:
        """GeneratorExit in fstat before yield closes descriptor and propagates identity."""
        injected = GeneratorExit("injected fstat generator exit")
        body_entered = False

        def failing_fstat(fd: int) -> os.stat_result:
            raise injected

        with self._scoped_tracking() as (opened_fds, closed_fds):
            os.fstat = failing_fstat  # type: ignore[assignment]
            with self.assertRaises(GeneratorExit) as ctx:
                with hold_original_inode(self.target_file):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertIs(ctx.exception, injected)
            self.assertTrue(opened_fds)
            tracked_fd = opened_fds[-1]
            self.assertEqual(closed_fds.count(tracked_fd), 1)
            self._assert_fd_closed(tracked_fd)

        self._assert_target_unmutated()

    def test_open_failure_propagates_without_entering_body_or_attempting_close(self) -> None:
        """Open failure never enters body, closes nothing, and propagates exact exception."""
        missing_path = self.nested_dir / "missing_target_file"
        body_entered = False

        with self._scoped_tracking() as (opened_fds, closed_fds):
            with self.assertRaises(FileNotFoundError):
                with hold_original_inode(missing_path):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertEqual(opened_fds, [])
            self.assertEqual(closed_fds, [])

        injected = OSError(errno.EACCES, "injected open access failure")

        def failing_open(*args: object, **kwargs: object) -> int:
            raise injected

        with self._scoped_tracking() as (opened_fds, closed_fds):
            os.open = failing_open  # type: ignore[assignment]
            with self.assertRaises(OSError) as ctx:
                with hold_original_inode(self.target_file):
                    body_entered = True

            self.assertFalse(body_entered)
            self.assertIs(ctx.exception, injected)
            self.assertEqual(opened_fds, [])
            self.assertEqual(closed_fds, [])

        self._assert_target_unmutated()

    def test_symlink_path_rejected_by_nofollow(self) -> None:
        """Symlink target fails open via O_NOFOLLOW without entering body."""
        symlink_path = self.nested_dir / "symlink_cli"
        symlink_path.symlink_to(self.target_file)
        body_entered = False

        try:
            with self._scoped_tracking() as (opened_fds, closed_fds):
                with self.assertRaises(OSError):
                    with hold_original_inode(symlink_path):
                        body_entered = True

                self.assertFalse(body_entered)
                self.assertEqual(opened_fds, [])
                self.assertEqual(closed_fds, [])
        finally:
            symlink_path.unlink(missing_ok=True)

        self._assert_target_unmutated()


if __name__ == "__main__":
    unittest.main()
