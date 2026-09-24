#!/usr/bin/env python3
"""Hermetic tests for AE03 pure rescope receipt byte codec."""
import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    ACCEPTED_RESULT_KEYS_ORDER,
    CONTROL_PATHS,
    MAX_RESCOPE_RECEIPT_BYTES,
    PROTECTED_REFS,
    RESCOPE_RECEIPT_FILENAME,
    RESCOPE_RECEIPT_KEYS,
    RESCOPE_RECEIPT_SCHEMA_VERSION,
    RESCOPE_RESULT_KEYS_ORDER,
    AutopilotError,
    _create_rescope_temp_file,
    load_rescope_receipt,
    parse_rescope_receipt,
    serialize_rescope_receipt,
)


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="asg-ae03-test",
        stage_id="P1-00b",
        sha256="a" * 64,
        expires_at="2026-12-31T23:59:59Z",
        allowed_paths=["scripts/two_site_codex_autopilot.py", "tests/test_two_site_ae03_rescope_recovery.py"],
        forbidden_paths=[],
    )


def _make_repo() -> dict[str, object]:
    return {
        "head": "a1b2c3d4e5" * 4,
        "status": "",
        "protected_refs": {ref: "11" * 20 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "22" * 20 for p in CONTROL_PATHS},
        "assignment_scope": _make_scope(),
    }


def _make_result(repo_head: str = "a1b2c3d4e5" * 4) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "Rescope cycle required due to bounded proposal constraint.",
        "commit": None,
        "next_action": "Execute rescope receipt byte codec.",
        "blockers": ["Proposal exceeded single-unit boundaries."],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": repo_head,
        "head_after": repo_head,
        "head_changed": False,
    }


