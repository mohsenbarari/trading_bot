#!/usr/bin/env python3
"""Hermetic tests for Opus failure receipt loading and runner fallback routing."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_proposal_runner as runner
from tests.two_site_generation_fixture import install_current_contract, supported_profile
from scripts.two_site_worker_model_routing import (
    OPUS_EFFORT,
    OPUS_MODEL,
    WorkerRoutingPolicy,
    compute_task_bucket,
)
from tests.test_two_site_opus_fallback_receipt import (
    _make_snapshot_dict,
    _make_status_dict,
    _make_stream_bytes,
)

ENABLED_POLICY = WorkerRoutingPolicy(
    schema_version="1.0",
    enabled=True,
    percent=22,
    model=OPUS_MODEL,
    effort=OPUS_EFFORT,
)
TASK_ID_OPUS = next(
    f"TASK-OPUS-{i:04d}"
    for i in range(1000)
    if compute_task_bucket(f"TASK-OPUS-{i:04d}") < 22
)
TASK_ID_GEMINI = next(
    f"TASK-GEMINI-{i:04d}"
    for i in range(1000)
    if compute_task_bucket(f"TASK-GEMINI-{i:04d}") >= 22
)


def _write_evidence(
    failed_dir: Path,
    failed_id: str,
    task: dict,
    sources: dict,
    head: str,
    controller_id: str,
) -> tuple[bytes, bytes, bytes]:
    failed_dir.mkdir(parents=True, exist_ok=True)
    status_raw = json.dumps(
        _make_status_dict(
            run_id=failed_id,
            task_id=task["task_id"],
            head=head,
            controller_run_id=controller_id,
        )
    ).encode("utf-8")
    task_raw = json.dumps(_make_snapshot_dict(task, sources, head)).encode("utf-8")
    stream_raw = _make_stream_bytes()
    for name, content in (
        ("status.json", status_raw),
        ("task.json", task_raw),
        ("stream.jsonl", stream_raw),
    ):
        target = failed_dir / name
        if target.is_symlink() or target.exists():
            target.unlink()
        target.write_bytes(content)
    return status_raw, task_raw, stream_raw


class TestLoadBoundOpusFallbackReceipt(unittest.TestCase):
    """Hermetic tests for receipt validation, isolation, symlinks, and size limits."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.ctrl_id = "ctrl-receipt-001"
        self.failed_id = "run-opus-failed-001"
        self.head = "0655392de0594432d41a73641e5134376b4c9e43"
        self.task = {
            "task_id": "TASK-OPUS-001",
            "goal": "Hermetic receipt test",
            "context_files": [],
            "allowed_files": ["file.py"],
            "requirements": ["1. Test."],
            "test_selectors": ["tests.test_two_site_opus_runner_fallback"],
        }
        self.sources = {
            "file.py": {
                "sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
                "content": "old_file_content\n",
            }
        }
        self.failed_dir = self.root / ".local/two-site-refactor/proposals" / self.failed_id
        self._write_baseline()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_baseline(self) -> tuple[bytes, bytes, bytes]:
        return _write_evidence(
            self.failed_dir,
            self.failed_id,
            self.task,
            self.sources,
            self.head,
            self.ctrl_id,
        )

    def test_authentic_and_foreign_controller(self) -> None:
        """Authentic receipt is SHA-bound; foreign or empty controller is rejected."""
        status_raw, task_raw, stream_raw = self._write_baseline()
        receipt = runner._load_bound_opus_fallback_receipt(
            self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
        )
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(receipt["reason"], "FORBIDDEN_TOOL_ATTEMPT")
        self.assertEqual(receipt["run_id"], self.failed_id)
        self.assertEqual(receipt["task_id"], self.task["task_id"])
        self.assertEqual(receipt["head"], self.head)
        self.assertEqual(receipt["status_sha256"], hashlib.sha256(status_raw).hexdigest())
        self.assertEqual(receipt["task_sha256"], hashlib.sha256(task_raw).hexdigest())
        self.assertEqual(receipt["stream_sha256"], hashlib.sha256(stream_raw).hexdigest())

        # Foreign controller rejected
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, "foreign-ctrl"
            )
        self.assertIn("failed run controller run ID does not match current controller", str(cm.exception))

        # Empty and whitespace controller rejected
        for bad_ctrl in ["", "   "]:
            with self.assertRaises(runner.ProposalError) as cm:
                runner._load_bound_opus_fallback_receipt(
                    self.root, self.failed_id, self.task, self.sources, self.head, bad_ctrl
                )
            self.assertIn("nonempty controller ID required for fallback", str(cm.exception))

        # Invalid failed run ID rejected
        for bad_id in ["invalid/id", "", "   ", "a" * 81]:
            with self.assertRaises(runner.ProposalError) as cm:
                runner._load_bound_opus_fallback_receipt(
                    self.root, bad_id, self.task, self.sources, self.head, self.ctrl_id
                )
            self.assertIn("invalid failed run ID", str(cm.exception))

    def test_tampered_status_task_stream(self) -> None:
        """Mutated status, task, or stream independently rejected starting from authentic baseline."""
        # Bad status role
        self._write_baseline()
        bad_status = _make_status_dict(
            run_id=self.failed_id, task_id=self.task["task_id"], head=self.head,
            controller_run_id=self.ctrl_id, role="verifier",
        )
        (self.failed_dir / "status.json").write_bytes(json.dumps(bad_status).encode("utf-8"))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("role 'worker'", str(cm.exception))

        # Bad status state
        self._write_baseline()
        bad_status = _make_status_dict(
            run_id=self.failed_id, task_id=self.task["task_id"], head=self.head,
            controller_run_id=self.ctrl_id, state="SUCCESS",
        )
        (self.failed_dir / "status.json").write_bytes(json.dumps(bad_status).encode("utf-8"))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("state 'BLOCKED'", str(cm.exception))

        # Bad status head
        self._write_baseline()
        bad_status = _make_status_dict(
            run_id=self.failed_id, task_id=self.task["task_id"], head="bad-head",
            controller_run_id=self.ctrl_id,
        )
        (self.failed_dir / "status.json").write_bytes(json.dumps(bad_status).encode("utf-8"))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("status head", str(cm.exception))

        # Bad task in snapshot
        self._write_baseline()
        bad_snap = _make_snapshot_dict(dict(self.task, task_id="TASK-DIFF-001"), self.sources, self.head)
        (self.failed_dir / "task.json").write_bytes(json.dumps(bad_snap).encode("utf-8"))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("snapshot task does not match", str(cm.exception))

        # Bad stream without qualifying forbidden tool attempt
        self._write_baseline()
        (self.failed_dir / "stream.jsonl").write_bytes(_make_stream_bytes(tool_name="finish"))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("no qualifying explicit forbidden tool evidence", str(cm.exception))

    def test_symlink_and_oversize(self) -> None:
        """Symlink rejected with auto-cleaned outside tempdir; oversized files rejected."""
        # Symlink pointing to second temporary directory
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_status = Path(outside_dir) / "outside_status.json"
            outside_status.write_bytes((self.failed_dir / "status.json").read_bytes())
            (self.failed_dir / "status.json").unlink()
            (self.failed_dir / "status.json").symlink_to(outside_status)
            with self.assertRaises(runner.ProposalError) as cm:
                runner._load_bound_opus_fallback_receipt(
                    self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
                )
            self.assertIn("failed status", str(cm.exception))
            (self.failed_dir / "status.json").unlink()
        self.assertFalse(outside_status.exists())

        # Oversize status.json
        self._write_baseline()
        (self.failed_dir / "status.json").write_bytes(b'{"x": "' + b"a" * (runner.MAX_CONTEXT + 1) + b'"}')
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("failed status", str(cm.exception))

        # Oversize task.json
        self._write_baseline()
        (self.failed_dir / "task.json").write_bytes(b'{"x": "' + b"a" * (3 * runner.MAX_CONTEXT + 1) + b'"}')
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("failed task", str(cm.exception))

        # Oversize stream.jsonl
        self._write_baseline()
        (self.failed_dir / "stream.jsonl").write_bytes(b"\n" * (runner.MAX_OUTPUT + 1))
        with self.assertRaises(runner.ProposalError) as cm:
            runner._load_bound_opus_fallback_receipt(
                self.root, self.failed_id, self.task, self.sources, self.head, self.ctrl_id
            )
        self.assertIn("failed stream", str(cm.exception))


