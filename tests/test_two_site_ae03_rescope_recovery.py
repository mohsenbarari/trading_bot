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


if __name__ == "__main__":
    unittest.main()