class TestAE03RescopeReceiptCodec(unittest.TestCase):
    def test_constants_and_result_keys_order(self) -> None:
        self.assertEqual(RESCOPE_RESULT_KEYS_ORDER, ACCEPTED_RESULT_KEYS_ORDER)
        self.assertEqual(set(RESCOPE_RECEIPT_KEYS), set(RESCOPE_RESULT_KEYS_ORDER))

    def test_roundtrip_all_attempts(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        for attempt, expected_terminal in [(1, False), (2, False), (3, True)]:
            raw_bytes = serialize_rescope_receipt(result, repo, attempt=attempt)
            self.assertIsInstance(raw_bytes, bytes)
            self.assertLessEqual(len(raw_bytes), MAX_RESCOPE_RECEIPT_BYTES)

            # Roundtrip parse with live repository
            envelope = parse_rescope_receipt(raw_bytes, repo)
            self.assertEqual(envelope["schema_version"], RESCOPE_RECEIPT_SCHEMA_VERSION)
            self.assertEqual(envelope["attempt"], attempt)
            self.assertEqual(envelope["terminal"], expected_terminal)
            self.assertEqual(envelope["result"]["state"], "BLOCKED")
            self.assertEqual(envelope["result"]["disposition"], "RESCOPE")
            self.assertEqual(envelope["result"]["category"], "INTERNAL_REJECTION")
            self.assertIsNone(envelope["result"]["commit"])
            self.assertIsNone(envelope["result"]["gemini_worker_run_id"])
            self.assertFalse(envelope["result"]["head_changed"])
            self.assertEqual(envelope["result"]["head_before"], repo["head"])
            self.assertEqual(envelope["result"]["head_after"], repo["head"])
            self.assertEqual(tuple(envelope["result"].keys()), RESCOPE_RESULT_KEYS_ORDER)

            # Roundtrip parse with persisted repository facts
            persisted_repo = envelope["repository"]
            envelope_persisted = parse_rescope_receipt(raw_bytes, persisted_repo)
            self.assertEqual(envelope, envelope_persisted)

    def test_serialize_terminal_explicit_validation(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        self.assertTrue(serialize_rescope_receipt(result, repo, attempt=3, terminal=True))
        self.assertTrue(serialize_rescope_receipt(result, repo, attempt=1, terminal=False))

        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=3, terminal=False)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=2, terminal=True)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=1, terminal="False")  # type: ignore[arg-type]

    def test_serialize_attempt_bounds(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        for invalid_attempt in [0, 4, -1, True, False, 1.5, "1", None]:
            with self.assertRaises(AutopilotError):
                serialize_rescope_receipt(result, repo, attempt=invalid_attempt)  # type: ignore[arg-type]

    def test_serialize_result_invariants(self) -> None:
        repo = _make_repo()

        # Non-BLOCKED state
        bad = _make_result(repo["head"])
        bad["state"] = "CONTINUE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-RESCOPE disposition
        bad = _make_result(repo["head"])
        bad["disposition"] = "HALT"
        bad["category"] = "EXTERNAL_GATE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-INTERNAL_REJECTION category
        bad = _make_result(repo["head"])
        bad["category"] = "NONE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-null commit
        bad = _make_result(repo["head"])
        bad["commit"] = "c" * 40
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-null Gemini evidence
        bad = _make_result(repo["head"])
        bad["gemini_worker_run_id"] = "worker-001"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Changed head
        bad = _make_result(repo["head"])
        bad["head_changed"] = True
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Mismatched head_before and head_after
        bad = _make_result(repo["head"])
        bad["head_after"] = "b" * 40
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Mismatched repository head
        bad = _make_result("e" * 40)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

    def test_parse_rejects_malformed_utf8(self) -> None:
        repo = _make_repo()
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(b"\xff\xfe\x00\x01", repo)

    def test_parse_rejects_incomplete_and_malformed_json(self) -> None:
        repo = _make_repo()
        for bad_bytes in [b"", b"{", b'{"schema_version":', b'{"schema_version": 1,', b"null", b"123"]:
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(bad_bytes, repo)

    def test_parse_rejects_duplicate_keys(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"attempt": 1', valid_bytes)
        dup_bytes = valid_bytes.replace(b'"attempt": 1', b'"attempt": 1, "attempt": 1', 1)
        self.assertNotEqual(dup_bytes, valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(dup_bytes, repo)

    def test_parse_rejects_noncanonical_formatting(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"attempt": 1', valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes + b" ", repo)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes.replace(b'"attempt": 1', b'"attempt":  1'), repo)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes.replace(b'"attempt": 1', b'"attempt":1'), repo)
        if valid_bytes.endswith(b"\n"):
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(valid_bytes[:-1], repo)
        else:
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(valid_bytes + b"\n", repo)

    def test_parse_rejects_unexpected_fields(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"terminal": false', valid_bytes)
        bad_envelope = valid_bytes.replace(b'"terminal": false', b'"terminal": false, "extra": true')
        self.assertNotEqual(bad_envelope, valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(bad_envelope, repo)

    def test_parse_rejects_oversize(self) -> None:
        repo = _make_repo()
        oversized = b" " * (MAX_RESCOPE_RECEIPT_BYTES + 1)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(oversized, repo)

    def test_parse_rejects_stale_expected_repository(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)

        stale_repo = dict(repo)
        stale_repo["head"] = "f" * 40
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes, stale_repo)

        drifted_refs = dict(repo)
        drifted_refs["protected_refs"] = {ref: "9" * 40 for ref in PROTECTED_REFS}
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes, drifted_refs)


class TestAE03RescopeReceiptReader(unittest.TestCase):
    def test_path_validation_rejects_non_path_relative_and_dot_dot(self) -> None:
        repo = _make_repo()
        with self.assertRaises(AutopilotError):
            load_rescope_receipt("not_a_path", repo)  # type: ignore[arg-type]
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(Path("relative/path"), repo)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            dot_dot_path = root / "other" / ".." / "state"
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(dot_dot_path, repo)
            dot_dot_rel = Path("..") / "state"
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(dot_dot_rel, repo)

    def test_absent_receipt_attempts_leaf_os_open(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            opened_names = []
            real_open = os.open

            def trace_open(path, flags, *args, **kwargs):
                if isinstance(path, (str, bytes)):
                    opened_names.append(os.path.basename(path))
                return real_open(path, flags, *args, **kwargs)

            with mock.patch("os.open", side_effect=trace_open):
                result = load_rescope_receipt(state_root, repo)

            self.assertIsNone(result)
            self.assertIn(RESCOPE_RECEIPT_FILENAME, opened_names)

    def test_symlink_swap_between_precheck_and_leaf_open(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME
            raw_bytes = serialize_rescope_receipt(_make_result(), repo, attempt=1)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o600)

            sentinel = root / "sentinel.txt"
            sentinel.write_text("secret sentinel data")

            real_stat = os.stat
            swapped = False

            def intercept_stat(path, *args, **kwargs):
                nonlocal swapped
                st = real_stat(path, *args, **kwargs)
                if path == RESCOPE_RECEIPT_FILENAME and not swapped:
                    receipt_file.unlink()
                    receipt_file.symlink_to(sentinel)
                    swapped = True
                return st

            with mock.patch("os.stat", side_effect=intercept_stat):
                with self.assertRaises(AutopilotError):
                    load_rescope_receipt(state_root, repo)

            self.assertTrue(swapped)
            self.assertEqual(sentinel.read_text(), "secret sentinel data")

    def test_parent_component_swap_detected(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)

            real_open = os.open

            def swap_dir_on_open(path, flags, *args, **kwargs):
                if path == RESCOPE_RECEIPT_FILENAME:
                    state_root.rename(root / "state_old")
                    new_state = root / "state"
                    new_state.mkdir(mode=0o700)
                    new_state.chmod(0o700)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch("os.open", side_effect=swap_dir_on_open):
                with self.assertRaises(AutopilotError):
                    load_rescope_receipt(state_root, repo)

    def test_leaf_attributes_validation(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME
            raw_bytes = serialize_rescope_receipt(_make_result(), repo, attempt=1)

            # Test hardlink (nlink > 1)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o600)
            extra_link = state_root / "extra_link"
            extra_link.hardlink_to(receipt_file)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)
            extra_link.unlink()

            # Test symlink leaf
            receipt_file.unlink()
            target = root / "target"
            target.write_bytes(raw_bytes)
            target.chmod(0o600)
            receipt_file.symlink_to(target)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)
            receipt_file.unlink()

            # Test mode 0644 (not 0600)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o644)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)

            # Test wrong owner (fstat test double)
            receipt_file.chmod(0o600)
            real_fstat = os.fstat

            def wrong_owner_fstat(fd):
                st = real_fstat(fd)
                if stat.S_ISREG(st.st_mode):
                    return os.stat_result((
                        st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                        os.geteuid() + 100, st.st_gid, st.st_size,
                        st.st_atime, st.st_mtime, st.st_ctime,
                    ))
                return st

            with mock.patch("os.fstat", side_effect=wrong_owner_fstat):
                with self.assertRaises(AutopilotError):
                    load_rescope_receipt(state_root, repo)

            # Test oversize
            oversized = b" " * (MAX_RESCOPE_RECEIPT_BYTES + 1)
            receipt_file.write_bytes(oversized)
            receipt_file.chmod(0o600)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)

    def test_leaf_content_and_stale_repository_validation(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME

            # Malformed JSON
            receipt_file.write_bytes(b"not json")
            receipt_file.chmod(0o600)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)

            # Duplicate key JSON
            valid = serialize_rescope_receipt(_make_result(), repo, attempt=1)
            dup = valid.replace(b'"attempt": 1', b'"attempt": 1, "attempt": 1', 1)
            receipt_file.write_bytes(dup)
            receipt_file.chmod(0o600)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)

            # Noncanonical JSON
            receipt_file.write_bytes(valid + b" ")
            receipt_file.chmod(0o600)
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, repo)

            # Stale expected repository
            receipt_file.write_bytes(valid)
            receipt_file.chmod(0o600)
            stale_repo = dict(repo)
            stale_repo["head"] = "f" * 40
            with self.assertRaises(AutopilotError):
                load_rescope_receipt(state_root, stale_repo)

    def test_injected_read_and_close_oserror(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME
            raw_bytes = serialize_rescope_receipt(_make_result(), repo, attempt=1)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o600)

            # Injected os.read OSError
            with mock.patch("os.read", side_effect=OSError(errno.EIO, "injected read failure")):
                with self.assertRaises(AutopilotError) as ctx:
                    load_rescope_receipt(state_root, repo)
                self.assertIn("error reading rescope receipt", str(ctx.exception))

            # Injected os.close OSError on cleanup
            real_close = os.close
            read_done = False

            def failing_close_cleanup(fd):
                if read_done:
                    real_close(fd)
                    raise OSError(errno.EIO, "injected close failure")
                return real_close(fd)

            real_read = os.read

            def trace_read(fd, n):
                nonlocal read_done
                res = real_read(fd, n)
                read_done = True
                return res

            with mock.patch("os.read", side_effect=trace_read):
                with mock.patch("os.close", side_effect=failing_close_cleanup):
                    with self.assertRaises(AutopilotError) as ctx:
                        load_rescope_receipt(state_root, repo)
                    self.assertIn("error closing descriptor", str(ctx.exception))

            # Injected close error preserves primary exception
            def bad_read(*args, **kwargs):
                raise OSError(errno.EIO, "primary read failure")

            with mock.patch("os.read", side_effect=bad_read):
                with mock.patch("os.close", side_effect=failing_close_cleanup):
                    with self.assertRaises(AutopilotError) as ctx:
                        load_rescope_receipt(state_root, repo)
                    self.assertIn("error closing descriptor", str(ctx.exception))
                    self.assertIsInstance(ctx.exception.__cause__, AutopilotError)

    def test_interrupt_propagation_not_suppressed(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME
            raw_bytes = serialize_rescope_receipt(_make_result(), repo, attempt=1)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o600)

            # KeyboardInterrupt during read propagates
            with mock.patch("os.read", side_effect=KeyboardInterrupt("user interrupt")):
                with self.assertRaises(KeyboardInterrupt):
                    load_rescope_receipt(state_root, repo)

            # KeyboardInterrupt with close error does NOT suppress interrupt
            real_close = os.close
            interrupted = False

            def interrupt_read(*args, **kwargs):
                nonlocal interrupted
                interrupted = True
                raise KeyboardInterrupt("user interrupt")

            def failing_close_on_cleanup(fd):
                if interrupted:
                    real_close(fd)
                    raise OSError(errno.EIO, "close failure on interrupt cleanup")
                return real_close(fd)

            with mock.patch("os.read", side_effect=interrupt_read):
                with mock.patch("os.close", side_effect=failing_close_on_cleanup):
                    with self.assertRaises(KeyboardInterrupt):
                        load_rescope_receipt(state_root, repo)

    def test_unsupported_dir_fd_rejected(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            with mock.patch.object(os, "supports_dir_fd", set()):
                with self.assertRaises(AutopilotError):
                    load_rescope_receipt(state_root, repo)

    def test_successful_load_roundtrip(self) -> None:
        repo = _make_repo()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            state_root.chmod(0o700)
            receipt_file = state_root / RESCOPE_RECEIPT_FILENAME
            result = _make_result(repo["head"])
            raw_bytes = serialize_rescope_receipt(result, repo, attempt=1)
            receipt_file.write_bytes(raw_bytes)
            receipt_file.chmod(0o600)

            loaded = load_rescope_receipt(state_root, repo)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["schema_version"], RESCOPE_RECEIPT_SCHEMA_VERSION)
            self.assertEqual(loaded["attempt"], 1)
            self.assertFalse(loaded["terminal"])
            self.assertEqual(loaded["result"]["state"], "BLOCKED")
            self.assertEqual(loaded["result"]["disposition"], "RESCOPE")


class TestAE03RescopeTempWriter(unittest.TestCase):
    def test_success_contents_mode_and_cleanup_by_caller(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                payload = b'{"test": "ae03-temp-writer"}'
                temp_name = _create_rescope_temp_file(dir_fd, payload)
                self.assertTrue(temp_name.startswith("."))

                # Stat verification via dir_fd
                st = os.stat(temp_name, dir_fd=dir_fd, follow_symlinks=False)
                self.assertTrue(stat.S_ISREG(st.st_mode))
                self.assertEqual(st.st_mode & 0o777, 0o600)
                self.assertEqual(st.st_nlink, 1)
                self.assertEqual(st.st_uid, os.geteuid())
                self.assertEqual(st.st_size, len(payload))

                temp_path = state_root / temp_name
                self.assertEqual(temp_path.read_bytes(), payload)

                # Caller removes returned temp; verify no leaked files
                os.unlink(temp_name, dir_fd=dir_fd)
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)

    def test_fail_closed_when_o_cloexec_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(os, "O_CLOEXEC", None, create=False):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"test")
                    self.assertIn("O_CLOEXEC", str(ctx.exception))
            finally:
                os.close(dir_fd)

    def test_temp_open_eacces_preserves_cause_and_no_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                real_open = os.open
                unlink_called = False

                def fail_temp_open(path, flags, *args, **kwargs):
                    if isinstance(path, str) and path.startswith(".rescope_receipt_tmp_"):
                        raise OSError(errno.EACCES, "Permission denied")
                    return real_open(path, flags, *args, **kwargs)

                def record_unlink(*args, **kwargs):
                    nonlocal unlink_called
                    unlink_called = True
                    return os.unlink(*args, **kwargs)

                with mock.patch("os.open", side_effect=fail_temp_open):
                    with mock.patch("os.unlink", side_effect=record_unlink):
                        with self.assertRaises(AutopilotError) as ctx:
                            _create_rescope_temp_file(dir_fd, b"payload")
                        self.assertIn("cannot open temporary rescope file", str(ctx.exception))
                        self.assertIsInstance(ctx.exception.__cause__, OSError)
                        self.assertEqual(ctx.exception.__cause__.errno, errno.EACCES)

                self.assertFalse(unlink_called)
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)

    def test_write_failures_and_short_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                # Failed write with OSError
                with mock.patch("os.write", side_effect=OSError(errno.EIO, "I/O error")):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("error writing temporary rescope file", str(ctx.exception))
                    self.assertIsInstance(ctx.exception.__cause__, OSError)
                    self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)
                self.assertEqual(list(state_root.iterdir()), [])

                # Zero write count error
                with mock.patch("os.write", return_value=0):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("zero or negative write count", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])

                # Negative write count error
                with mock.patch("os.write", return_value=-1):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("zero or negative write count", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])

                # Short write loop completion
                real_write = os.write

                def bounded_short_write(fd, data):
                    chunk = data[:2]
                    return real_write(fd, chunk)

                with mock.patch("os.write", side_effect=bounded_short_write):
                    payload = b"abcdefghij"
                    temp_name = _create_rescope_temp_file(dir_fd, payload)
                    self.assertEqual((state_root / temp_name).read_bytes(), payload)
                    os.unlink(temp_name, dir_fd=dir_fd)
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)

    def test_fsync_failure_unlinks_temp_and_preserves_cause(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fsync failure")):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("cannot fsync temporary rescope file", str(ctx.exception))
                    self.assertIsInstance(ctx.exception.__cause__, OSError)
                    self.assertEqual(ctx.exception.__cause__.errno, errno.EIO)
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)

    def test_cleanup_close_and_unlink_failures_with_chained_original(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                # Cleanup close failure chains original write failure
                real_close = os.close
                write_failed = False

                def fail_write(*args, **kwargs):
                    nonlocal write_failed
                    write_failed = True
                    raise OSError(errno.EIO, "primary write error")

                def fail_cleanup_close(fd):
                    if write_failed:
                        real_close(fd)
                        raise OSError(errno.EBADF, "cleanup close error")
                    return real_close(fd)

                with mock.patch("os.write", side_effect=fail_write):
                    with mock.patch("os.close", side_effect=fail_cleanup_close):
                        with self.assertRaises(AutopilotError) as ctx:
                            _create_rescope_temp_file(dir_fd, b"payload")
                        self.assertIn("error cleaning up temporary rescope file", str(ctx.exception))
                        self.assertIsInstance(ctx.exception.__cause__, AutopilotError)
                        self.assertIn("error writing temporary rescope file", str(ctx.exception.__cause__))

                # Cleanup unlink failure chains original fsync failure
                def fail_cleanup_unlink(path, *args, **kwargs):
                    raise OSError(errno.EACCES, "cleanup unlink error")

                with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "primary fsync error")):
                    with mock.patch("os.unlink", side_effect=fail_cleanup_unlink):
                        with self.assertRaises(AutopilotError) as ctx:
                            _create_rescope_temp_file(dir_fd, b"payload")
                        self.assertIn("error cleaning up temporary rescope file", str(ctx.exception))
                        self.assertIsInstance(ctx.exception.__cause__, AutopilotError)
                        self.assertIn("cannot fsync temporary rescope file", str(ctx.exception.__cause__))
            finally:
                os.close(dir_fd)

    def test_interrupt_propagation_and_temp_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                # KeyboardInterrupt propagation
                with mock.patch("os.write", side_effect=KeyboardInterrupt("user interrupt")):
                    with self.assertRaises(KeyboardInterrupt):
                        _create_rescope_temp_file(dir_fd, b"payload")
                self.assertEqual(list(state_root.iterdir()), [])

                # SystemExit propagation
                with mock.patch("os.write", side_effect=SystemExit(2)):
                    with self.assertRaises(SystemExit) as exit_ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertEqual(exit_ctx.exception.code, 2)
                self.assertEqual(list(state_root.iterdir()), [])

                # KeyboardInterrupt with cleanup close error does not suppress interrupt
                real_close = os.close
                interrupted = False

                def interrupt_write(*args, **kwargs):
                    nonlocal interrupted
                    interrupted = True
                    raise KeyboardInterrupt("user interrupt")

                def failing_close(fd):
                    if interrupted:
                        real_close(fd)
                        raise OSError(errno.EIO, "cleanup close error on interrupt")
                    return real_close(fd)

                with mock.patch("os.write", side_effect=interrupt_write):
                    with mock.patch("os.close", side_effect=failing_close):
                        with self.assertRaises(KeyboardInterrupt):
                            _create_rescope_temp_file(dir_fd, b"payload")
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)

    def test_invalid_payload_and_dir_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                # Invalid payload types
                for bad_payload in ["str", 123, 12.5, None, [b"bytes"], bytearray(b"x")]:
                    with self.assertRaises(AutopilotError):
                        _create_rescope_temp_file(dir_fd, bad_payload)  # type: ignore[arg-type]

                # Empty payload
                with self.assertRaises(AutopilotError):
                    _create_rescope_temp_file(dir_fd, b"")

                # Oversized payload
                with self.assertRaises(AutopilotError):
                    _create_rescope_temp_file(dir_fd, b"a" * (MAX_RESCOPE_RECEIPT_BYTES + 1))

                # Invalid pinned_dir_fd types and values
                for bad_fd in ["3", None, True, False, 1.5, -1]:
                    with self.assertRaises(AutopilotError):
                        _create_rescope_temp_file(bad_fd, b"valid")  # type: ignore[arg-type]

                # pinned_dir_fd not a directory
                reg_file = state_root / "reg_file"
                reg_file.write_bytes(b"content")
                reg_fd = os.open(str(reg_file), os.O_RDONLY)
                try:
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(reg_fd, b"valid")
                    self.assertIn("not a directory", str(ctx.exception))
                finally:
                    os.close(reg_fd)

                # pinned_dir_fd mode != 0700
                state_root.chmod(0o755)
                with self.assertRaises(AutopilotError) as ctx:
                    _create_rescope_temp_file(dir_fd, b"valid")
                self.assertIn("mode must be 0700", str(ctx.exception))
                state_root.chmod(0o700)

                # pinned_dir_fd owner != euid
                real_fstat = os.fstat

                def foreign_owner_fstat(fd):
                    st = real_fstat(fd)
                    return os.stat_result((
                        st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                        os.geteuid() + 100, st.st_gid, st.st_size,
                        st.st_atime, st.st_mtime, st.st_ctime,
                    ))

                with mock.patch("os.fstat", side_effect=foreign_owner_fstat):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"valid")
                    self.assertIn("owner must be process euid", str(ctx.exception))

                # Missing platform dir_fd support
                with mock.patch.object(os, "supports_dir_fd", set()):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"valid")
                    self.assertIn("missing dir_fd support", str(ctx.exception))

                # Missing platform O_NOFOLLOW / O_DIRECTORY
                with mock.patch.object(os, "O_NOFOLLOW", None, create=False):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"valid")
                    self.assertIn("O_NOFOLLOW", str(ctx.exception))
                with mock.patch.object(os, "O_DIRECTORY", None, create=False):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"valid")
                    self.assertIn("O_DIRECTORY", str(ctx.exception))
            finally:
                os.close(dir_fd)

    def test_opened_temp_validation_defects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_root = Path(td).resolve()
            state_root.chmod(0o700)
            dir_fd = os.open(str(state_root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                real_fstat = os.fstat

                # Opened temp mode != 0600
                def bad_mode_fstat(fd):
                    st = real_fstat(fd)
                    if stat.S_ISREG(st.st_mode):
                        return os.stat_result((
                            (st.st_mode & ~0o777) | 0o644, st.st_ino, st.st_dev, st.st_nlink,
                            st.st_uid, st.st_gid, st.st_size,
                            st.st_atime, st.st_mtime, st.st_ctime,
                        ))
                    return st

                with mock.patch("os.fstat", side_effect=bad_mode_fstat):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("mode must be exactly 0600", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])

                # Opened temp link count != 1
                def bad_nlink_fstat(fd):
                    st = real_fstat(fd)
                    if stat.S_ISREG(st.st_mode):
                        return os.stat_result((
                            st.st_mode, st.st_ino, st.st_dev, 2,
                            st.st_uid, st.st_gid, st.st_size,
                            st.st_atime, st.st_mtime, st.st_ctime,
                        ))
                    return st

                with mock.patch("os.fstat", side_effect=bad_nlink_fstat):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("link count must be 1", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])

                # Opened temp owner != euid
                def bad_owner_fstat(fd):
                    st = real_fstat(fd)
                    if stat.S_ISREG(st.st_mode):
                        return os.stat_result((
                            st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                            os.geteuid() + 10, st.st_gid, st.st_size,
                            st.st_atime, st.st_mtime, st.st_ctime,
                        ))
                    return st

                with mock.patch("os.fstat", side_effect=bad_owner_fstat):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("owner must be process euid", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])

                # Opened temp not regular
                def non_reg_fstat(fd):
                    st = real_fstat(fd)
                    if stat.S_ISREG(st.st_mode):
                        return os.stat_result((
                            stat.S_IFDIR | 0o600, st.st_ino, st.st_dev, st.st_nlink,
                            st.st_uid, st.st_gid, st.st_size,
                            st.st_atime, st.st_mtime, st.st_ctime,
                        ))
                    return st

                with mock.patch("os.fstat", side_effect=non_reg_fstat):
                    with self.assertRaises(AutopilotError) as ctx:
                        _create_rescope_temp_file(dir_fd, b"payload")
                    self.assertIn("not a regular file", str(ctx.exception))
                self.assertEqual(list(state_root.iterdir()), [])
            finally:
                os.close(dir_fd)


if __name__ == "__main__":
    unittest.main()
