import io
import json
import logging
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.audit_logger import audit_log
from core.audit_sink import write_audit_record
from core.log_redaction import REDACTED
from core.logging_config import configure_logging
from core.request_context import clear_request_context, set_request_context


def _write_concurrent_audit_batch(path: str, worker_id: int, count: int) -> None:
    with patch("core.audit_sink._audit_trail_path", return_value=path):
        for index in range(count):
            write_audit_record({
                "action": "test.concurrent",
                "worker_id": worker_id,
                "index": index,
            })


class AuditLoggerTests(unittest.TestCase):
    def tearDown(self):
        clear_request_context()
        logging.getLogger().handlers.clear()

    def test_audit_log_uses_strict_schema_and_request_context(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        set_request_context(request_id="req-audit-1", actor_id=42, actor_role="مدیر ارشد", client_ip="127.0.0.1")
        with patch("core.audit_sink._audit_trail_path", return_value=None):
            audit_log(
                "user.update",
                target_type="user",
                target_id=7,
                before_summary={"role": "عادی"},
                after_summary={"role": "مدیر میانی", "password": "new-secret"},
                reason="operator requested password=unsafe",
            )

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["logger"], "audit")
        self.assertEqual(payload["service"], "audit-test")
        self.assertEqual(payload["event"], "audit.user.update")
        self.assertEqual(payload["log_class"], "audit")
        self.assertTrue(payload["audit"])
        self.assertEqual(payload["action"], "user.update")
        self.assertEqual(payload["target_type"], "user")
        self.assertEqual(payload["target_id"], 7)
        self.assertEqual(payload["result"], "success")
        self.assertEqual(payload["request_id"], "req-audit-1")
        self.assertEqual(payload["client_ip"], "127.0.0.1")
        self.assertEqual(payload["actor_id"], 42)
        self.assertEqual(payload["actor_role"], "مدیر ارشد")
        self.assertFalse(payload["audit_durable"])
        self.assertEqual(payload["audit_durable_reason"], "audit_trail_unconfigured")
        self.assertEqual(payload["after_summary"]["password"], REDACTED)
        self.assertNotIn("new-secret", stream.getvalue())
        self.assertNotIn("unsafe", stream.getvalue())

    def test_audit_log_allows_explicit_actor_and_normalizes_result(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        set_request_context(actor_id=1, actor_role="ignored")
        with patch("core.audit_sink._audit_trail_path", return_value=None):
            audit_log(
                "session.reject",
                target_type="session_login_request",
                target_id="request-1",
                result="unexpected",
                actor_id=99,
                actor_role="مدیر میانی",
                extra={"refresh_token": "secret-refresh", "status_code": 403},
            )

        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["actor_id"], 99)
        self.assertEqual(payload["actor_role"], "مدیر میانی")
        self.assertEqual(payload["result"], "failure")
        self.assertEqual(payload["audit_extra"]["refresh_token"], REDACTED)
        self.assertEqual(payload["audit_extra"]["status_code"], 403)
        self.assertNotIn("secret-refresh", stream.getvalue())

    def test_audit_log_writes_durable_hash_chain_when_configured(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = f"{tmpdir}/audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=audit_path):
                audit_log(
                    "user.password_change",
                    target_type="user",
                    target_id=10,
                    result="success",
                    actor_id=1,
                    after_summary={"password": "raw-secret"},
                )
                audit_log(
                    "session.reset",
                    target_type="session",
                    target_id="sess-1",
                    result="denied",
                    actor_id=1,
                    reason="token=unsafe",
                )

            lines = [json.loads(line) for line in Path(audit_path).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(lines), 2)
        self.assertIsNone(lines[0]["previous_hash"])
        self.assertTrue(lines[0]["audit_durable"])
        self.assertEqual(lines[1]["previous_hash"], lines[0]["event_hash"])
        self.assertEqual(lines[0]["payload"]["after_summary"]["password"], REDACTED)
        self.assertNotIn("raw-secret", repr(lines))
        self.assertNotIn("unsafe", repr(lines))

        stdout_payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(stdout_payloads[0]["audit_event_hash"], lines[0]["event_hash"])
        self.assertTrue(stdout_payloads[0]["audit_durable"])
        self.assertEqual(stdout_payloads[0]["audit_trail_path"], audit_path)
        self.assertEqual(stdout_payloads[1]["audit_previous_hash"], lines[0]["event_hash"])

    def test_audit_log_marks_non_durable_fallback_when_sink_write_fails(self):
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            configure_logging("audit-test")

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit-dir"
            audit_dir.mkdir()
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_dir)):
                audit_log(
                    "user.delete",
                    target_type="user",
                    target_id=99,
                    result="failure",
                    actor_id=1,
                )

        payload = json.loads(stream.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload["audit_durable"])
        self.assertEqual(payload["audit_durable_reason"], "audit_trail_write_failed")
        self.assertEqual(payload["audit_durable_error_type"], "IsADirectoryError")

    def test_partial_tail_fails_closed_without_appending_or_restarting_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                first = write_audit_record({"action": "test.first"})
                with audit_path.open("ab") as handle:
                    handle.write(b'{"truncated":')
                corrupted = audit_path.read_bytes()

                rejected = write_audit_record({"action": "test.rejected"})

            self.assertTrue(first["audit_durable"])
            self.assertFalse(rejected["audit_durable"])
            self.assertEqual(
                rejected["audit_durable_reason"],
                "audit_trail_integrity_failed",
            )
            self.assertEqual(rejected["audit_durable_error_type"], "AuditTrailIntegrityError")
            self.assertEqual(audit_path.read_bytes(), corrupted)

    def test_invalid_or_tampered_final_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                write_audit_record({"action": "test.first"})
                record = json.loads(audit_path.read_text(encoding="utf-8"))
                record["payload"]["action"] = "tampered"
                audit_path.write_text(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                corrupted = audit_path.read_bytes()

                rejected = write_audit_record({"action": "test.rejected"})

            self.assertFalse(rejected["audit_durable"])
            self.assertEqual(
                rejected["audit_durable_reason"],
                "audit_trail_integrity_failed",
            )
            self.assertEqual(audit_path.read_bytes(), corrupted)

    def test_fsync_failure_returns_non_durable_and_rolls_back_append(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                write_audit_record({"action": "test.first"})
                original = audit_path.read_bytes()
                real_fsync = os.fsync
                calls = 0

                def fail_once(file_descriptor):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise OSError("fsync failed")
                    return real_fsync(file_descriptor)

                with patch("core.audit_sink.os.fsync", side_effect=fail_once):
                    rejected = write_audit_record({"action": "test.rejected"})

            self.assertFalse(rejected["audit_durable"])
            self.assertEqual(rejected["audit_durable_reason"], "audit_trail_write_failed")
            self.assertEqual(rejected["audit_durable_error_type"], "OSError")
            self.assertEqual(audit_path.read_bytes(), original)

    def test_zero_progress_write_returns_non_durable_without_changing_existing_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)):
                write_audit_record({"action": "test.first"})
                original = audit_path.read_bytes()
                with patch("core.audit_sink.os.write", return_value=0):
                    rejected = write_audit_record({"action": "test.rejected"})

            self.assertFalse(rejected["audit_durable"])
            self.assertEqual(rejected["audit_durable_reason"], "audit_trail_write_failed")
            self.assertEqual(audit_path.read_bytes(), original)

    def test_first_file_directory_fsync_failure_removes_uncommitted_trail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            real_fsync = os.fsync
            calls = 0

            def fail_directory_sync_once(file_descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("directory fsync failed")
                return real_fsync(file_descriptor)

            with patch("core.audit_sink._audit_trail_path", return_value=str(audit_path)), patch(
                "core.audit_sink.os.fsync",
                side_effect=fail_directory_sync_once,
            ):
                rejected = write_audit_record({"action": "test.rejected"})

            self.assertFalse(rejected["audit_durable"])
            self.assertEqual(rejected["audit_durable_reason"], "audit_trail_write_failed")
            self.assertFalse(audit_path.exists())

    def test_durable_hash_chain_is_linear_across_multiple_processes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = str(Path(tmpdir) / "audit.jsonl")
            context = multiprocessing.get_context("fork")
            processes = [
                context.Process(
                    target=_write_concurrent_audit_batch,
                    args=(audit_path, worker_id, 20),
                )
                for worker_id in range(4)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)

            records = [
                json.loads(line)
                for line in Path(audit_path).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(records), 80)
        self.assertIsNone(records[0]["previous_hash"])
        for previous, current in zip(records, records[1:]):
            self.assertEqual(current["previous_hash"], previous["event_hash"])
        self.assertEqual(len({record["event_hash"] for record in records}), 80)


if __name__ == "__main__":
    unittest.main()
