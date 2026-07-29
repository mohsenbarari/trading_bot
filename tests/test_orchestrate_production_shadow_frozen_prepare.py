from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_frozen_prepare as MODULE
from scripts import production_shadow_cutover_controller as CONTROLLER
from scripts import production_shadow_frozen_prepare_worker as WORKER


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
RELEASE_SHA = "1" * 40
TREE_SHA = "2" * 40
CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def context() -> MODULE.LoadedOrchestration:
    topology = {
        role: dict(CONTROLLER.EXPECTED_TOPOLOGY[role])
        for role in MODULE.PREPARE_ROLES
    }
    document = {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "controller_manifest_path": "/secure/controller-manifest.json",
        "controller_manifest_sha256": SHA_A,
        "plan_sha256": SHA_B,
        "approval_path": "/secure/approval.json",
        "approval_sha256": SHA_C,
        "approval_policy_path": "/secure/policy.json",
        "approval_policy_sha256": SHA_D,
        "ssh_identity_path": "/secure/id_ed25519",
        "known_hosts_path": "/secure/known_hosts",
        "host_inputs": {
            role: {
                "path": (
                    f"/root/secure-envs/trading-bot/"
                    "three-site-production-shadow/"
                    f"{OPERATION_ID}/frozen-final-generations/"
                    f"{SHA_E}/{WORKER.ROLE_PATHS[role]}/"
                    f"prepare-inputs/{role[0] * 64}.json"
                ),
                "sha256": role[0] * 64,
                "transport": topology[role]["transport"],
                "object_versions": (
                    object_versions() if role == "webapp_ir" else {}
                ),
            }
            for role in MODULE.PREPARE_ROLES
        },
    }
    manifest = {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "legacy_release_sha": "3" * 40,
        "topology": topology,
        "artifacts": {
            "cutover_approval_sha256": SHA_C,
            "human_approval_policy_sha256": SHA_D,
            "host_agent_sha256": SHA_A,
            "host_agent_contract_sha256": SHA_B,
            "phase_evidence_schema_sha256": SHA_E,
        },
        "deployment": {
            "controller_journal_path": "/secure/journal.json",
            "controller_evidence_root": "/secure/evidence",
        },
    }
    return MODULE.LoadedOrchestration(
        document=document,
        sha256=SHA_E,
        path=Path("/secure/request.json"),
        manifest=manifest,
        manifest_sha256=SHA_A,
        plan={"plan_sha256": SHA_B},
        output_root=Path("/secure/evidence/frozen-prepare"),
        prior_paths={},
    )


def object_versions() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for index, kind in enumerate(MODULE.TRANSPORT_OBJECT_KINDS, 1):
        rows[kind] = {
            "provider": "arvan-s3",
            "private": True,
            "versioned": True,
            "encryption": "age",
            "bucket": "private-stage",
            "recipient": "age1" + "q" * 58,
            "object_key": f"campaign/{kind}-{index}.age",
            "version_id": f"version-{index}",
            "ciphertext_sha256": f"{index:x}" * 64,
            "readback_receipt_sha256": f"{index + 4:x}" * 64,
            "exact_version_readback_verified": True,
            "payload_bytes_over_ssh": False,
            "presigned_url_persisted": False,
            "artifact_kind": kind,
            "artifact_sha256": f"{index + 8:x}" * 64,
        }
    return rows


def journal_state(
    phase: str,
    *,
    context_value: MODULE.LoadedOrchestration | None = None,
) -> dict[str, object]:
    loaded = context_value or context()
    prefix = list(
        CONTROLLER.PHASES[: CONTROLLER.PHASES.index(phase)]
    )
    return {
        **MODULE._journal_bindings(loaded),
        "status": "phase_started",
        "started_phase": phase,
        "completed_phases": prefix,
        "phase_evidence_sha256": {
            item: hashlib.sha256(item.encode("ascii")).hexdigest()
            for item in prefix
        },
        "rollback_eligible": True,
        "first_business_write_allowed": False,
        "state_sha256": SHA_C,
        "event_tail_sha256": SHA_D,
        "events": [{"kind": "phase_started"}],
    }


def intent(
    loaded: MODULE.LoadedOrchestration,
    *,
    phase: str,
    role: str,
) -> dict[str, object]:
    return {
        "campaign_id": loaded.document["campaign_id"],
        "operation_id": loaded.document["operation_id"],
        "role": role,
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "release_sha": loaded.document["release_sha"],
        "release_tree_sha": loaded.document["release_tree_sha"],
        "controller_manifest_sha256": loaded.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": loaded.document["plan_sha256"],
        "request_sha256": SHA_E,
        "restore_generation_sha256": SHA_D,
    }


def challenge(
    loaded: MODULE.LoadedOrchestration,
    *,
    phase: str,
    role: str,
    nonce: str = SHA_A,
) -> dict[str, object]:
    value = {
        "schema": WORKER.AUTHORITY_CHALLENGE_SCHEMA,
        "status": "challenge",
        **intent(loaded, phase=phase, role=role),
        "boundary": "before:migrate:attempt:1",
        "sequence": 1,
        "challenge_nonce": nonce,
        "previous_authority_sha256": MODULE.ZERO_SHA256,
        "publication_kind": None,
        "publication_payload_sha256": None,
    }
    assert set(value) == WORKER.AUTHORITY_CHALLENGE_FIELDS
    return value


def phase_result(
    phase: str,
    role: str,
    semantic: dict[str, object],
) -> dict[str, object]:
    return {
        "phase": phase,
        "role": role,
        "worker_result_sha256": hashlib.sha256(
            f"{phase}:{role}".encode("ascii")
        ).hexdigest(),
        "worker_return": {
            "result": {
                "semantic": semantic,
                "journal_tail_sha256": hashlib.sha256(
                    f"journal:{phase}:{role}".encode("ascii")
                ).hexdigest(),
            }
        },
    }


