"""Unit and fault characterization tests for final acknowledgement storage."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import json
from pathlib import Path
import stat
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from scripts.two_site_quota_accepted_lineage_storage import (
    publish_accepted_lineage_record_candidate,
)
from scripts.two_site_quota_final_ack_record import (
    MAX_FINAL_ACK_RECORD_BYTES,
    _bounded_canonical_bytes,
)
import scripts.two_site_quota_final_ack_storage as ack_storage_mod
from scripts.two_site_quota_final_ack_storage import (
    FINAL_ACK_FILENAME,
    FinalAckStorageAmbiguousPostPublicationError,
    FinalAckStorageError,
    publish_final_ack_record_candidate,
    read_final_ack_record_candidate,
)
from tests.test_two_site_quota_accepted_lineage_storage import (
    _create_cycle_dir,
    _make_fixtures,
)


def _make_complete_fixtures(
    run_id: str = "cycle-0001-20260929T120000Z-123",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    exp, raw, ctx, up = _make_fixtures(run_id)
    head = exp["head_before"]
    for d in (exp, raw):
        d.update({
            "state": "COMPLETE",
            "commit": None,
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
        })
    exp["head_after"] = head
    exp["head_changed"] = False
    ctx["current_head"] = head
    return exp, raw, ctx, up


def _setup_cycle_fixture(
    root: Path,
    run_id: str,
    exp: dict[str, Any],
    raw: dict[str, Any],
    ctx: dict[str, Any],
    up: dict[str, Any],
) -> tuple[str, str, Path]:
    cycle_dir = _create_cycle_dir(root, run_id)
    result_path = cycle_dir / "result.json"
    result_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    os.chmod(result_path, 0o644)
    publish_accepted_lineage_record_candidate(
        root=root,
        continuation_controller_run_id=run_id,
        expected_transition_result=exp,
        acceptance_context=ctx,
        upstream=up,
    )
    return up["original_controller_run_id"], run_id, cycle_dir


def _init_env(temp_dir: str, complete: bool = False):
    root = Path(temp_dir)
    cont_id = "cycle-0001-20260929T120000Z-123"
    exp, raw, ctx, up = (
        _make_complete_fixtures(cont_id) if complete else _make_fixtures(cont_id)
    )
    orig_id, _, cycle_dir = _setup_cycle_fixture(root, cont_id, exp, raw, ctx, up)
    return root, orig_id, cont_id, exp, ctx, up, cycle_dir


def _publish_ack(
    root: Path,
    orig_id: str,
    cont_id: str,
    exp: dict[str, Any],
    ctx: dict[str, Any],
    up: dict[str, Any],
    ack_time: str = "2026-09-29T12:05:00Z",
) -> dict[str, Any]:
    return publish_final_ack_record_candidate(
        root=root,
        original_controller_run_id=orig_id,
        continuation_controller_run_id=cont_id,
        expected_transition_result=exp,
        acceptance_context=ctx,
        upstream=up,
        acknowledged_at_utc=ack_time,
    )


class TestTwoSiteQuotaFinalAckStorage(unittest.TestCase):
    def test_positive_continue_publication_and_readback(self) -> None:
        ack_time = "2026-09-29T12:05:00Z"
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            pub = _publish_ack(root, orig_id, cont_id, exp, ctx, up, ack_time)
            self.assertEqual(pub["accepted_state"], "CONTINUE")
            self.assertEqual(pub["original_controller_run_id"], orig_id)
            self.assertEqual(pub["continuation_controller_run_id"], cont_id)
            self.assertEqual(pub["acknowledged_at_utc"], ack_time)

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())
            st = os.stat(target)
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_nlink, 1)
            self.assertEqual(st.st_uid, os.geteuid())
            self.assertGreater(st.st_size, 0)
            self.assertLessEqual(st.st_size, MAX_FINAL_ACK_RECORD_BYTES)

            exp_bytes = _bounded_canonical_bytes(pub, MAX_FINAL_ACK_RECORD_BYTES)
            self.assertEqual(target.read_bytes(), exp_bytes)

            readback = read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertEqual(readback, pub)

            readback["accepted_state"] = "MUTATED"
            readback2 = read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertEqual(readback2["accepted_state"], "CONTINUE")
            self.assertEqual([f for f in os.listdir(cycle_dir) if f.startswith(".")], [])

    def test_positive_complete_publication_and_readback(self) -> None:
        ack_time = "2026-09-29T12:05:00Z"
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, _ = _init_env(td, complete=True)
            pub = _publish_ack(root, orig_id, cont_id, exp, ctx, up, ack_time)
            self.assertEqual(pub["accepted_state"], "COMPLETE")
            self.assertEqual(pub["head_after"], exp["head_before"])
            readback = read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertEqual(readback, pub)

    def test_duplicate_publication_fails_closed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            _publish_ack(root, orig_id, cont_id, exp, ctx, up)
            target = cycle_dir / FINAL_ACK_FILENAME
            first_bytes = target.read_bytes()
            first_st = os.stat(target)

            with self.assertRaises(FinalAckStorageError) as err:
                _publish_ack(root, orig_id, cont_id, exp, ctx, up, "2026-09-29T12:06:00Z")
            self.assertIn("already exists", str(err.exception))
            self.assertEqual(target.read_bytes(), first_bytes)
            second_st = os.stat(target)
            self.assertEqual(second_st.st_ino, first_st.st_ino)
            self.assertEqual(second_st.st_mtime_ns, first_st.st_mtime_ns)

    def test_readback_mode_link_symlink_size_corrupt_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            _publish_ack(root, orig_id, cont_id, exp, ctx, up)
            target = cycle_dir / FINAL_ACK_FILENAME
            canonical_content = target.read_bytes()

            # 1. Mode 0644 rejected
            os.chmod(target, 0o644)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("mode must be 0600", str(err.exception))
            os.chmod(target, 0o600)

            # 2. Hardlink count > 1 rejected
            alias = cycle_dir / "alias.json"
            os.link(target, alias)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("link count", str(err.exception))
            os.unlink(alias)

            # 3. Symlink rejected
            real_target = cycle_dir / "real_ack.json"
            os.rename(target, real_target)
            os.symlink(real_target, target)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("not a regular file", str(err.exception))
            os.unlink(target)
            os.rename(real_target, target)

            # 4. Non-canonical JSON formatting rejected
            reformatted = canonical_content.decode("utf-8").replace(",", ", ")
            target.write_text(reformatted, encoding="utf-8")
            os.chmod(target, 0o600)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("not canonical JSON bytes", str(err.exception))

            # 5. Oversize rejected (> 2048 bytes)
            target.write_bytes(b"{" + b" " * 2050 + b"}")
            os.chmod(target, 0o600)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("exceeds byte limit", str(err.exception))

            # 6. Corrupt hash rejected
            data = json.loads(canonical_content.decode("utf-8"))
            data["result_sha256"] = "f" * 64
            target.write_bytes(_bounded_canonical_bytes(data, MAX_FINAL_ACK_RECORD_BYTES))
            os.chmod(target, 0o600)
            with self.assertRaises(FinalAckStorageError) as err:
                read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertIn("validation failed", str(err.exception))

    def test_pre_link_fault_leaves_no_target_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            real_fsync = os.fsync

            def _fake_fsync_temp(fd: int) -> None:
                st = os.fstat(fd)
                if stat.S_ISREG(st.st_mode) and (stat.S_IMODE(st.st_mode) == 0o600):
                    raise OSError(5, "simulated temp fsync error")
                real_fsync(fd)

            with patch("scripts.two_site_quota_final_ack_storage.os.fsync", side_effect=_fake_fsync_temp):
                with self.assertRaises(FinalAckStorageError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("cannot fsync", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertFalse(target.exists())
            self.assertEqual([f for f in os.listdir(cycle_dir) if f.startswith(".")], [])

    def test_post_link_fsync_fault_retains_target_and_raises_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            real_fsync = os.fsync

            def _fake_fsync_dir(fd: int) -> None:
                is_dir = False
                try:
                    is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                except OSError:
                    pass
                if is_dir:
                    raise OSError(5, "simulated directory fsync failure")
                real_fsync(fd)

            with patch("scripts.two_site_quota_final_ack_storage.os.fsync", side_effect=_fake_fsync_dir):
                with self.assertRaises(FinalAckStorageAmbiguousPostPublicationError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("cannot fsync directory", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)

    def test_post_link_directory_fsync_keyboard_interrupt_retains_target_and_raises_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            real_fsync = os.fsync

            def _fake_dir_fsync_interrupt(fd: int) -> None:
                is_dir = False
                try:
                    is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                except OSError:
                    pass
                if is_dir:
                    raise KeyboardInterrupt("simulated directory fsync interrupt")
                real_fsync(fd)

            with patch("scripts.two_site_quota_final_ack_storage.os.fsync", side_effect=_fake_dir_fsync_interrupt):
                with self.assertRaises(FinalAckStorageAmbiguousPostPublicationError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("cannot fsync directory", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())
            target_bytes = target.read_bytes()
            self.assertGreater(len(target_bytes), 0)

            readback = read_final_ack_record_candidate(root, orig_id, cont_id, exp, ctx, up)
            self.assertEqual(target_bytes, _bounded_canonical_bytes(readback, MAX_FINAL_ACK_RECORD_BYTES))

            with self.assertRaises(FinalAckStorageError) as retry_err:
                _publish_ack(root, orig_id, cont_id, exp, ctx, up, "2026-09-29T12:06:00Z")
            self.assertIn("already exists", str(retry_err.exception))
            self.assertEqual(target.read_bytes(), target_bytes)

    def test_uncertain_link_after_actual_link_retains_target_and_raises_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            real_link = os.link

            def _fake_link_interrupt(*args: Any, **kwargs: Any) -> None:
                real_link(*args, **kwargs)
                raise KeyboardInterrupt("simulated link interrupt")

            with patch("scripts.two_site_quota_final_ack_storage.os.link", side_effect=_fake_link_interrupt):
                with self.assertRaises(FinalAckStorageAmbiguousPostPublicationError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("os.link status uncertain", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())
            target_bytes = target.read_bytes()
            self.assertGreater(len(target_bytes), 0)

            with self.assertRaises(FinalAckStorageError) as retry_err:
                _publish_ack(root, orig_id, cont_id, exp, ctx, up, "2026-09-29T12:06:00Z")
            self.assertIn("already exists", str(retry_err.exception))
            self.assertEqual(target.read_bytes(), target_bytes)

    def test_post_link_readback_fault_retains_target_and_raises_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            with patch(
                "scripts.two_site_quota_final_ack_storage.read_final_ack_record_candidate",
                side_effect=FinalAckStorageError("simulated readback failure"),
            ):
                with self.assertRaises(FinalAckStorageAmbiguousPostPublicationError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("ambiguous post-link publication failure", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)

    def test_accepted_lineage_source_drift_fails_closed_and_retains_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, cycle_dir = _init_env(td)
            calls = 0
            real_read_lineage = ack_storage_mod.read_accepted_lineage_record_candidate

            def _drifted_read_lineage(*args: Any, **kwargs: Any) -> Any:
                nonlocal calls
                calls += 1
                res = real_read_lineage(*args, **kwargs)
                if calls > 1:
                    res = copy.deepcopy(res)
                    res["result_sha256"] = "0" * 64
                return res

            with patch(
                "scripts.two_site_quota_final_ack_storage.read_accepted_lineage_record_candidate",
                side_effect=_drifted_read_lineage,
            ):
                with self.assertRaises(FinalAckStorageAmbiguousPostPublicationError) as err:
                    _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertIn("drifted after publication", str(err.exception))

            target = cycle_dir / FINAL_ACK_FILENAME
            self.assertTrue(target.is_file())

    def test_id_cross_check_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, _ = _init_env(td)
            wrong_orig_id = "cycle-0003-20260929T130000Z-999"
            with self.assertRaises(FinalAckStorageError) as err:
                publish_final_ack_record_candidate(
                    root=root,
                    original_controller_run_id=wrong_orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    acceptance_context=ctx,
                    upstream=up,
                    acknowledged_at_utc="2026-09-29T12:05:00Z",
                )
            self.assertIn("original_controller_run_id", str(err.exception))

            with self.assertRaises(FinalAckStorageError):
                read_final_ack_record_candidate(
                    root=root,
                    original_controller_run_id=wrong_orig_id,
                    continuation_controller_run_id=cont_id,
                    expected_transition_result=exp,
                    acceptance_context=ctx,
                    upstream=up,
                )

    def test_dormant_no_live_network_or_process_authority(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, orig_id, cont_id, exp, ctx, up, _ = _init_env(td)
            with patch("subprocess.run", side_effect=AssertionError("subprocess.run forbidden")),	\
                 patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen forbidden")),	\
                 patch("socket.socket", side_effect=AssertionError("socket forbidden")):
                pub = _publish_ack(root, orig_id, cont_id, exp, ctx, up)
                self.assertEqual(pub["accepted_state"], "CONTINUE")
                readback = read_final_ack_record_candidate(
                    root, orig_id, cont_id, exp, ctx, up
                )
                self.assertEqual(readback["accepted_state"], "CONTINUE")


if __name__ == "__main__":
    unittest.main()
