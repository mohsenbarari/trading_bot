from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import errno
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import (
    orchestrate_production_shadow_freeze_snapshot_phases as MODULE,
)
from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import verify_production_shadow_phase_evidence as VERIFY


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "c" * 40
LEGACY_RELEASE_SHA = "b" * 40


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def private_json(path: Path, value: object) -> MODULE.SecureRecord:
    payload = canonical(value) + b"\n"
    path.write_bytes(payload)
    path.chmod(0o600)
    metadata = path.stat(follow_symlinks=False)
    return MODULE.SecureRecord(
        path=path,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        identity=MODULE._identity(metadata),
        document=dict(value),  # type: ignore[arg-type]
    )


def external_liveness_pipe() -> tuple[int, subprocess.Popen[bytes]]:
    read_fd, write_fd = os.pipe()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            (
                "import os,sys,time;"
                "os.fstat(int(sys.argv[1]));"
                "time.sleep(300)"
            ),
            str(write_fd),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(write_fd,),
        close_fds=True,
    )
    os.close(write_fd)
    return read_fd, holder


def stop_holder(holder: subprocess.Popen[bytes]) -> None:
    if holder.poll() is None:
        holder.terminate()
    holder.wait(timeout=5)


def freeze_result(role: str) -> dict:
    return {
        "schema": MODULE.FREEZE.RESULT_SCHEMA,
        "status": "verified-frozen",
        "action": "verify",
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "role": role,
        "binding_sha256": ("1" if role == "bot_fi" else "2") * 64,
        "nginx_manifest_sha256": ("3" if role == "bot_fi" else "4") * 64,
        "nginx_aggregate_sha256": "5" * 64,
        "coordinated_state_receipt_sha256": "6" * 64,
        "live_lease_claim_sha256": "7" * 64,
        "live_lease_claim_epoch": 3,
        "role_freeze_generation_sha256": (
            "8" if role == "bot_fi" else "9"
        )
        * 64,
        "freeze_generation_sha256": "a" * 64,
        "journal_sha256": ("b" if role == "bot_fi" else "c") * 64,
        "freeze_evidence_sha256": (
            "d" if role == "bot_fi" else "e"
        )
        * 64,
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
        "database_container_running": True,
        "redis_container_running": True,
        "production_mutated": False,
    }


def snapshot(role: str) -> dict:
    seed = "1" if role == "bot_fi" else "2"
    other = "3" if role == "bot_fi" else "4"
    return {
        "artifacts": {
            "database-backup": {
                "sha256": seed * 64,
                "bytes": 101,
                "restored_tree_sha256": None,
            },
            "uploads-archive": {
                "sha256": other * 64,
                "bytes": 102,
                "restored_tree_sha256": "5" * 64,
            },
            "audit-archive": {
                "sha256": ("6" if role == "bot_fi" else "7") * 64,
                "bytes": 103,
                "restored_tree_sha256": "8" * 64,
            },
        },
        "source_database": {
            "database_fingerprint_sha256": (
                "9" if role == "bot_fi" else "a"
            )
            * 64,
            "alembic_revision": "head",
        },
        "file_snapshots": {
            "uploads": {
                "pre_tree_sha256": "5" * 64,
                "archive_tree_sha256": "5" * 64,
                "post_tree_sha256": "5" * 64,
                "member_count": 4,
                "expanded_bytes": 500,
            },
            "audit": {
                "pre_tree_sha256": "8" * 64,
                "archive_tree_sha256": "8" * 64,
                "post_tree_sha256": "8" * 64,
                "member_count": 3,
                "expanded_bytes": 400,
            },
        },
        "redis_rollback_only": {
            "policy": "sealed-rollback-evidence-only",
            "source_volume": f"{role}_redis",
            "tree_sha256": ("b" if role == "bot_fi" else "c") * 64,
            "metadata_sha256": ("d" if role == "bot_fi" else "e") * 64,
            "member_count": 7,
            "bytes": 700,
            "archive_created": False,
            "restore": False,
        },
        "redis_restored": False,
    }


def nginx_receipt() -> dict:
    return {
        "external_readback": {
            "vhosts": {
                vhost: {
                    "get": 200,
                    "post": 503,
                    "websocket": 503,
                }
                for vhost, _address in MODULE.NGINX.VHOST_TARGETS
            }
        }
    }


def current_epoch_records(
    root: Path,
) -> tuple[
    MODULE.SecureRecord,
    MODULE.SecureRecord,
    dict,
    dict,
    dict,
    dict[str, SimpleNamespace],
]:
    nginx = {
        "schema": (
            MODULE.NGINX.PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
        ),
        "readback_challenge_sha256": "1" * 64,
        "issued_at_epoch": 1_000,
        "expires_at_epoch": 2_000,
        "source_action": "readback",
        "coordinator_status": "read-back",
        "global_generation_sha256": "a" * 64,
        "readbacks": {
            role: {
                "generation_sha256": freeze_result(role)[
                    "role_freeze_generation_sha256"
                ]
            }
            for role in MODULE.ROLE_ORDER
        },
    }
    nginx_record = private_json(root / "fresh-nginx.json", nginx)
    frozen = {
        "nginx_aggregate_sha256": "5" * 64,
        "state_receipt_sha256": "6" * 64,
        "lease_claim_sha256": "7" * 64,
        "outcome_sha256": "8" * 64,
        "consumption_sha256": "9" * 64,
        "roles": {
            role: {
                "freeze_evidence_sha256": freeze_result(role)[
                    "freeze_evidence_sha256"
                ]
            }
            for role in MODULE.ROLE_ORDER
        },
    }
    current = {
        "capture_lease_claim_sha256": frozen["lease_claim_sha256"],
        "capture_outcome_sha256": frozen["outcome_sha256"],
        "capture_lease_consumption_sha256": frozen[
            "consumption_sha256"
        ],
        "fresh_state_receipt_sha256": nginx_record.sha256,
        "readback_challenge_sha256": nginx[
            "readback_challenge_sha256"
        ],
        "issued_at_epoch": nginx["issued_at_epoch"],
        "expires_at_epoch": nginx["expires_at_epoch"],
        "captured_at_epoch": 1_500,
        "freeze_generation_sha256": nginx[
            "global_generation_sha256"
        ],
        "host_results": {
            role: {
                **freeze_result(role),
                "freeze_evidence_live_lease_claim_sha256": frozen[
                    "lease_claim_sha256"
                ],
            }
            for role in MODULE.ROLE_ORDER
        },
        "freeze_evidence": {
            role: {
                "live_lease_claim_sha256": frozen[
                    "lease_claim_sha256"
                ],
                "sha256": frozen["roles"][role][
                    "freeze_evidence_sha256"
                ],
            }
            for role in MODULE.ROLE_ORDER
        },
    }
    current_record = private_json(
        root / "current-verification.json",
        current,
    )
    bindings = {
        role: SimpleNamespace(
            canonical_sha256=freeze_result(role)["binding_sha256"]
        )
        for role in MODULE.ROLE_ORDER
    }
    return (
        current_record,
        nginx_record,
        current,
        nginx,
        frozen,
        bindings,
    )