class TestOpusRunnerFallback(unittest.TestCase):
    """Hermetic tests for run() fallback gates, model pinning, and late mutation."""

    def _execute_run(
        self,
        temp_path: Path,
        proposals_dir: Path,
        *,
        role: str = "worker",
        task_id: str = TASK_ID_OPUS,
        policy: WorkerRoutingPolicy = ENABLED_POLICY,
        controller_id: str | None = "ctrl-exec-001",
        failed_id: str | None = "run-opus-failed-001",
        fallback_id: str | None = None,
        fallback_from_run_id: str | None = None,
        run_id: str = "run-opus-failed-001-gf",
        capture_side_effect: object = None,
    ) -> tuple[Path, list[list[str]], mock.MagicMock]:
        if fallback_from_run_id is not None:
            failed_id = fallback_from_run_id
        elif fallback_id is not None:
            failed_id = fallback_id

        install_current_contract(temp_path)
        task_file = temp_path / "task.json"
        task_file.write_text("{}", encoding="utf-8")
        cli_file = temp_path / "mock_agy"
        cli_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cli_file.chmod(0o755)

        mock_task = {
            "task_id": task_id,
            "goal": "Hermetic fallback execution test",
            "context_files": [],
            "allowed_files": ["file.py"],
            "requirements": ["1. Test."],
            "test_selectors": ["tests.test_two_site_opus_runner_fallback"],
        }
        mock_sources = {
            "file.py": {
                "sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
                "content": "old_file_content\n",
            }
        }
        head = "0655392de0594432d41a73641e5134376b4c9e43"
        if failed_id is not None and controller_id is not None:
            try:
                _write_evidence(
                    proposals_dir / failed_id, failed_id, mock_task, mock_sources, head, controller_id
                )
            except Exception:
                pass

        captured_commands: list[list[str]] = []

        class DummyProcess:
            pid = 12345
            returncode = 0
            def __enter__(self) -> DummyProcess: return self
            def __exit__(self, *exc: object) -> bool: return False

        def mock_popen(cmd: list[str], **_kwargs: object) -> DummyProcess:
            captured_commands.append(list(cmd))
            return DummyProcess()

        def default_capture(proc: object, in_b: bytes, out_p: Path, err_p: Path, to: float, max_out: int = runner.MAX_OUTPUT, *, generation_completion=None) -> None:
            out_p.write_bytes(b"")
            err_p.write_bytes(b"")

        capture_fn = capture_side_effect or default_capture
        mock_result = {
            "status": "PROPOSAL_READY",
            "summary": "Proposal verified.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [],
        }
        mock_envelope = {"status": "SUCCESS", "conversation_id": "conv-opus-42", "usage": {}}
        self.popen_mock = mock.MagicMock(side_effect=mock_popen)

        patches = [
            mock.patch.object(runner, "load_task", return_value=(mock_task, mock_sources)),
            mock.patch.object(runner, "git", side_effect=lambda root, *args: head if "rev-parse" in args else ""),
            mock.patch.object(runner, "bounded_file_text", side_effect=lambda root, path, limit, label: "{}" if label == "proposal" else "template"),
            mock.patch.object(runner, "acquire_proposal_lock", return_value=(42, proposals_dir)),
            mock.patch.object(runner, "release_proposal_lock"),
            mock.patch.object(runner, "cli_identity", return_value={"path": str(cli_file), "version": runner.EXPECTED_CLI_VERSION, "sha256": runner.EXPECTED_CLI_SHA256}),
            mock.patch.object(runner, "prepare_profile", side_effect=lambda cli, ws, model, effort=None, **kw: runner.PreparedProfile("conv-opus-42", ws, model, effort)),
            mock.patch.object(runner, "validate_profile", side_effect=lambda prof, **kw: supported_profile(prof.conversation_id)),
            mock.patch.object(runner, "capture_bounded_process", side_effect=capture_fn),
            mock.patch.object(runner, "_parse_stream", return_value=(mock_result, mock_envelope)),
            mock.patch.object(runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT),
            mock.patch.object(runner, "load_worker_routing_policy", return_value=policy),
            mock.patch.object(runner, "controller_run_id_from_environment", return_value=controller_id),
            mock.patch("subprocess.Popen", self.popen_mock),
        ]

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            out = runner.run(
                temp_path, task_file, role, run_id, 30, cli=cli_file, fallback_from_run_id=failed_id
            )
        return out, captured_commands, self.popen_mock

    def test_pre_popen_gates(self) -> None:
        """Verifier role, disabled policy, wrong route, missing controller, and bad ID reject before Popen."""
        disabled_policy = WorkerRoutingPolicy(
            schema_version="1.0",
            enabled=False,
            percent=22,
            model=OPUS_MODEL,
            effort=OPUS_EFFORT,
        )

        gate_cases = [
            ({"role": "verifier"}, "fallback is only supported for worker role"),
            ({"policy": disabled_policy}, "worker routing policy is disabled"),
            ({"task_id": TASK_ID_GEMINI}, "selected route model does not match policy model"),
            ({"controller_id": None}, "nonempty controller ID required for fallback"),
            (
                {"failed_id": "invalid/id", "run_id": "run-opus-failed-001-gf"},
                "invalid failed run ID",
            ),
            (
                {"failed_id": "", "run_id": "run-opus-failed-001-gf"},
                "invalid failed run ID",
            ),
            (
                {"failed_id": "run-opus-failed-001", "run_id": "wrong-run-id"},
                r"fallback run ID must be exactly failed_id \+ '-gf' and at most 80 characters",
            ),
        ]

        for kwargs, expected_err in gate_cases:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                proposals_dir = temp_path / ".local/two-site-refactor/proposals"
                proposals_dir.mkdir(parents=True, exist_ok=True)
                with self.assertRaises(runner.ProposalError) as cm:
                    self._execute_run(temp_path, proposals_dir, **kwargs)
                self.assertRegex(str(cm.exception), expected_err)
                self.popen_mock.assert_not_called()
                target_run_id = kwargs.get("run_id", "run-opus-failed-001-gf")
                self.assertFalse((proposals_dir / target_run_id).exists())

    def test_positive_fallback_pins_gemini_and_records_receipt(self) -> None:
        """Hermetic positive fallback pins Gemini WORKER_MODEL/WORKER_EFFORT and records receipt."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            proposals_dir = temp_path / ".local/two-site-refactor/proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            failed_id = "run-opus-failed-001"
            run_id = f"{failed_id}-gf"

            out, cmds, _ = self._execute_run(temp_path, proposals_dir, failed_id=failed_id, run_id=run_id)
            self.assertEqual(out, proposals_dir / run_id)
            self.assertEqual(len(cmds), 1)
            cmd = cmds[0]

            # Verify Gemini model and effort are pinned
            self.assertEqual(cmd[cmd.index("--model") + 1], runner.WORKER_MODEL)
            self.assertEqual(cmd[cmd.index("--effort") + 1], runner.WORKER_EFFORT)

            # Verify status.json records metadata and receipt
            status_data = json.loads((out / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_data["state"], "PROPOSAL_READY")
            self.assertEqual(status_data["model"], runner.WORKER_MODEL)
            self.assertEqual(status_data["effort"], runner.WORKER_EFFORT)
            self.assertEqual(status_data["fallback_from_run_id"], failed_id)
            self.assertEqual(status_data["fallback_receipt"]["run_id"], failed_id)
            self.assertTrue((out / "result.json").is_file())

    def test_late_stream_mutation_blocks_before_result(self) -> None:
        """Late mutation of prior stream during capture blocks before result.json."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            proposals_dir = temp_path / ".local/two-site-refactor/proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            failed_id = "run-opus-failed-001"
            run_id = f"{failed_id}-gf"

            def mock_capture_tamper(proc: object, in_b: bytes, out_p: Path, err_p: Path, to: float, max_out: int = runner.MAX_OUTPUT, *, generation_completion=None) -> None:
                self.assertIs(type(generation_completion), runner.GenerationRecoveryPolicy)
                out_p.write_bytes(b"")
                err_p.write_bytes(b"")
                (proposals_dir / failed_id / "stream.jsonl").write_bytes(b'{"event": "corrupt_late"}\n')

            with self.assertRaises(runner.ProposalError) as cm:
                self._execute_run(
                    temp_path, proposals_dir, failed_id=failed_id, run_id=run_id, capture_side_effect=mock_capture_tamper
                )
            self.assertIn("invalid Opus failure receipt:", str(cm.exception))

            out_dir = proposals_dir / run_id
            self.assertTrue(out_dir.is_dir())
            self.assertFalse((out_dir / "result.json").exists())
            status_data = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status_data["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