class ContractTests(unittest.TestCase):
    def test_exact_phases_and_roles_follow_controller(self) -> None:
        self.assertEqual(MODULE.PHASES, WORKER.PHASES)
        self.assertEqual(
            MODULE.PREPARE_ROLES,
            ("bot_fi", "webapp_fi", "webapp_ir"),
        )
        for phase in MODULE.PHASES:
            spec = next(
                item for item in CONTROLLER.PHASE_SPECS
                if item.phase == phase
            )
            self.assertEqual(WORKER.PHASE_ROLES[phase], spec.roles)
            self.assertNotIn("witness", WORKER.PHASE_ROLES[phase])

    def test_source_has_no_completion_buffered_subprocess_run(self) -> None:
        tree = ast.parse(Path(MODULE.__file__).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr in {"run", "call", "check_call", "check_output"}
        ]
        self.assertEqual(calls, [])

    def test_release_bootstrap_and_git_replace_defense_are_explicit(self) -> None:
        source = Path(MODULE.__file__).read_text(encoding="utf-8")
        bootstrap = "if str(REPO_ROOT) not in sys.path:"
        first_release_import = "from scripts import"
        self.assertLess(source.index(bootstrap), source.index(first_release_import))
        self.assertEqual(MODULE.SAFE_ENV["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_controller_request_binds_live_approval_files(self) -> None:
        self.assertTrue(
            {
                "approval_path",
                "approval_sha256",
                "approval_policy_path",
                "approval_policy_sha256",
            }.issubset(MODULE.ORCHESTRATION_REQUEST_FIELDS)
        )

    def test_host_command_uses_isolated_exact_python(self) -> None:
        loaded = context()
        argv = MODULE.session_arguments(
            loaded,
            phase="shadow_migrate",
            role="bot_fi",
            orchestrator_sha256=SHA_A,
        )
        self.assertEqual(argv[:3], ("/usr/bin/python3", "-I", "-B"))
        self.assertNotIn("witness", argv)

    def test_remote_command_has_no_payload_transport(self) -> None:
        loaded = context()
        argv = MODULE.session_arguments(
            loaded,
            phase="shadow_migrate",
            role="webapp_ir",
            orchestrator_sha256=SHA_A,
        )
        self.assertEqual(argv[0], "/usr/bin/ssh")
        joined = " ".join(argv).lower()
        self.assertNotIn("presigned", joined)
        self.assertNotIn("version_id=", joined)
        self.assertNotIn("payload", joined)
        self.assertIn("/usr/bin/python3", argv)
        python_index = argv.index("/usr/bin/python3")
        self.assertEqual(argv[python_index : python_index + 3], (
            "/usr/bin/python3",
            "-I",
            "-B",
        ))

    def test_witness_session_is_rejected(self) -> None:
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE.session_arguments(
                context(),
                phase="shadow_migrate",
                role="witness",
                orchestrator_sha256=SHA_A,
            )

    def test_webapp_ir_versions_require_all_four_distinct_objects(self) -> None:
        observed = MODULE._validate_object_versions(object_versions())
        self.assertEqual(set(observed), set(MODULE.TRANSPORT_OBJECT_KINDS))
        missing = object_versions()
        missing.pop("role_material")
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE._validate_object_versions(missing)

    def test_webapp_ir_version_id_reuse_is_rejected(self) -> None:
        rows = object_versions()
        rows["role_material"]["version_id"] = rows["release_bundle"][
            "version_id"
        ]
        rows["role_material"]["object_key"] = rows["release_bundle"][
            "object_key"
        ]
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE._validate_object_versions(rows)

    def test_orchestration_confirmation_binds_request_digest(self) -> None:
        loaded = context()
        self.assertEqual(
            MODULE.orchestration_confirmation(loaded),
            (
                "apply-production-shadow-frozen-prepare-orchestration:"
                f"{OPERATION_ID}:{SHA_E}"
            ),
        )

    def test_canonical_sorted_prior_mapping_loads_as_exact_phase_set(
        self,
    ) -> None:
        manifest = {
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
            "legacy_release_sha": "3" * 40,
            "topology": {
                role: dict(value)
                for role, value in CONTROLLER.EXPECTED_TOPOLOGY.items()
            },
            "artifacts": {
                "cutover_approval_sha256": SHA_C,
                "human_approval_policy_sha256": SHA_D,
            },
            "deployment": {
                "controller_evidence_root": "/secure/controller-evidence",
            },
        }
        prior_hashes = {
            phase: hashlib.sha256(phase.encode("ascii")).hexdigest()
            for phase in MODULE.PREPARE_PREFIX
        }
        host_inputs = {}
        for role in MODULE.PREPARE_ROLES:
            digest = hashlib.sha256(role.encode("ascii")).hexdigest()
            host_inputs[role] = {
                "path": (
                    f"/root/secure-envs/trading-bot/"
                    "three-site-production-shadow/"
                    f"{OPERATION_ID}/frozen-final-generations/"
                    f"{SHA_E}/{WORKER.ROLE_PATHS[role]}/"
                    f"prepare-inputs/{digest}.json"
                ),
                "sha256": digest,
                "transport": CONTROLLER.EXPECTED_TOPOLOGY[role][
                    "transport"
                ],
                "object_versions": (
                    object_versions() if role == "webapp_ir" else {}
                ),
            }
        document = {
            "schema": MODULE.ORCHESTRATION_REQUEST_SCHEMA,
            "status": "authorized-input",
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "release_tree_sha": TREE_SHA,
            "controller_manifest_path": "/secure/controller.json",
            "controller_manifest_sha256": SHA_A,
            "plan_sha256": SHA_B,
            "approval_path": "/secure/approval.json",
            "approval_sha256": SHA_C,
            "approval_policy_path": "/secure/policy.json",
            "approval_policy_sha256": SHA_D,
            "output_root": "/secure/controller-evidence/frozen-prepare",
            "ssh_identity_path": "/secure/id_ed25519",
            "ssh_identity_sha256": SHA_A,
            "known_hosts_path": "/secure/known_hosts",
            "known_hosts_sha256": SHA_B,
            "host_inputs": host_inputs,
            "prior_phase_evidence": {
                phase: {
                    "path": f"/secure/{phase}.json",
                    "sha256": prior_hashes[phase],
                }
                for phase in sorted(MODULE.PREPARE_PREFIX)
            },
            "constraints": {
                field: True
                for field in MODULE.ORCHESTRATION_CONSTRAINT_FIELDS
            },
        }
        payload = canonical(document) + b"\n"
        digest = hashlib.sha256(payload).hexdigest()
        request_path = Path(
            document["output_root"]
        ) / "requests" / f"orchestrate.{digest}.json"

        def prior(path: Path):
            phase = path.stem
            return (
                {
                    "phase": phase,
                    "campaign_id": CAMPAIGN_ID,
                    "operation_id": OPERATION_ID,
                    "release_sha": RELEASE_SHA,
                    "legacy_release_sha": "3" * 40,
                    "manifest_sha256": SHA_A,
                    "plan_sha256": SHA_B,
                    "status": "passed",
                    "business_write_observed": False,
                },
                prior_hashes[phase],
            )

        def file_hash(path: Path, **_kwargs: object) -> str:
            return {
                "/secure/approval.json": SHA_C,
                "/secure/policy.json": SHA_D,
                "/secure/id_ed25519": SHA_A,
                "/secure/known_hosts": SHA_B,
            }[os.fspath(path)]

        with (
            mock.patch.object(
                MODULE,
                "_secure_json",
                return_value=(document, payload, digest),
            ),
            mock.patch.object(
                CONTROLLER,
                "read_root_only_manifest",
                return_value=(manifest, SHA_A),
            ),
            mock.patch.object(
                CONTROLLER,
                "render_plan",
                return_value={"plan_sha256": SHA_B},
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "read_root_only_evidence",
                side_effect=prior,
            ),
            mock.patch.object(
                MODULE,
                "_hash_secure_file",
                side_effect=file_hash,
            ),
            mock.patch.object(MODULE, "load_host_input"),
        ):
            loaded = MODULE.load_orchestration_request(request_path)
        self.assertEqual(set(loaded.prior_paths), set(MODULE.PREPARE_PREFIX))
        self.assertEqual(loaded.sha256, digest)


class AuthorityTests(unittest.TestCase):
    def test_response_is_bound_to_exact_started_journal(self) -> None:
        loaded = context()
        phase = "shadow_migrate"
        role = "bot_fi"
        observed = MODULE._authority_response(
            challenge(loaded, phase=phase, role=role),
            context=loaded,
            state=journal_state(phase, context_value=loaded),
            intent=intent(loaded, phase=phase, role=role),
            seen_nonces=set(),
        )
        self.assertEqual(observed["journal_status"], "phase_started")
        self.assertEqual(observed["started_phase"], phase)
        self.assertTrue(observed["controller_lock_held"])
        self.assertFalse(observed["business_write_allowed"])

    def test_stale_journal_phase_is_rejected(self) -> None:
        loaded = context()
        phase = "shadow_migrate"
        stale = journal_state(phase, context_value=loaded)
        stale["started_phase"] = "shadow_roles_pre_migration"
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE._authority_response(
                challenge(loaded, phase=phase, role="bot_fi"),
                context=loaded,
                state=stale,
                intent=intent(loaded, phase=phase, role="bot_fi"),
                seen_nonces=set(),
            )

    def test_challenge_nonce_replay_is_rejected(self) -> None:
        loaded = context()
        phase = "shadow_migrate"
        nonce_set = {SHA_A}
        with self.assertRaisesRegex(
            MODULE.FrozenPrepareOrchestratorError,
            "replayed",
        ):
            MODULE._authority_response(
                challenge(loaded, phase=phase, role="bot_fi"),
                context=loaded,
                state=journal_state(phase, context_value=loaded),
                intent=intent(loaded, phase=phase, role="bot_fi"),
                seen_nonces=nonce_set,
            )

    def test_challenge_role_substitution_is_rejected(self) -> None:
        loaded = context()
        phase = "shadow_migrate"
        changed = challenge(loaded, phase=phase, role="bot_fi")
        changed["role"] = "webapp_fi"
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE._authority_response(
                changed,
                context=loaded,
                state=journal_state(phase, context_value=loaded),
                intent=intent(loaded, phase=phase, role="bot_fi"),
                seen_nonces=set(),
            )

    def test_publication_challenge_requires_exact_kind_and_digest(self) -> None:
        loaded = context()
        phase = "shadow_migrate"
        changed = challenge(loaded, phase=phase, role="bot_fi")
        changed["boundary"] = "publish:evidence"
        changed["publication_kind"] = "result"
        changed["publication_payload_sha256"] = SHA_B
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE._authority_response(
                changed,
                context=loaded,
                state=journal_state(phase, context_value=loaded),
                intent=intent(loaded, phase=phase, role="bot_fi"),
                seen_nonces=set(),
            )

    def test_transcript_is_hash_chained(self) -> None:
        loaded = context()
        first_challenge = challenge(
            loaded,
            phase="shadow_migrate",
            role="bot_fi",
        )
        first_response = {"response": 1}
        first = MODULE._transcript_entry(
            first_challenge,
            first_response,
            previous=MODULE.ZERO_SHA256,
            index=1,
        )
        second_challenge = dict(first_challenge)
        second_challenge["challenge_nonce"] = SHA_B
        second_challenge["sequence"] = 2
        second = MODULE._transcript_entry(
            second_challenge,
            {"response": 2},
            previous=first["entry_sha256"],
            index=2,
        )
        self.assertEqual(
            second["previous_entry_sha256"],
            first["entry_sha256"],
        )
        self.assertNotEqual(first["entry_sha256"], second["entry_sha256"])

    def test_control_reader_closes_liveness_on_controller_eof(self) -> None:
        input_read, input_write = os.pipe()
        live_read, live_write = os.pipe()
        stream = os.fdopen(input_read, "rb", buffering=0)
        reader = MODULE.HostControlReader(stream, live_write)
        try:
            reader.start()
            os.close(input_write)
            deadline = time.monotonic() + 2
            while not reader.failed.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(reader.failed.is_set())
            self.assertEqual(os.read(live_read, 1), b"")
        finally:
            reader.stop()
            stream.close()
            os.close(live_read)

    def test_control_reader_rejects_unsolicited_response(self) -> None:
        input_read, input_write = os.pipe()
        live_read, live_write = os.pipe()
        stream = os.fdopen(input_read, "rb", buffering=0)
        reader = MODULE.HostControlReader(stream, live_write)
        try:
            reader.start()
            os.write(input_write, b"{}\n")
            deadline = time.monotonic() + 2
            while not reader.failed.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(reader.failed.is_set())
        finally:
            os.close(input_write)
            reader.stop()
            stream.close()
            os.close(live_read)


class ClaimAggregationTests(unittest.TestCase):
    def test_pre_migration_aggregates_only_exact_webapp_roles(self) -> None:
        phase = "shadow_roles_pre_migration"
        results = {
            role: phase_result(
                phase,
                role,
                {
                    "least_privilege_role_set_verified": True,
                    "excessive_grant_count": 0,
                },
            )
            for role in WORKER.PHASE_ROLES[phase]
        }
        values = MODULE._phase_claim_values(
            phase,
            results,
            prior_records={},
        )
        self.assertEqual(values, {
            "least_privilege_role_set_verified": True,
            "excessive_grant_count": 0,
        })
        self.assertNotIn("bot_fi", results)

    def test_migration_requires_equal_schema_fingerprints(self) -> None:
        phase = "shadow_migrate"
        results = {}
        for index, role in enumerate(WORKER.PHASE_ROLES[phase]):
            results[role] = phase_result(
                phase,
                role,
                {
                    "alembic_chain_state": "target",
                    "current_revision": "target",
                    "target_revision": "target",
                    "off_chain_revision_count": 0,
                    "invalid_unready_index_count": 0,
                    "schema_fingerprint_sha256": (
                        SHA_A if index < 2 else SHA_B
                    ),
                },
            )
        prior = {
            "shadow_restore": {
                "document": {
                    "claims": {
                        "restore_result_set_sha256": {
                            "value": SHA_C,
                            "source_sha256": SHA_D,
                        }
                    }
                }
            }
        }
        with self.assertRaisesRegex(
            MODULE.FrozenPrepareOrchestratorError,
            "differs across",
        ):
            MODULE._phase_claim_values(
                phase,
                results,
                prior_records=prior,
            )

    def test_migration_binds_restore_claim_and_worker_journals(self) -> None:
        phase = "shadow_migrate"
        results = {
            role: phase_result(
                phase,
                role,
                {
                    "alembic_chain_state": "target",
                    "current_revision": "target",
                    "target_revision": "target",
                    "off_chain_revision_count": 0,
                    "invalid_unready_index_count": 0,
                    "schema_fingerprint_sha256": SHA_A,
                },
            )
            for role in WORKER.PHASE_ROLES[phase]
        }
        prior = {
            "shadow_restore": {
                "document": {
                    "claims": {
                        "restore_result_set_sha256": {
                            "value": SHA_C,
                            "source_sha256": SHA_D,
                        }
                    }
                }
            }
        }
        values = MODULE._phase_claim_values(
            phase,
            results,
            prior_records=prior,
        )
        self.assertEqual(values["restore_result_set_sha256"], SHA_C)
        self.assertEqual(values["schema_fingerprint_sha256"], SHA_A)
        self.assertRegex(
            str(values["migration_journal_sha256"]),
            r"^[0-9a-f]{64}$",
        )

    def test_post_migration_rejects_schema_substitution(self) -> None:
        phase = "shadow_roles_post_migration"
        results = {
            role: phase_result(
                phase,
                role,
                {
                    "least_privilege_role_set_verified": True,
                    "excessive_grant_count": 0,
                    "post_migration_grant_set_sha256": SHA_B,
                    "migrated_schema_fingerprint_sha256": SHA_C,
                },
            )
            for role in WORKER.PHASE_ROLES[phase]
        }
        prior = {
            "shadow_migrate": {
                "document": {
                    "claims": {
                        "schema_fingerprint_sha256": {
                            "value": SHA_A,
                            "source_sha256": SHA_D,
                        }
                    }
                }
            }
        }
        with self.assertRaisesRegex(
            MODULE.FrozenPrepareOrchestratorError,
            "differ.*from migrated schema",
        ):
            MODULE._phase_claim_values(
                phase,
                results,
                prior_records=prior,
            )

    def test_fence_aggregates_three_databases(self) -> None:
        phase = "shadow_fence"
        results = {
            role: phase_result(
                phase,
                role,
                {
                    "fenced_database_count": 1,
                    "unfenced_writer_count": 0,
                    "database_event_fence_verified": True,
                    "migrated_schema_fingerprint_sha256": SHA_A,
                    "fence_configuration_sha256": SHA_B,
                },
            )
            for role in WORKER.PHASE_ROLES[phase]
        }
        prior = {
            "shadow_migrate": {
                "document": {
                    "claims": {
                        "schema_fingerprint_sha256": {
                            "value": SHA_A,
                            "source_sha256": SHA_D,
                        }
                    }
                }
            }
        }
        values = MODULE._phase_claim_values(
            phase,
            results,
            prior_records=prior,
        )
        self.assertEqual(values["fenced_database_count"], 3)
        self.assertEqual(values["unfenced_writer_count"], 0)
        self.assertTrue(values["database_event_fence_verified"])

    def test_missing_role_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            MODULE.FrozenPrepareOrchestratorError,
            "roles are not exact",
        ):
            MODULE._phase_claim_values(
                "shadow_migrate",
                {},
                prior_records={},
            )


class ProcessTests(unittest.TestCase):
    def test_bounded_process_captures_incremental_output(self) -> None:
        control = MODULE.ProcessControl(
            argv=(
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "import sys; print('ok'); print('note', file=sys.stderr)",
            ),
            stdin=b"",
            timeout_seconds=5,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )
        result = MODULE.run_bounded_process(control)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"ok\n")
        self.assertEqual(result.stderr, b"note\n")
        self.assertTrue(result.process_group_cleanup_performed)

    def test_bounded_process_rejects_output_overflow(self) -> None:
        control = MODULE.ProcessControl(
            argv=(
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "print('x' * 10000)",
            ),
            stdin=b"",
            timeout_seconds=5,
            max_stdout_bytes=64,
            max_stderr_bytes=64,
        )
        result = MODULE.run_bounded_process(control)
        self.assertTrue(result.stdout_limit_exceeded)
        self.assertLessEqual(len(result.stdout), 64)

    def test_bounded_process_kills_detached_descendant(self) -> None:
        code = (
            "import subprocess,sys;"
            "p=subprocess.Popen(['/usr/bin/python3','-I','-B','-c',"
            "'import time; time.sleep(60)'],start_new_session=True);"
            "print(p.pid,flush=True)"
        )
        control = MODULE.ProcessControl(
            argv=("/usr/bin/python3", "-I", "-B", "-c", code),
            stdin=b"",
            timeout_seconds=5,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )
        result = MODULE.run_bounded_process(control)
        child_pid = int(result.stdout.strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and Path(
            f"/proc/{child_pid}"
        ).exists():
            time.sleep(0.02)
        self.assertFalse(Path(f"/proc/{child_pid}").exists())

    def test_process_control_rejects_shell(self) -> None:
        control = MODULE.ProcessControl(
            argv=("/bin/sh", "-c", "true"),
            stdin=b"",
            timeout_seconds=5,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorError):
            MODULE.run_bounded_process(control)

    def test_signal_guard_handles_sigint_and_restores_handler(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorCancellation):
            with MODULE._signal_cancellation_guard():
                os.kill(os.getpid(), signal.SIGINT)
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_signal_guard_suppresses_reentrant_cleanup_signal(self) -> None:
        before = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
        }
        with self.assertRaises(MODULE.FrozenPrepareOrchestratorCancellation):
            with MODULE._signal_cancellation_guard():
                handler = signal.getsignal(signal.SIGINT)
                self.assertTrue(callable(handler))
                try:
                    handler(signal.SIGINT, None)
                except MODULE.FrozenPrepareOrchestratorCancellation:
                    second = signal.getsignal(signal.SIGTERM)
                    self.assertTrue(callable(second))
                    second(signal.SIGTERM, None)
                    raise
        self.assertEqual(
            {
                signum: signal.getsignal(signum)
                for signum in before
            },
            before,
        )

    def test_signal_guard_rejects_non_main_thread(self) -> None:
        errors: list[BaseException] = []

        def target() -> None:
            try:
                with MODULE._signal_cancellation_guard():
                    pass
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(
            errors[0],
            MODULE.FrozenPrepareOrchestratorError,
        )

    def test_bounded_process_reaps_detached_zombie(self) -> None:
        before = MODULE._direct_child_baseline()
        code = (
            "import subprocess,time;"
            "subprocess.Popen(['/usr/bin/python3','-I','-B','-c','pass'],"
            "start_new_session=True);time.sleep(.2)"
        )
        result = MODULE.run_bounded_process(
            MODULE.ProcessControl(
                argv=("/usr/bin/python3", "-I", "-B", "-c", code),
                stdin=b"",
                timeout_seconds=5,
                max_stdout_bytes=1024,
                max_stderr_bytes=1024,
            )
        )
        self.assertEqual(result.returncode, 0)
        deadline = time.monotonic() + 2
        after = MODULE._direct_child_baseline()
        while after != before and time.monotonic() < deadline:
            time.sleep(0.02)
            after = MODULE._direct_child_baseline()
        self.assertEqual(after, before)

    def test_bounded_process_reaps_rapid_double_fork_zombies(self) -> None:
        before = MODULE._direct_child_baseline()
        code = (
            "import os,time;"
            "pid=os.fork();"
            "\nif pid==0:"
            "\n os.setsid(); child=os.fork();"
            "\n if child==0: os._exit(0)"
            "\n os._exit(0)"
            "\ntime.sleep(.2)"
        )
        result = MODULE.run_bounded_process(
            MODULE.ProcessControl(
                argv=("/usr/bin/python3", "-I", "-B", "-c", code),
                stdin=b"",
                timeout_seconds=5,
                max_stdout_bytes=1024,
                max_stderr_bytes=1024,
            )
        )
        self.assertEqual(result.returncode, 0)
        deadline = time.monotonic() + 2
        after = MODULE._direct_child_baseline()
        while after != before and time.monotonic() < deadline:
            time.sleep(0.02)
            after = MODULE._direct_child_baseline()
        self.assertEqual(after, before)

    def test_identity_bound_signal_refuses_reused_pid(self) -> None:
        identity = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=100,
            state="S",
        )
        reused = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=101,
            state="S",
        )
        with (
            mock.patch.object(
                MODULE,
                "_process_identity",
                return_value=reused,
            ),
            mock.patch.object(MODULE.os, "pidfd_open") as pidfd_open,
        ):
            MODULE._signal_identity(identity, signal.SIGKILL)
        pidfd_open.assert_not_called()

    def test_owned_processes_refuses_reused_root_pid(self) -> None:
        root = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=100,
            state="S",
        )
        reused = MODULE.ProcessIdentity(
            pid=4242,
            parent_pid=os.getpid(),
            process_group=4242,
            session_id=4242,
            start_time=101,
            state="S",
        )
        with mock.patch.object(
            MODULE,
            "_process_snapshot",
            return_value={reused.pid: reused},
        ):
            self.assertEqual(
                MODULE._owned_processes(
                    root,
                    baseline_children=frozenset(),
                ),
                set(),
            )

    def test_root_pidfd_contains_identity_acquisition_failure(self) -> None:
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(pid: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(pid, flags)
            opened.append((pid, descriptor))
            return descriptor

        with (
            mock.patch.object(
                MODULE,
                "_direct_child_baseline",
                return_value=frozenset(),
            ),
            mock.patch.object(
                MODULE,
                "_process_identity",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorError,
                "identity is unavailable",
            ),
        ):
            MODULE.run_bounded_process(
                MODULE.ProcessControl(
                    argv=(
                        "/usr/bin/python3",
                        "-I",
                        "-B",
                        "-c",
                        "import time;time.sleep(60)",
                    ),
                    stdin=b"",
                    timeout_seconds=5,
                    max_stdout_bytes=1024,
                    max_stderr_bytes=1024,
                )
            )
        self.assertEqual(len(opened), 1)
        pid, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{pid}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_default_session_factory_contains_identity_failure(self) -> None:
        opened: list[tuple[int, int]] = []
        real_pidfd_open = os.pidfd_open

        def capture_pidfd(pid: int, flags: int = 0) -> int:
            descriptor = real_pidfd_open(pid, flags)
            opened.append((pid, descriptor))
            return descriptor

        with (
            mock.patch.object(
                MODULE,
                "_process_identity",
                return_value=None,
            ),
            mock.patch.object(
                MODULE.os,
                "pidfd_open",
                side_effect=capture_pidfd,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorError,
                "identity is unavailable",
            ),
        ):
            MODULE._default_session_factory(
                (
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    "import time;time.sleep(60)",
                )
            )
        self.assertEqual(len(opened), 1)
        pid, descriptor = opened[0]
        self.assertFalse(Path(f"/proc/{pid}").exists())
        with self.assertRaises(OSError):
            os.fstat(descriptor)


class SessionLoopTests(unittest.TestCase):
    class Journal:
        def _lock(self) -> int:
            return os.open("/dev/null", os.O_RDONLY)

        def _read(self) -> dict[str, object]:
            return {}

    def setUp(self) -> None:
        self.authorization = mock.patch.object(
            CONTROLLER,
            "_verify_runtime_authorization",
        )
        self.authorization_mock = self.authorization.start()
        self.addCleanup(self.authorization.stop)

    @staticmethod
    def factory(script: str):
        def create(_argv: object) -> subprocess.Popen[bytes]:
            return subprocess.Popen(
                ["/usr/bin/python3", "-I", "-B", "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

        return create

    def test_incremental_intent_challenge_result_round_trip(self) -> None:
        script = (
            "import json,sys;"
            "w=sys.stdout.write;f=sys.stdout.flush;"
            f"w(json.dumps({{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})"
            "+'\\n');f();"
            f"w(json.dumps({{'schema':'{WORKER.AUTHORITY_CHALLENGE_SCHEMA}',"
            "'boundary':'b',"
            "'sequence':1})+'\\n');f();"
            "r=sys.stdin.readline();"
            "assert json.loads(r)['ok'] is True;"
            f"w(json.dumps({{'schema':'{MODULE.HOST_RESULT_SCHEMA}'}})"
            "+'\\n');f()"
        )
        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                return_value={"phase": "shadow_migrate"},
            ),
            mock.patch.object(
                MODULE,
                "_authority_response",
                return_value={"ok": True},
            ),
            mock.patch.object(
                MODULE,
                "_transcript_entry",
                return_value={"entry_sha256": SHA_A},
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_result",
                return_value={"status": "completed"},
            ),
        ):
            result = MODULE._run_host_session(
                context(),
                journal=self.Journal(),
                phase="shadow_migrate",
                role="bot_fi",
                orchestrator_sha256=SHA_A,
                prepare_worker_sha256=SHA_B,
                session_factory=self.factory(script),
            )
        self.assertEqual(result.document["status"], "completed")
        self.assertGreater(result.response_bytes, 0)
        self.assertTrue(result.process_tree_clean)
        self.authorization_mock.assert_called_once()

    def test_prebuffered_frames_past_authority_are_rejected(self) -> None:
        script = (
            "import json,sys;"
            f"rows=[{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}},"
            f"{{'schema':'{WORKER.AUTHORITY_CHALLENGE_SCHEMA}',"
            "'boundary':'b','sequence':1},"
            f"{{'schema':'{WORKER.AUTHORITY_CHALLENGE_SCHEMA}',"
            "'boundary':'c','sequence':2}];"
            "sys.stdout.write(''.join(json.dumps(x)+'\\n' for x in rows));"
            "sys.stdout.flush();sys.stdin.readline()"
        )
        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                return_value={"phase": "shadow_migrate"},
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorError,
                "prebuffered",
            ):
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=self.factory(script),
                )

    def test_expired_approval_cancels_live_authority_exchange(self) -> None:
        script = (
            "import json,sys;"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})+'\\n');"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{WORKER.AUTHORITY_CHALLENGE_SCHEMA}',"
            "'boundary':'b','sequence':1})+'\\n');"
            "sys.stdout.flush();sys.stdin.readline()"
        )
        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                return_value={"phase": "shadow_migrate"},
            ),
            mock.patch.object(
                CONTROLLER,
                "_verify_runtime_authorization",
                side_effect=CONTROLLER.CutoverContractError("expired"),
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorCancellation,
                "expired",
            ):
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=self.factory(script),
                )

    def test_stderr_is_bounded_and_rejected_on_success(self) -> None:
        script = (
            "import json,sys;"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})+'\\n');"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{MODULE.HOST_RESULT_SCHEMA}'}})+'\\n');"
            "sys.stdout.flush();sys.stderr.write('unexpected')"
        )
        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                return_value={"phase": "shadow_migrate"},
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_result",
                return_value={"status": "completed"},
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorError,
                "emitted stderr",
            ):
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=self.factory(script),
                )

    def test_eof_before_result_is_cancellation(self) -> None:
        script = (
            "import json; print(json.dumps("
            f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}}))"
        )
        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                return_value={"phase": "shadow_migrate"},
            ),
        ):
            with self.assertRaises(
                MODULE.FrozenPrepareOrchestratorCancellation
            ):
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=self.factory(script),
                )

    def test_detached_host_descendant_is_killed_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "child.pid"
            script = (
                "import json,pathlib,subprocess,sys;"
                "p=subprocess.Popen(['/usr/bin/python3','-I','-B','-c',"
                "'import time;time.sleep(60)'],start_new_session=True);"
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid));"
                "sys.stdout.write(json.dumps("
                f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})+'\\n');"
                "sys.stdout.write(json.dumps("
                f"{{'schema':'{MODULE.HOST_RESULT_SCHEMA}'}})+'\\n');"
                "sys.stdout.flush()"
            )
            with (
                mock.patch.object(
                    MODULE,
                    "session_arguments",
                    return_value=("/usr/bin/python3",),
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_host_intent",
                    return_value={"phase": "shadow_migrate"},
                ),
                mock.patch.object(
                    MODULE,
                    "_validate_host_result",
                    return_value={"status": "completed"},
                ),
            ):
                with self.assertRaisesRegex(
                    MODULE.FrozenPrepareOrchestratorError,
                    "did not exit|retained a descendant",
                ):
                    MODULE._run_host_session(
                        context(),
                        journal=self.Journal(),
                        phase="shadow_migrate",
                        role="bot_fi",
                        orchestrator_sha256=SHA_A,
                        prepare_worker_sha256=SHA_B,
                        session_factory=self.factory(script),
                    )
            child_pid = int(pid_path.read_text(encoding="ascii"))
            deadline = time.monotonic() + 2
            while (
                Path(f"/proc/{child_pid}").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())

    def test_baseexception_still_terminates_host_process_tree(self) -> None:
        class FatalAudit(BaseException):
            pass

        processes: list[subprocess.Popen[bytes]] = []
        script = (
            "import json,sys,time;"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})+'\\n');"
            "sys.stdout.flush();time.sleep(60)"
        )

        def factory(_argv: object) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                ["/usr/bin/python3", "-I", "-B", "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            processes.append(process)
            return process

        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                side_effect=FatalAudit("stop"),
            ),
        ):
            with self.assertRaises(FatalAudit):
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=factory,
                )
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(Path(f"/proc/{processes[0].pid}").exists())

    def test_cleanup_failure_does_not_replace_primary_baseexception(
        self,
    ) -> None:
        class FatalAudit(BaseException):
            pass

        class CleanupAudit(BaseException):
            pass

        processes: list[subprocess.Popen[bytes]] = []
        script = (
            "import json,sys,time;"
            "sys.stdout.write(json.dumps("
            f"{{'schema':'{MODULE.HOST_INTENT_SCHEMA}'}})+'\\n');"
            "sys.stdout.flush();time.sleep(60)"
        )

        def factory(_argv: object) -> subprocess.Popen[bytes]:
            process = subprocess.Popen(
                ["/usr/bin/python3", "-I", "-B", "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            processes.append(process)
            return process

        real_terminate = MODULE._terminate_process_tree

        def terminate_then_fail(*args: object, **kwargs: object) -> None:
            real_terminate(*args, **kwargs)
            raise CleanupAudit("cleanup audit failure")

        with (
            mock.patch.object(
                MODULE,
                "session_arguments",
                return_value=("/usr/bin/python3",),
            ),
            mock.patch.object(
                MODULE,
                "_validate_host_intent",
                side_effect=FatalAudit("primary audit failure"),
            ),
            mock.patch.object(
                MODULE,
                "_terminate_process_tree",
                side_effect=terminate_then_fail,
            ),
        ):
            with self.assertRaises(FatalAudit) as raised:
                MODULE._run_host_session(
                    context(),
                    journal=self.Journal(),
                    phase="shadow_migrate",
                    role="bot_fi",
                    orchestrator_sha256=SHA_A,
                    prepare_worker_sha256=SHA_B,
                    session_factory=factory,
                )
        self.assertIsInstance(raised.exception.__cause__, CleanupAudit)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(Path(f"/proc/{processes[0].pid}").exists())


class OrchestrationFlowTests(unittest.TestCase):
    class Journal:
        def __init__(self, loaded: MODULE.LoadedOrchestration) -> None:
            self.loaded = loaded
            self.completed = list(MODULE.PREPARE_PREFIX)
            self.phase_hashes = {
                phase: hashlib.sha256(phase.encode("ascii")).hexdigest()
                for phase in self.completed
            }
            self.status = "active"
            self.started_phase = None
            self.events = [{"kind": "existing"}]

        def state(self) -> dict[str, object]:
            return {
                **MODULE._journal_bindings(self.loaded),
                "status": self.status,
                "started_phase": self.started_phase,
                "completed_phases": list(self.completed),
                "phase_evidence_sha256": dict(self.phase_hashes),
                "rollback_eligible": True,
                "first_business_write_allowed": False,
                "state_sha256": SHA_C,
                "event_tail_sha256": SHA_D,
                "events": list(self.events),
            }

        def assert_bindings(self, **_kwargs: object) -> dict[str, object]:
            return self.state()

        def begin_phase(self, phase: str) -> dict[str, object]:
            self.status = "phase_started"
            self.started_phase = phase
            self.events.append({"kind": "phase_started", "phase": phase})
            return self.state()

        def complete_phase(
            self,
            phase: str,
            *,
            verification: CONTROLLER.VerifiedPhaseCompletion,
        ) -> dict[str, object]:
            self.completed.append(phase)
            self.phase_hashes[phase] = verification.evidence_sha256
            self.status = "active"
            self.started_phase = None
            self.events.append({"kind": "phase_completed", "phase": phase})
            return self.state()

    def test_controller_runs_exact_phase_role_matrix_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            loaded = replace(
                context(),
                output_root=Path(temporary) / "frozen-prepare",
                prior_paths={
                    phase: Path(temporary) / f"{phase}.json"
                    for phase in MODULE.PREPARE_PREFIX
                },
            )
            journal = self.Journal(loaded)

            def session(
                _context: object,
                *,
                phase: str,
                role: str,
                **_kwargs: object,
            ) -> MODULE.HostSessionResult:
                return MODULE.HostSessionResult(
                    document={"phase": phase, "role": role},
                    stdout_bytes=1,
                    stderr_bytes=0,
                    response_bytes=1,
                    process_tree_clean=True,
                    deadline_enforced=True,
                    stream_limits_enforced=True,
                )

            def evidence(
                _context: object,
                *,
                phase: str,
                **_kwargs: object,
            ):
                return (
                    Path(temporary) / f"{phase}.json",
                    {
                        role: Path(temporary) / f"{phase}-{role}.json"
                        for role in WORKER.PHASE_ROLES[phase]
                    },
                    {
                        claim: Path(temporary) / f"{phase}-{claim}.json"
                        for claim in MODULE.VERIFY.PHASE_CLAIM_RULES[phase]
                    },
                    {"phase": phase},
                )

            def verification(**kwargs: object):
                phase = str(kwargs["phase"])
                return (
                    CONTROLLER.VerifiedPhaseCompletion(
                        phase=phase,
                        evidence_sha256=hashlib.sha256(
                            f"evidence:{phase}".encode("ascii")
                        ).hexdigest(),
                        receipt_sha256=SHA_E,
                    ),
                    b"receipt\n",
                )

            with (
                mock.patch.object(
                    MODULE,
                    "load_orchestration_request",
                    return_value=loaded,
                ),
                mock.patch.object(
                    CONTROLLER,
                    "_verify_runtime_authorization",
                ),
                mock.patch.object(
                    MODULE,
                    "_release_artifact_hashes",
                    return_value={
                        "orchestrator": SHA_A,
                        "prepare_worker": SHA_B,
                    },
                ),
                mock.patch.object(
                    CONTROLLER,
                    "ProductionCutoverJournal",
                    return_value=journal,
                ),
                mock.patch.object(
                    MODULE,
                    "_run_host_session",
                    side_effect=session,
                ) as run_session,
                mock.patch.object(
                    MODULE,
                    "_prepare_phase_evidence",
                    side_effect=evidence,
                ),
                mock.patch.object(
                    CONTROLLER,
                    "_run_release_phase_verifier",
                    side_effect=verification,
                ),
                mock.patch.object(
                    CONTROLLER,
                    "_persist_phase_verification_receipt",
                ),
                mock.patch.object(
                    MODULE,
                    "_persist_document",
                    return_value=(
                        Path(temporary) / "aggregate.json",
                        SHA_A,
                        "created",
                    ),
                ),
            ):
                result = MODULE.execute_orchestration(
                    Path(temporary) / "request.json",
                    apply=True,
                    confirm=MODULE.orchestration_confirmation(loaded),
                )
        observed = [
            (
                call.kwargs["phase"],
                call.kwargs["role"],
            )
            for call in run_session.call_args_list
        ]
        expected = [
            (phase, role)
            for phase in MODULE.PHASES
            for role in WORKER.PHASE_ROLES[phase]
        ]
        self.assertEqual(observed, expected)
        self.assertNotIn("witness", [role for _phase, role in observed])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["next_phase"], "witness_lease")

    def test_phase_evidence_is_canonical_private_and_role_exact(self) -> None:
        phase = "shadow_roles_pre_migration"
        with tempfile.TemporaryDirectory() as temporary:
            loaded = replace(
                context(),
                output_root=Path(temporary) / "frozen-prepare",
            )
            results: dict[str, dict[str, object]] = {}
            for role in WORKER.PHASE_ROLES[phase]:
                value = phase_result(
                    phase,
                    role,
                    {
                        "least_privilege_role_set_verified": True,
                        "excessive_grant_count": 0,
                    },
                )
                value.update(
                    {
                        "request_sha256": hashlib.sha256(
                            f"request:{role}".encode("ascii")
                        ).hexdigest(),
                        "expected_host": loaded.manifest["topology"][
                            role
                        ]["host"],
                        "host_identity_observed": True,
                        "release_attestation_sha256": SHA_A,
                        "transport_manifest_sha256": (
                            SHA_B if role == "webapp_ir" else None
                        ),
                        "object_versions": (
                            object_versions()
                            if role == "webapp_ir"
                            else {}
                        ),
                    }
                )
                results[role] = value
            state = journal_state(phase, context_value=loaded)
            evidence_paths = {
                item: Path(temporary) / f"{item}.json"
                for item in MODULE.PREPARE_PREFIX
            }
            with (
                mock.patch.object(
                    MODULE,
                    "_load_prior_records",
                    return_value={},
                ),
                mock.patch.object(
                    MODULE.VERIFY,
                    "_derive_prior_claim_rows",
                    return_value=[],
                ),
            ):
                (
                    evidence_path,
                    role_paths,
                    claim_paths,
                    _aggregate,
                ) = MODULE._prepare_phase_evidence(
                    loaded,
                    phase=phase,
                    results=results,
                    journal_state=state,
                    evidence_paths=evidence_paths,
                )
            evidence, payload, digest = MODULE._secure_json(
                evidence_path,
                label="test phase evidence",
            )
            self.assertEqual(set(evidence), MODULE.VERIFY.EVIDENCE_FIELDS)
            self.assertEqual(
                [row["role"] for row in evidence["role_attestations"]],
                list(WORKER.PHASE_ROLES[phase]),
            )
            self.assertNotIn(
                "witness",
                [row["role"] for row in evidence["role_attestations"]],
            )
            self.assertEqual(
                set(evidence["claims"]),
                set(MODULE.VERIFY.PHASE_CLAIM_RULES[phase]),
            )
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
            self.assertEqual(
                os.stat(evidence_path, follow_symlinks=False).st_mode & 0o777,
                0o600,
            )
            self.assertEqual(set(role_paths), set(WORKER.PHASE_ROLES[phase]))
            self.assertEqual(
                set(claim_paths),
                set(MODULE.VERIFY.PHASE_CLAIM_RULES[phase]),
            )


class CliTests(unittest.TestCase):
    def test_plan_is_default_nonmutating_contract(self) -> None:
        planned = {
            "status": "planned",
            "runtime_mutated": False,
        }
        with mock.patch.object(
            MODULE,
            "execute_orchestration",
            return_value=planned,
        ) as execute:
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                code = MODULE.main(
                    ["controller", "--request", "/secure/request.json"]
                )
        self.assertEqual(code, 0)
        self.assertFalse(execute.call_args.kwargs["apply"])
        self.assertEqual(json.loads(output.getvalue()), planned)

    def test_apply_requires_exact_confirmation_in_api(self) -> None:
        loaded = context()
        with (
            mock.patch.object(
                MODULE,
                "load_orchestration_request",
                return_value=loaded,
            ),
            self.assertRaisesRegex(
                MODULE.FrozenPrepareOrchestratorError,
                "confirmation differs",
            ),
        ):
            MODULE.execute_orchestration(
                Path("/secure/request.json"),
                apply=True,
                confirm="wrong",
            )

    def test_host_cli_never_prints_result_twice(self) -> None:
        output = io.BytesIO()
        fake_stdin = SimpleNamespace(buffer=io.BytesIO())
        fake_stdout = SimpleNamespace(buffer=output)
        with (
            mock.patch.object(
                MODULE,
                "execute_host_session",
                return_value={"status": "completed"},
            ) as execute,
            mock.patch.object(MODULE.sys, "stdin", fake_stdin),
            mock.patch.object(MODULE.sys, "stdout", fake_stdout),
        ):
            code = MODULE.main(
                [
                    "host",
                    "--input-manifest",
                    "/secure/input.json",
                    "--input-sha256",
                    SHA_A,
                    "--role",
                    "bot_fi",
                    "--phase",
                    "shadow_migrate",
                    "--expected-orchestrator-sha256",
                    SHA_B,
                ]
            )
        self.assertEqual(code, 0)
        execute.assert_called_once()
        self.assertEqual(output.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