def validated_sources(root: Path) -> MODULE.ValidatedSources:
    current_receipt = {
        "host_results": {
            role: freeze_result(role) for role in MODULE.ROLE_ORDER
        }
    }
    records = {
        "current_frozen_verification_receipt": private_json(
            root / "current-verification.json",
            current_receipt,
        )
    }
    records["nginx_readback_receipt"] = private_json(
        root / "nginx.json",
        nginx_receipt(),
    )
    for role in MODULE.ROLE_ORDER:
        records[f"{role}_snapshot_manifest"] = private_json(
            root / f"{role}-snapshot.json",
            snapshot(role),
        )
    return MODULE.ValidatedSources(
        records=records,
        bindings={},
        freeze_results={
            role: freeze_result(role) for role in MODULE.ROLE_ORDER
        },
        freeze_evidence={
            role: {
                "write_capable_route_count": 0,
                "legacy_writer_process_count": 0,
                "writer_database_client_count": 0,
                "file_mutator_process_count": 0,
            }
            for role in MODULE.ROLE_ORDER
        },
        snapshots={role: snapshot(role) for role in MODULE.ROLE_ORDER},
        frozen_result={
            "public_phase": MODULE.PHASES[0],
            "public_phase_handoff_sha256": "a" * 64,
            "public_phase_start_journal_state_sha256": "8" * 64,
            "public_phase_start_journal_event_tail_sha256": "9" * 64,
            "public_phase_start_journal_event_count": 1,
            "roles": {
                role: {
                    "freeze_evidence_sha256": freeze_result(role)[
                        "freeze_evidence_sha256"
                    ]
                }
                for role in MODULE.ROLE_ORDER
            }
        },
        current_verification_receipt=current_receipt,
        nginx_receipt=nginx_receipt(),
        source_closure_sha256="f" * 64,
    )


def manifest(root: Path) -> dict:
    topology = {
        role: {
            "host": host,
            "transport": transport,
        }
        for role, host, transport in (
            ("bot_fi", "65.109.216.187", "local-controller"),
            ("webapp_fi", "65.109.220.240", "trusted-ssh-scp"),
            ("webapp_ir", "87.107.3.22", "private-versioned-age-object"),
            ("witness", "185.206.95.94", "trusted-ssh-control"),
        )
    }
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "legacy_release_sha": LEGACY_RELEASE_SHA,
        "topology": topology,
        "deployment": {
            "controller_journal_path": str(root / "journal.json"),
            "controller_evidence_root": str(root / "evidence"),
        },
        "artifacts": {
            "cutover_approval_sha256": "1" * 64,
            "human_approval_policy_sha256": "2" * 64,
            "phase_evidence_schema_sha256": (
                VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
            ),
            "host_agent_sha256": "3" * 64,
            "host_agent_contract_sha256": "4" * 64,
            "nginx_freeze_generation_sha256": "a" * 64,
        },
    }


def context(root: Path) -> MODULE.BridgeContext:
    request = private_json(
        root / "request.json",
        {
            "frozen_snapshot_result": {
                "path": str(root / "frozen.json"),
                "sha256": "5" * 64,
            }
        },
    )
    value = manifest(root)
    return MODULE.BridgeContext(
        request=request,
        manifest_path=root / "manifest.json",
        approval_path=root / "approval.json",
        approval_policy_path=root / "policy.json",
        manifest=value,
        manifest_sha256="6" * 64,
        plan={"plan_sha256": "7" * 64},
        plan_sha256="7" * 64,
        output_root=root / "evidence" / "freeze-snapshot-phase-bridge",
        prior_paths={
            phase: root / f"{phase}.json"
            for phase in CONTROLLER.PHASES[: MODULE.FIRST_PHASE_INDEX]
        },
    )


def journal_state(
    completed_count: int,
    *,
    root: Path,
    started: str | None = None,
) -> dict:
    completed = list(
        CONTROLLER.PHASES[
            : MODULE.FIRST_PHASE_INDEX + completed_count
        ]
    )
    evidence = {
        phase: hashlib.sha256(phase.encode("ascii")).hexdigest()
        for phase in completed
    }
    verification = {
        phase: hashlib.sha256((phase + "-v").encode("ascii")).hexdigest()
        for phase in completed
    }
    events = (
        [
            {
                "kind": "phase_started",
                "phase": started,
                "event_hash": "9" * 64,
            }
        ]
        if started is not None
        else [{"kind": "test"}]
    )
    return {
        **MODULE._journal_bindings(context(root)),
        "status": "phase_started" if started is not None else "active",
        "completed_phases": completed,
        "phase_evidence_sha256": evidence,
        "phase_verification_sha256": verification,
        "started_phase": started,
        "rollback_eligible": True,
        "first_business_write_allowed": False,
        "events": events,
        "state_sha256": "8" * 64,
        "event_tail_sha256": "9" * 64,
    }


class FakeJournal:
    def __init__(
        self,
        root: Path,
        completed_count: int = 0,
        *,
        prestarted: bool = True,
    ):
        self.root = root
        self.state = journal_state(
            completed_count,
            root=root,
            started=(
                MODULE.PHASES[0]
                if completed_count == 0 and prestarted
                else None
            ),
        )
        if prestarted:
            self.state["events"] = [
                {
                    "kind": "phase_started",
                    "phase": MODULE.PHASES[0],
                    "event_hash": "9" * 64,
                }
            ]
        self.begin_calls: list[str] = []
        self.complete_calls: list[str] = []

    def assert_bindings(self, **_bindings):  # noqa: ANN003, ANN202
        return dict(self.state)

    def begin_phase(self, phase: str) -> dict:
        self.begin_calls.append(phase)
        self.state["status"] = "phase_started"
        self.state["started_phase"] = phase
        return dict(self.state)

    def complete_phase(self, phase: str, *, verification) -> dict:  # noqa: ANN001
        self.complete_calls.append(phase)
        self.state["completed_phases"].append(phase)
        self.state["phase_evidence_sha256"][phase] = (
            verification.evidence_sha256
        )
        self.state["phase_verification_sha256"][phase] = (
            verification.receipt_sha256
        )
        self.state["status"] = "active"
        self.state["started_phase"] = None
        return dict(self.state)


class FreezeSnapshotPhaseBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_phase_specs_are_exactly_the_three_adjacent_public_phases(self):
        self.assertEqual(
            MODULE.PHASES,
            (
                "stop_legacy_writers",
                "zero_writer_surface_readback",
                "final_snapshot_hashes",
            ),
        )
        self.assertEqual(
            tuple(
                CONTROLLER.PHASES[
                    MODULE.FIRST_PHASE_INDEX : MODULE.FIRST_PHASE_INDEX + 3
                ]
            ),
            MODULE.PHASES,
        )
        self.assertIn(
            "current_frozen_verification_receipt",
            MODULE.REQUEST_FIELDS,
        )
        self.assertNotIn(
            "freeze_verify_result",
            MODULE.ROLE_SOURCE_FIELDS,
        )

    def test_bridge_refuses_an_unstarted_first_public_phase(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root, prestarted=False)
        read_fd, holder = external_liveness_pipe()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ) as validate_sources,
                mock.patch.object(MODULE, "_prepare_phase_evidence") as prepare,
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeError,
                    "coordinator-owned public phase start",
                ),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(fake_journal.begin_calls, [])
            prepare.assert_not_called()
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_apply_rejects_caller_supplied_observation_time(self):
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "caller-supplied observation time",
        ):
            MODULE.execute(
                self.root / "not-read.json",
                apply=True,
                now=datetime(2026, 7, 28, tzinfo=timezone.utc),
            )

    def test_claims_are_derived_only_from_worker_and_readback_artifacts(self):
        sources = validated_sources(self.root)
        stop = MODULE._phase_claims("stop_legacy_writers", sources)
        zero = MODULE._phase_claims(
            "zero_writer_surface_readback",
            sources,
        )
        final = MODULE._phase_claims("final_snapshot_hashes", sources)
        self.assertEqual(set(stop.values()), {0})
        self.assertEqual(zero["write_capable_route_count"], 0)
        self.assertEqual(zero["externally_read_vhost_count"], 3)
        self.assertEqual(final["legacy_redis_restore_member_count"], 0)
        self.assertEqual(final["frozen_writer_delta_count"], 0)
        self.assertTrue(final["file_snapshot_pre_post_stat_stable"])
        self.assertTrue(final["file_snapshot_tree_hash_stable"])
        for name in (
            "postgres_snapshot_set_sha256",
            "reviewed_file_snapshot_set_sha256",
            "legacy_redis_sealed_set_sha256",
        ):
            self.assertRegex(final[name], r"^[0-9a-f]{64}$")

    def test_no_caller_boolean_can_fabricate_nonzero_writer_proof(self):
        sources = validated_sources(self.root)
        changed = dict(sources.freeze_results)
        changed["webapp_fi"] = {
            **changed["webapp_fi"],
            "legacy_writer_process_count": 1,
        }
        hostile = MODULE.ValidatedSources(
            **{
                **sources.__dict__,
                "freeze_results": changed,
            }
        )
        with self.assertRaises(MODULE.FreezeSnapshotPhaseBridgeError):
            MODULE._phase_claims("stop_legacy_writers", hostile)
        self.assertNotIn(
            "writer_stopped",
            MODULE.REQUEST_FIELDS,
        )
        self.assertEqual(
            MODULE.EXPECTED_CONSTRAINTS["caller_truth_values_forbidden"],
            True,
        )

    def test_external_readback_must_cover_all_three_vhosts_exactly(self):
        sources = validated_sources(self.root)
        receipt = nginx_receipt()
        receipt["external_readback"]["vhosts"].pop("coin.gold-trade.ir")
        hostile = MODULE.ValidatedSources(
            **{**sources.__dict__, "nginx_receipt": receipt}
        )
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "three-vhost",
        ):
            MODULE._phase_claims(
                "zero_writer_surface_readback",
                hostile,
            )

    def test_final_hashes_reject_unstable_files_and_redis_restore(self):
        sources = validated_sources(self.root)
        unstable = {
            role: json.loads(json.dumps(value))
            for role, value in sources.snapshots.items()
        }
        unstable["bot_fi"]["file_snapshots"]["uploads"][
            "post_tree_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "unstable",
        ):
            MODULE._derive_final_hashes(
                MODULE.ValidatedSources(
                    **{**sources.__dict__, "snapshots": unstable}
                )
            )
        restored = {
            role: json.loads(json.dumps(value))
            for role, value in sources.snapshots.items()
        }
        restored["webapp_fi"]["redis_rollback_only"]["restore"] = True
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "rollback-only",
        ):
            MODULE._derive_final_hashes(
                MODULE.ValidatedSources(
                    **{**sources.__dict__, "snapshots": restored}
                )
            )

    def test_frozen_result_rejects_partial_host_closure(self):
        value = {
            field: None for field in MODULE.FROZEN_RESULT_FIELDS
        }
        value.update(
            {
                "schema": MODULE.FROZEN.RESULT_SCHEMA,
                "status": "complete",
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": RELEASE_TREE_SHA,
                "roles": {"bot_fi": {}},
                "live_lease_outcome": "handoff-shadow-readonly",
                "legacy_writers_frozen": True,
                "automatic_restore_performed": False,
                "pull_policy": "never",
                "build_performed": False,
                "object_storage_used": False,
                "wa_contacted": False,
            }
        )
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "coordinator result",
        ):
            MODULE._validate_frozen_result(
                value,
                context=context(self.root),
            )

    def test_tampered_source_identity_is_rejected(self):
        record = private_json(self.root / "source.json", {"value": 1})
        (self.root / "source.json").write_bytes(
            canonical({"value": 2}) + b"\n"
        )
        (self.root / "source.json").chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "validated source changed",
        ):
            MODULE._assert_records_unchanged({"source": record})

    def test_file_mtime_is_not_used_as_freshness_authority(self):
        self.assertFalse(hasattr(MODULE, "_require_fresh"))
        self.assertFalse(hasattr(MODULE, "_mtime"))

    def test_current_epoch_uses_fresh_loaders_after_file_touch(self):
        (
            current_record,
            nginx_record,
            current,
            nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)
        old = time.time() - 86_400
        os.utime(current_record.path, (old, old))
        os.utime(nginx_record.path, (old, old))
        current_record = MODULE._read_private_json(
            current_record.path,
            label="touched current verification",
        )
        nginx_record = MODULE._read_private_json(
            nginx_record.path,
            label="touched Nginx receipt",
        )
        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                return_value=(current, current_record.sha256),
            ) as current_loader,
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                return_value=(nginx, nginx_record.sha256),
            ) as nginx_loader,
            mock.patch.object(
                MODULE.FROZEN,
                "canonical_paths",
                return_value={"state_receipt": nginx_record.path},
            ),
        ):
            observed = MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=current_record,
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(1_500, tz=timezone.utc),
            )
        self.assertEqual(observed[0], current)
        self.assertEqual(observed[1], nginx)
        self.assertEqual(
            current_loader.call_args.kwargs["observed_at_epoch"],
            1_500,
        )
        self.assertEqual(
            nginx_loader.call_args.kwargs["observed_at_epoch"],
            1_500,
        )

    def test_current_epoch_accepts_completed_receipt_after_r2_expiry(self):
        (
            current_record,
            nginx_record,
            current,
            nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)
        current_calls: list[bool] = []
        nginx_calls: list[bool] = []

        def load_current(_path, **kwargs):  # noqa: ANN001, ANN202
            historical = kwargs.get("allow_historical_completed", False)
            current_calls.append(historical)
            if not historical:
                raise MODULE.CURRENT.CurrentFrozenVerificationError(
                    "expired"
                )
            return current, current_record.sha256

        def load_nginx(*_args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            historical = kwargs.get("allow_historical", False)
            nginx_calls.append(historical)
            if not historical:
                raise MODULE.NGINX.NginxCoordinatorError("expired")
            return nginx, nginx_record.sha256

        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                side_effect=load_current,
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                side_effect=load_nginx,
            ),
            mock.patch.object(
                MODULE.FROZEN,
                "canonical_paths",
                return_value={"state_receipt": nginx_record.path},
            ),
        ):
            observed = MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=current_record,
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(2_100, tz=timezone.utc),
            )
        self.assertEqual(observed[0], current)
        self.assertEqual(current_calls, [False, True])
        self.assertEqual(nginx_calls, [False, True])

    def test_current_epoch_rejects_completed_receipt_beyond_phase_age(self):
        (
            current_record,
            nginx_record,
            current,
            _nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)

        def load_current(_path, **kwargs):  # noqa: ANN001, ANN202
            if not kwargs.get("allow_historical_completed", False):
                raise MODULE.CURRENT.CurrentFrozenVerificationError(
                    "expired"
                )
            return current, current_record.sha256

        stale_epoch = int(
            current["captured_at_epoch"]
            + MODULE.CURRENT_VERIFICATION_MAX_AGE.total_seconds()
            + 1
        )
        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                side_effect=load_current,
            ),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "outside phase age",
            ),
        ):
            MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=current_record,
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(stale_epoch, tz=timezone.utc),
            )

    def test_current_epoch_rejects_wrong_readback_challenge(self):
        (
            current_record,
            nginx_record,
            current,
            nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)
        nginx = {**nginx, "readback_challenge_sha256": "2" * 64}
        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                return_value=(current, current_record.sha256),
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                return_value=(nginx, nginx_record.sha256),
            ),
            mock.patch.object(
                MODULE.FROZEN,
                "canonical_paths",
                return_value={"state_receipt": nginx_record.path},
            ),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "fresh read-only",
            ),
        ):
            MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=current_record,
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(1_500, tz=timezone.utc),
            )

    def test_current_epoch_rejects_copied_nginx_receipt(self):
        (
            current_record,
            nginx_record,
            current,
            _nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)
        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                return_value=(current, current_record.sha256),
            ),
            mock.patch.object(
                MODULE.FROZEN,
                "canonical_paths",
                return_value={
                    "state_receipt": self.root / "canonical-nginx.json"
                },
            ),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "controller-canonical",
            ),
        ):
            MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=current_record,
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(1_500, tz=timezone.utc),
            )

    def test_current_epoch_rejects_r1_evidence_bound_to_r2(self):
        (
            current_record,
            nginx_record,
            current,
            nginx,
            frozen,
            bindings,
        ) = current_epoch_records(self.root)
        current = json.loads(json.dumps(current))
        current["host_results"]["bot_fi"][
            "freeze_evidence_live_lease_claim_sha256"
        ] = "b" * 64
        with (
            mock.patch.object(
                MODULE.CURRENT,
                "load_current_frozen_verification_receipt",
                return_value=(current, current_record.sha256),
            ),
            mock.patch.object(
                MODULE.NGINX,
                "load_state_receipt",
                return_value=(nginx, nginx_record.sha256),
            ),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "R1 freeze evidence",
            ),
        ):
            MODULE._load_current_verification_epoch(
                context=context(self.root),
                frozen_result=frozen,
                bindings=bindings,
                current_record=MODULE.SecureRecord(
                    **{
                        **current_record.__dict__,
                        "document": current,
                    }
                ),
                nginx_record=nginx_record,
                now=datetime.fromtimestamp(1_500, tz=timezone.utc),
            )

    def test_controller_guard_rejects_regular_file(self):
        path = self.root / "not-pipe"
        path.write_bytes(b"")
        descriptor = os.open(path, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "anonymous read-only pipe",
            ):
                MODULE.ControllerEOFGuard(descriptor)
        finally:
            os.close(descriptor)

    def test_controller_eof_cancels_once(self):
        read_fd, holder = external_liveness_pipe()
        try:
            with MODULE._one_shot_signal_guard():
                with self.assertRaises(
                    MODULE.FreezeSnapshotPhaseBridgeCancellation
                ):
                    with MODULE.ControllerEOFGuard(read_fd):
                        stop_holder(holder)
                        time.sleep(0.2)
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_controller_guard_rejects_retained_writer(self):
        read_fd, write_fd = os.pipe()
        try:
            with self.assertRaisesRegex(
                MODULE.FreezeSnapshotPhaseBridgeError,
                "retains a writer",
            ):
                MODULE.ControllerEOFGuard(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_controller_guard_fails_closed_on_unexpected_fd_scan_error(self):
        read_fd, write_fd = os.pipe()
        real_fstat = os.fstat

        def hostile_fstat(descriptor: int):  # noqa: ANN202
            if descriptor == write_fd:
                raise OSError(errno.EIO, "scan failed")
            return real_fstat(descriptor)

        try:
            with (
                mock.patch.object(
                    MODULE.os,
                    "fstat",
                    side_effect=hostile_fstat,
                ),
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeError,
                    "descriptor scan failed",
                ),
            ):
                MODULE.ControllerEOFGuard(read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_preexisting_eof_is_detected_synchronously_before_thread_start(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        guard = MODULE.ControllerEOFGuard(read_fd)
        try:
            with (
                MODULE._one_shot_signal_guard(),
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeCancellation,
                    "already lost",
                ),
            ):
                guard.__enter__()
            with self.assertRaises(OSError):
                os.fstat(guard.descriptor)
            self.assertFalse(guard.start_attempted)
        finally:
            os.close(read_fd)

    def test_thread_start_failure_closes_duplicate_without_clobbering_primary(self):
        read_fd, holder = external_liveness_pipe()
        guard = MODULE.ControllerEOFGuard(read_fd)
        try:
            with (
                mock.patch.object(
                    guard.thread,
                    "start",
                    side_effect=KeyboardInterrupt("start failed"),
                ),
                self.assertRaisesRegex(
                    KeyboardInterrupt,
                    "start failed",
                ) as raised,
            ):
                guard.__enter__()
            with self.assertRaises(OSError):
                os.fstat(guard.descriptor)
            self.assertTrue(guard.start_attempted)
            self.assertTrue(
                any(
                    "liveness cleanup failed" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_signal_guard_is_one_shot_and_restores_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        with MODULE._one_shot_signal_guard():
            handler = signal.getsignal(signal.SIGTERM)
            self.assertTrue(callable(handler))
            with self.assertRaises(
                MODULE.FreezeSnapshotPhaseBridgeCancellation
            ):
                handler(signal.SIGTERM, None)  # type: ignore[operator]
            handler(signal.SIGTERM, None)  # type: ignore[operator]
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_signal_install_failure_restores_every_installed_handler_in_reverse(self):
        calls: list[tuple[int, object]] = []

        def hostile_signal(signum: int, handler: object) -> object:
            calls.append((signum, handler))
            if len(calls) == 3:
                raise RuntimeError("install failed")
            if len(calls) == 4:
                raise RuntimeError("restore failed")
            return signal.SIG_DFL

        guard = MODULE.OneShotSignalGuard()
        with (
            mock.patch.object(
                MODULE.signal,
                "signal",
                side_effect=hostile_signal,
            ),
            self.assertRaisesRegex(RuntimeError, "install failed") as raised,
        ):
            guard.__enter__()
        self.assertEqual(len(calls), 5)
        self.assertEqual(
            [item[0] for item in calls[3:]],
            [signal.SIGINT, signal.SIGHUP],
        )
        self.assertTrue(
            any(
                "partial signal installation cleanup failed" in note
                for note in getattr(raised.exception, "__notes__", ())
            )
        )

    def test_baseexception_propagates_and_guard_closes_duplicate(self):
        read_fd, holder = external_liveness_pipe()
        guard = MODULE.ControllerEOFGuard(read_fd)
        try:
            with self.assertRaises(KeyboardInterrupt):
                with MODULE._one_shot_signal_guard(), guard:
                    raise KeyboardInterrupt("host callback aborted")
            with self.assertRaises(OSError):
                os.fstat(guard.descriptor)
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_guard_cleanup_failure_annotates_primary_and_still_closes_fd(self):
        read_fd, holder = external_liveness_pipe()
        guard = MODULE.ControllerEOFGuard(read_fd)
        primary = KeyboardInterrupt("primary")
        try:
            guard.__enter__()
            with mock.patch.object(
                guard.thread,
                "join",
                side_effect=RuntimeError("join failed"),
            ):
                self.assertFalse(
                    guard.__exit__(KeyboardInterrupt, primary, None)
                )
            with self.assertRaises(OSError):
                os.fstat(guard.descriptor)
            self.assertTrue(
                any(
                    "liveness cleanup failed" in note
                    for note in getattr(primary, "__notes__", ())
                )
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_guard_close_failure_does_not_skip_join(self):
        read_fd, holder = external_liveness_pipe()
        guard = MODULE.ControllerEOFGuard(read_fd)
        primary = KeyboardInterrupt("primary")
        real_close = os.close
        try:
            guard.__enter__()

            def hostile_close(descriptor: int) -> None:
                if descriptor == guard.descriptor:
                    raise RuntimeError("close failed")
                real_close(descriptor)

            with (
                mock.patch.object(
                    MODULE.os,
                    "close",
                    side_effect=hostile_close,
                ),
                mock.patch.object(
                    guard.thread,
                    "join",
                    wraps=guard.thread.join,
                ) as joined,
            ):
                self.assertFalse(
                    guard.__exit__(KeyboardInterrupt, primary, None)
                )
            joined.assert_called_once()
            self.assertTrue(
                any(
                    "close failed" in note
                    for note in getattr(primary, "__notes__", ())
                )
            )
        finally:
            try:
                real_close(guard.descriptor)
            except OSError:
                pass
            real_close(read_fd)
            stop_holder(holder)

    def test_completed_resume_reuses_all_phases_without_transitions(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root, completed_count=3)
        read_fd, holder = external_liveness_pipe()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(
                    MODULE,
                    "_verify_authorization",
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_locate_completed_evidence",
                    side_effect=lambda _context, phase, digest: (
                        self.root / f"{phase}.{digest}.json"
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ) as validate_sources,
                mock.patch.object(
                    MODULE,
                    "_assert_records_unchanged",
                ),
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "aggregate.json", "f" * 64),
                ),
            ):
                result = MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(fake_journal.begin_calls, [])
            self.assertEqual(fake_journal.complete_calls, [])
            self.assertEqual(
                set(validate_sources.call_args.kwargs),
                {"now"},
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_three_phases_begin_verify_and_complete_sequentially(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()
        verifier_calls: list[str] = []

        def verifier(**kwargs):  # noqa: ANN003, ANN202
            phase = kwargs["phase"]
            verifier_calls.append(phase)
            return (
                CONTROLLER.VerifiedPhaseCompletion(
                    phase=phase,
                    evidence_sha256=hashlib.sha256(
                        phase.encode("ascii")
                    ).hexdigest(),
                    receipt_sha256=hashlib.sha256(
                        (phase + "-receipt").encode("ascii")
                    ).hexdigest(),
                ),
                canonical({"phase": phase}) + b"\n",
            )

        def prepared(
            _context,
            *,
            phase,
            **_kwargs,
        ):  # noqa: ANN001, ANN003, ANN202
            path = self.root / f"{phase}.json"
            return (
                path,
                {
                    role: self.root / f"{phase}-{role}.json"
                    for role in next(
                        spec.roles
                        for spec in CONTROLLER.PHASE_SPECS
                        if spec.phase == phase
                    )
                },
                {
                    claim: self.root / f"{phase}-{claim}.json"
                    for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                },
                {"phase": phase},
            )

        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ) as validate_sources,
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=prepared,
                ),
                mock.patch.object(
                    MODULE,
                    "_assert_records_unchanged",
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_run_release_phase_verifier",
                    side_effect=verifier,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_persist_phase_verification_receipt",
                ),
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "aggregate.json", "f" * 64),
                ),
            ):
                result = MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(
                fake_journal.begin_calls,
                list(MODULE.PHASES[1:]),
            )
            self.assertEqual(validate_sources.call_count, 16)
            self.assertEqual(fake_journal.complete_calls, list(MODULE.PHASES))
            self.assertEqual(verifier_calls, list(MODULE.PHASES))
            self.assertEqual(
                result["next_phase"],
                "pristine_shadow_redis",
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_source_expiry_after_durable_start_leaves_phase_started(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    side_effect=(
                        sources,
                        sources,
                        MODULE.FreezeSnapshotPhaseBridgeError(
                            "completed current frozen verification is outside phase age"
                        ),
                    ),
                ) as validate_sources,
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "closure.json", "f" * 64),
                ),
                mock.patch.object(MODULE, "_assert_records_unchanged"),
                mock.patch.object(MODULE, "_prepare_phase_evidence") as prepare,
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeError,
                    "outside phase age",
                ),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(validate_sources.call_count, 3)
            prepare.assert_not_called()
            self.assertEqual(fake_journal.complete_calls, [])
            self.assertEqual(fake_journal.state["status"], "phase_started")
            self.assertEqual(
                fake_journal.state["started_phase"],
                MODULE.PHASES[0],
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_source_expiry_before_receipt_persistence_leaves_phase_started(
        self,
    ):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()

        def prepared(
            _context,
            *,
            phase,
            **_kwargs,
        ):  # noqa: ANN001, ANN003, ANN202
            return (
                self.root / f"{phase}.json",
                {
                    role: self.root / f"{phase}-{role}.json"
                    for role in next(
                        spec.roles
                        for spec in CONTROLLER.PHASE_SPECS
                        if spec.phase == phase
                    )
                },
                {
                    claim: self.root / f"{phase}-{claim}.json"
                    for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                },
                {"phase": phase},
            )

        def verifier(**kwargs):  # noqa: ANN003, ANN202
            phase = kwargs["phase"]
            return (
                CONTROLLER.VerifiedPhaseCompletion(
                    phase=phase,
                    evidence_sha256="a" * 64,
                    receipt_sha256="b" * 64,
                ),
                canonical({"phase": phase}) + b"\n",
            )

        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    side_effect=(
                        sources,
                        sources,
                        sources,
                        sources,
                        MODULE.FreezeSnapshotPhaseBridgeError(
                            "completed current frozen verification is outside phase age"
                        ),
                    ),
                ) as validate_sources,
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "closure.json", "f" * 64),
                ),
                mock.patch.object(MODULE, "_assert_records_unchanged"),
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=prepared,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_run_release_phase_verifier",
                    side_effect=verifier,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_persist_phase_verification_receipt",
                ) as persist_receipt,
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeError,
                    "outside phase age",
                ),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(validate_sources.call_count, 5)
            persist_receipt.assert_not_called()
            self.assertEqual(fake_journal.complete_calls, [])
            self.assertEqual(fake_journal.state["status"], "phase_started")
            self.assertEqual(
                fake_journal.state["started_phase"],
                MODULE.PHASES[0],
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_source_expiry_after_receipt_persistence_leaves_phase_started(
        self,
    ):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()

        def prepared(
            _context,
            *,
            phase,
            **_kwargs,
        ):  # noqa: ANN001, ANN003, ANN202
            return (
                self.root / f"{phase}.json",
                {
                    role: self.root / f"{phase}-{role}.json"
                    for role in next(
                        spec.roles
                        for spec in CONTROLLER.PHASE_SPECS
                        if spec.phase == phase
                    )
                },
                {
                    claim: self.root / f"{phase}-{claim}.json"
                    for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                },
                {"phase": phase},
            )

        def verifier(**kwargs):  # noqa: ANN003, ANN202
            phase = kwargs["phase"]
            return (
                CONTROLLER.VerifiedPhaseCompletion(
                    phase=phase,
                    evidence_sha256="a" * 64,
                    receipt_sha256="b" * 64,
                ),
                canonical({"phase": phase}) + b"\n",
            )

        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    side_effect=(
                        sources,
                        sources,
                        sources,
                        sources,
                        sources,
                        MODULE.FreezeSnapshotPhaseBridgeError(
                            "completed current frozen verification is outside phase age"
                        ),
                    ),
                ) as validate_sources,
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "closure.json", "f" * 64),
                ),
                mock.patch.object(MODULE, "_assert_records_unchanged"),
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=prepared,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_run_release_phase_verifier",
                    side_effect=verifier,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_persist_phase_verification_receipt",
                ) as persist_receipt,
                self.assertRaisesRegex(
                    MODULE.FreezeSnapshotPhaseBridgeError,
                    "outside phase age",
                ),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(validate_sources.call_count, 6)
            persist_receipt.assert_called_once()
            self.assertEqual(fake_journal.complete_calls, [])
            self.assertEqual(fake_journal.state["status"], "phase_started")
            self.assertEqual(
                fake_journal.state["started_phase"],
                MODULE.PHASES[0],
            )
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_generated_evidence_passes_semantic_verifier_for_all_three_phases(self):
        from tests import (  # noqa: PLC0415
            test_verify_production_shadow_phase_evidence as VERIFY_FIXTURE,
        )

        now = datetime.now(timezone.utc)
        base_paths: dict[str, Path] = {}
        evidence_sha256: dict[str, str] = {}
        for phase in CONTROLLER.PHASES[: MODULE.FIRST_PHASE_INDEX]:
            record = private_json(
                self.root / f"prior-{phase}.json",
                VERIFY_FIXTURE.evidence_for(
                    phase,
                    captured_at=now,
                ),
            )
            base_paths[phase] = record.path
            evidence_sha256[phase] = record.sha256
        request = private_json(
            self.root / "semantic-request.json",
            {"source": "semantic-test"},
        )
        semantic_manifest = {
            **manifest(self.root),
            "topology": json.loads(
                json.dumps(CONTROLLER.EXPECTED_TOPOLOGY)
            ),
            "artifacts": dict(VERIFY_FIXTURE.MANIFEST_ARTIFACTS),
        }
        semantic_context = MODULE.BridgeContext(
            request=request,
            manifest_path=self.root / "manifest.json",
            approval_path=self.root / "approval.json",
            approval_policy_path=self.root / "policy.json",
            manifest=semantic_manifest,
            manifest_sha256=VERIFY_FIXTURE.MANIFEST_SHA256,
            plan={"plan_sha256": VERIFY_FIXTURE.PLAN_SHA256},
            plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
            output_root=self.root / "semantic-output",
            prior_paths=base_paths,
        )
        sources = validated_sources(self.root)
        evidence_paths = dict(base_paths)
        completed = list(
            CONTROLLER.PHASES[: MODULE.FIRST_PHASE_INDEX]
        )
        for phase in MODULE.PHASES:
            state = {
                **MODULE._journal_bindings(semantic_context),
                "status": "phase_started",
                "completed_phases": list(completed),
                "phase_evidence_sha256": dict(evidence_sha256),
                "phase_verification_sha256": {
                    item: "9" * 64 for item in completed
                },
                "started_phase": phase,
                "rollback_eligible": True,
                "first_business_write_allowed": False,
                "events": [{"kind": "test"}],
                "state_sha256": "8" * 64,
                "event_tail_sha256": "9" * 64,
            }
            (
                evidence_path,
                role_paths,
                claim_paths,
                _aggregate,
            ) = MODULE._prepare_phase_evidence(
                semantic_context,
                phase=phase,
                state=state,
                evidence_paths=evidence_paths,
                sources=sources,
                now=now,
            )
            evidence, evidence_digest = (
                VERIFY.read_root_only_evidence(evidence_path)
            )
            spec = next(
                item
                for item in CONTROLLER.PHASE_SPECS
                if item.phase == phase
            )
            request_hashes: dict[str, str] = {}
            source_hashes: dict[str, str] = {}
            observed_at: dict[str, str] = {}
            for role in spec.roles:
                role_record = MODULE._read_private_json(
                    role_paths[role],
                    label=f"{role} semantic role validation",
                )
                request_hashes[role] = role_record.document[
                    "request_sha256"
                ]
                source_hashes[role] = role_record.sha256
                observed_at[role] = role_record.document["observed_at"]
            dynamic: dict[str, object] = {}
            claim_hashes: dict[str, str] = {}
            for claim, rule in VERIFY.PHASE_CLAIM_RULES[phase].items():
                claim_record = MODULE._read_private_json(
                    claim_paths[claim],
                    label=f"{claim} semantic claim source",
                )
                claim_hashes[claim] = claim_record.sha256
                if rule.kind != "exact":
                    dynamic[claim] = claim_record.document["value"]
            prior_records: dict[str, dict[str, object]] = {}
            for prior in completed:
                prior_document, prior_digest = (
                    VERIFY.read_root_only_evidence(evidence_paths[prior])
                )
                prior_records[prior] = {
                    "document": prior_document,
                    "file_sha256": prior_digest,
                }
            verified = VERIFY.verify_phase_evidence(
                evidence,
                expected_phase=phase,
                expected_campaign_id=CAMPAIGN_ID,
                expected_operation_id=OPERATION_ID,
                expected_release_sha=RELEASE_SHA,
                expected_legacy_release_sha=LEGACY_RELEASE_SHA,
                expected_manifest_sha256=(
                    VERIFY_FIXTURE.MANIFEST_SHA256
                ),
                expected_plan_sha256=VERIFY_FIXTURE.PLAN_SHA256,
                expected_approval_sha256=(
                    VERIFY_FIXTURE.APPROVAL_SHA256
                ),
                expected_phase_evidence_schema_sha256=(
                    VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
                ),
                expected_manifest_artifacts=dict(
                    VERIFY_FIXTURE.MANIFEST_ARTIFACTS
                ),
                expected_role_request_sha256=request_hashes,
                expected_role_source_artifact_sha256=source_hashes,
                expected_role_observed_at=observed_at,
                expected_dynamic_claim_values=dynamic,
                expected_claim_source_sha256=claim_hashes,
                expected_prior_phase_evidence_sha256={
                    prior: evidence_sha256[prior]
                    for prior in completed
                },
                prior_phase_evidence_records=prior_records,
                now=now,
                evidence_file_sha256=evidence_digest,
            )
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(verified["phase"], phase)
            evidence_paths[phase] = evidence_path
            evidence_sha256[phase] = evidence_digest
            completed.append(phase)

    def test_baseexception_after_begin_never_runs_restore_or_restart(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ),
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "closure.json", "f" * 64),
                ),
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=KeyboardInterrupt("host lost"),
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(
                fake_journal.state["started_phase"],
                "stop_legacy_writers",
            )
            self.assertEqual(fake_journal.complete_calls, [])
            source = Path(MODULE.__file__).read_text(encoding="utf-8")
            self.assertNotIn("FREEZE.execute(", source)
            self.assertNotIn("_set_container_running(", source)
            self.assertNotIn(".restore(", source)
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_signal_during_complete_is_deferred_until_journal_reconciles(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)

        class SignalJournal(FakeJournal):
            def complete_phase(self, phase: str, *, verification) -> dict:  # noqa: ANN001
                os.kill(os.getpid(), signal.SIGTERM)
                return super().complete_phase(
                    phase,
                    verification=verification,
                )

        fake_journal = SignalJournal(self.root)
        read_fd, holder = external_liveness_pipe()

        def prepared(
            _context,
            *,
            phase,
            **_kwargs,
        ):  # noqa: ANN001, ANN003, ANN202
            spec = next(
                item
                for item in CONTROLLER.PHASE_SPECS
                if item.phase == phase
            )
            return (
                self.root / f"{phase}.json",
                {
                    role: self.root / f"{phase}-{role}.json"
                    for role in spec.roles
                },
                {
                    claim: self.root / f"{phase}-{claim}.json"
                    for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                },
                {"phase": phase},
            )

        def verifier(**kwargs):  # noqa: ANN003, ANN202
            phase = kwargs["phase"]
            return (
                CONTROLLER.VerifiedPhaseCompletion(
                    phase=phase,
                    evidence_sha256=hashlib.sha256(
                        phase.encode("ascii")
                    ).hexdigest(),
                    receipt_sha256="e" * 64,
                ),
                canonical({"phase": phase}) + b"\n",
            )

        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ),
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=prepared,
                ),
                mock.patch.object(
                    MODULE,
                    "_assert_records_unchanged",
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_run_release_phase_verifier",
                    side_effect=verifier,
                ),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "_persist_phase_verification_receipt",
                ),
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(self.root / "aggregate.json", "f" * 64),
                ),
                self.assertRaises(
                    MODULE.FreezeSnapshotPhaseBridgeCancellation
                ),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(
                fake_journal.complete_calls,
                ["stop_legacy_writers"],
            )
            self.assertIn(
                "stop_legacy_writers",
                fake_journal.state["completed_phases"],
            )
            self.assertEqual(fake_journal.state["status"], "active")
            self.assertIsNone(fake_journal.state["started_phase"])
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_phase_one_is_never_auto_begun_by_the_bridge(self):
        bridge_context = context(self.root)
        sources = validated_sources(self.root)
        fake_journal = FakeJournal(self.root)
        read_fd, holder = external_liveness_pipe()
        try:
            with (
                mock.patch.object(
                    MODULE,
                    "_load_request",
                    return_value=bridge_context,
                ),
                mock.patch.object(MODULE, "_verify_authorization"),
                mock.patch.object(
                    MODULE.CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=fake_journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_sources",
                    return_value=sources,
                ),
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=KeyboardInterrupt("stop after prestarted phase"),
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                MODULE.execute(
                    self.root / "ignored.json",
                    apply=True,
                    confirm=MODULE.confirmation_phrase(bridge_context),
                    control_fd=read_fd,
                )
            self.assertEqual(
                fake_journal.begin_calls,
                [],
            )
            self.assertEqual(
                fake_journal.state["started_phase"],
                "stop_legacy_writers",
            )
            self.assertEqual(fake_journal.state["status"], "phase_started")
        finally:
            os.close(read_fd)
            stop_holder(holder)

    def test_apply_failure_reports_unknown_journal_reconciliation_state(self):
        output = io.StringIO()
        with (
            mock.patch.object(
                MODULE,
                "execute",
                side_effect=MODULE.FreezeSnapshotPhaseBridgeError(
                    "failed"
                ),
            ),
            redirect_stdout(output),
        ):
            status = MODULE.main(
                [
                    "--request",
                    str(self.root / "request.json"),
                    "--apply",
                    "--confirm",
                    "confirmation",
                    "--control-fd",
                    "7",
                ]
            )
        self.assertEqual(status, 2)
        document = json.loads(output.getvalue())
        self.assertIsNone(document["journal_mutated"])
        self.assertIsNone(document["reconciliation_required"])
        self.assertTrue(document["legacy_writers_may_be_frozen"])

    def test_publication_is_create_only_and_reuses_only_exact_bytes(self):
        directory = self.root / "private"
        directory.mkdir(mode=0o700)
        path, digest = MODULE._persist_document(
            directory,
            prefix="evidence",
            document={"value": 1},
        )
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        reused, reused_digest = MODULE._persist_document(
            directory,
            prefix="evidence",
            document={"value": 1},
        )
        self.assertEqual((reused, reused_digest), (path, digest))
        path.write_bytes(canonical({"value": 2}) + b"\n")
        path.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotPhaseBridgeError,
            "differs",
        ):
            MODULE._persist_document(
                directory,
                prefix="evidence",
                document={"value": 1},
            )


if __name__ == "__main__":
    unittest.main()
