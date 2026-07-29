from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_convergence_gate as MODULE
from scripts.production_shadow_prepared_clone_errors import PreparedCloneInventoryError


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def context(root: Path, *, started: bool = True) -> MODULE.EvidenceContext:
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700, parents=True)
    manifest = {
        "campaign_id": "7fb08095-7a9e-4a92-9fa9-3f9a301b2944",
        "operation_id": "7fb08095-7a9e-4a92-9fa9-3f9a301b2945",
        "release_sha": "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2",
        "release_tree_sha": "a" * 40,
        "legacy_release_sha": "b" * 40,
        "artifacts": {
            "cutover_approval_sha256": "1" * 64,
            "human_approval_policy_sha256": "2" * 64,
            "host_agent_sha256": "3" * 64,
            "phase_evidence_schema_sha256": "4" * 64,
        },
        "deployment": {
            "controller_evidence_root": str(evidence),
            "controller_journal_path": str(root / "journal.json"),
        },
    }
    prefix = list(MODULE.PRIOR_PHASES)
    journal = {
        "completed_phases": prefix,
        "status": "phase_started" if started else "active",
        "started_phase": MODULE.PHASE if started else None,
        "started_at": NOW.isoformat() if started else None,
    }
    return MODULE.EvidenceContext(
        manifest_path=root / "manifest.json",
        approval_path=root / "approval.json",
        approval_policy_path=root / "policy.json",
        journal_path=root / "journal.json",
        manifest=manifest,
        manifest_sha256="5" * 64,
        plan={"plan_sha256": "6" * 64},
        plan_sha256="6" * 64,
        journal=journal,
        prior_paths={},
        prior_digests={},
        prior_records={},
        evidence_root=evidence,
    )


def identity(ctx: MODULE.EvidenceContext) -> dict[str, object]:
    return {
        "campaign_id": ctx.manifest["campaign_id"],
        "operation_id": ctx.manifest["operation_id"],
        "release_sha": ctx.manifest["release_sha"],
        "release_tree_sha": ctx.manifest["release_tree_sha"],
        "manifest_sha256": ctx.manifest_sha256,
        "plan_sha256": ctx.plan_sha256,
        "approval_sha256": ctx.manifest["artifacts"]["cutover_approval_sha256"],
    }


def write_private(path: Path, value: object) -> MODULE.Reference:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = canonical(value) + b"\n"
    path.write_bytes(payload)
    path.chmod(0o600)
    return MODULE.Reference(path, hashlib.sha256(payload).hexdigest())


class ConvergenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.context = context(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_three_site_tls_pairs_exclude_witness(self) -> None:
        self.assertEqual(
            MODULE.TLS_PAIRS,
            {
                ("bot_fi", "webapp_fi"),
                ("bot_fi", "webapp_ir"),
                ("webapp_fi", "bot_fi"),
                ("webapp_fi", "webapp_ir"),
                ("webapp_ir", "bot_fi"),
                ("webapp_ir", "webapp_fi"),
            },
        )
        self.assertFalse(any("witness" in pair for pair in MODULE.TLS_PAIRS))

    def test_tls_observation_requires_exact_three_site_peer_coverage(self) -> None:
        peers = [
            {
                "origin_role": origin,
                "destination_role": destination,
                "protocol": "TLSv1.3",
                "status_code": 200,
                "certificate_sha256": "a" * 64,
                "peer_handshake_sha256": "b" * 64,
                "ca_bundle_sha256": "c" * 64,
            }
            for origin, destination in sorted(MODULE.TLS_PAIRS)
        ]
        document = {
            "schema": MODULE.TLS_OBSERVATION_SCHEMA,
            "status": "observed",
            **identity(self.context),
            "observed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "peers": peers,
            "peer_set_sha256": digest(peers),
        }
        self.assertEqual(
            MODULE._validate_tls_observation(document, context=self.context),
            NOW + timedelta(seconds=1),
        )
        hostile = json.loads(json.dumps(document))
        hostile["peers"].append(
            {
                **peers[0],
                "origin_role": "witness",
                "destination_role": "webapp_fi",
            }
        )
        hostile["peer_set_sha256"] = digest(hostile["peers"])
        with self.assertRaises(MODULE.ConvergenceGateError):
            MODULE._validate_tls_observation(hostile, context=self.context)

    def test_witness_live_observation_is_separate_signed_source(self) -> None:
        proof = {
            "lease_id": "lease-1",
            "holder_site": "webapp_fi",
            "writer_epoch": 1,
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            "witness_transition_id": "transition-1",
            "signature": "signature",
        }
        readback = {
            "proof_sha256": MODULE._proof_sha256(proof),
            "status_receipt_sha256": "b" * 64,
            "observed_at": (NOW + timedelta(seconds=1)).isoformat(),
        }
        document = {
            "schema": MODULE.WITNESS_OBSERVATION_SCHEMA,
            "status": "observed",
            **identity(self.context),
            "observed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "witness_public_key": "public-key",
            "witness_public_key_sha256": hashlib.sha256(b"public-key").hexdigest(),
            "signed_proof": proof,
            "signed_proof_sha256": MODULE._proof_sha256(proof),
            "witness_status_receipt_sha256": "b" * 64,
            "lease_live_readback_sha256": digest(readback),
        }

        class Verified:
            canonical_payload = proof

        with (
            mock.patch.object(MODULE, "witness_public_key_is_valid", return_value=True),
            mock.patch.object(MODULE, "validate_witness_lease_proof", return_value=Verified()),
        ):
            self.assertEqual(
                MODULE._validate_witness_observation(
                    document,
                    context=self.context,
                    now=NOW + timedelta(seconds=2),
                    require_fresh=True,
                ),
                NOW + timedelta(seconds=1),
            )

    def test_old_source_fails_without_using_mtime(self) -> None:
        with self.assertRaises(MODULE.ConvergenceSourceUnavailable):
            MODULE._validate_source_times(
                {"database": NOW - timedelta(minutes=16)},
                phase_started_at=NOW - timedelta(minutes=20),
                now=NOW,
                require_fresh=True,
            )
        # The only accepted freshness value is the record timestamp.  File
        # metadata is deliberately absent from this API.
        self.assertEqual(
            MODULE._validate_source_times(
                {"database": NOW - timedelta(seconds=1)},
                phase_started_at=NOW - timedelta(minutes=1),
                now=NOW,
                require_fresh=True,
            ),
            NOW - timedelta(seconds=1),
        )

    def test_source_record_requires_prestarted_journal_before_loading_source(self) -> None:
        not_started = context(self.root / "not-started", started=False)
        source = MODULE.Reference(self.root / "missing.json", "a" * 64)
        with mock.patch.object(MODULE, "_validate_context", side_effect=MODULE.ConvergenceGateError("not prestarted")) as checked:
            with self.assertRaisesRegex(MODULE.ConvergenceGateError, "not prestarted"):
                MODULE.prepare_source_record(not_started, source_set=source, now=NOW)
        self.assertEqual(checked.call_args.kwargs["required_position"], "started")

    def test_apply_never_auto_begins_a_ready_journal(self) -> None:
        ready = context(self.root / "ready", started=False)
        request = mock.Mock(path=self.root / "request.json", sha256="a" * 64)
        source = mock.Mock(path=self.root / "source.json", sha256="b" * 64)
        source.document = {"source_binding_sha256": "c" * 64}
        source_set = mock.Mock()

        class Journal:
            begin_phase = mock.Mock(side_effect=AssertionError("must not begin"))

            def assert_bindings(self, **_kwargs):
                return ready.journal

        def fail_started(_context, *, required_position="any"):
            if required_position == "started":
                raise MODULE.ConvergenceGateError("journal was not prestarted")
            return {}, {}

        journal = Journal()
        with (
            mock.patch.object(MODULE, "load_request", return_value=(ready, request, source, source_set)),
            mock.patch.object(MODULE, "_validate_context", side_effect=fail_started),
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceGateError, "not prestarted"):
                MODULE.apply_phase(
                    self.root / "request.json",
                    confirm="unused",
                    control_fd=0,
                    journal_factory=lambda _path: journal,
                )
        journal.begin_phase.assert_not_called()

    def test_apply_with_injected_dependencies_does_not_import_prepared_runtime(self) -> None:
        started = context(self.root / "started", started=True)
        request = mock.Mock(path=self.root / "request.json", sha256="a" * 64)
        source = mock.Mock(path=self.root / "source.json", sha256="b" * 64)
        source.document = {"source_binding_sha256": "c" * 64}
        source_set = mock.Mock()

        class Journal:
            def assert_bindings(self, **_kwargs):
                return started.journal

        original_import = __import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                name == "scripts.orchestrate_production_shadow_prepared_clone_inventory"
                or (
                    name == "scripts"
                    and "orchestrate_production_shadow_prepared_clone_inventory" in fromlist
                )
            ):
                raise AssertionError("prepared runtime must remain lazy")
            return original_import(name, globals, locals, fromlist, level)

        with (
            mock.patch.object(MODULE, "load_request", return_value=(started, request, source, source_set)),
            mock.patch.object(MODULE, "_validate_context", return_value=({}, {})),
            mock.patch.object(MODULE, "build_plan", return_value={"required_confirmation": "exact"}),
            mock.patch("builtins.__import__", side_effect=guarded_import),
        ):
            with self.assertRaisesRegex(MODULE.ConvergenceGateError, "dependency is unavailable"):
                MODULE.apply_phase(
                    self.root / "request.json",
                    confirm="exact",
                    control_fd=0,
                    liveness_factory=None,
                    signal_authority_factory=lambda: None,
                    journal_factory=lambda _path: Journal(),
                )

    def test_apply_normalizes_injected_prepared_clone_error_without_import(self) -> None:
        started = context(self.root / "started", started=True)
        request = mock.Mock(path=self.root / "request.json", sha256="a" * 64)
        source = mock.Mock(path=self.root / "source.json", sha256="b" * 64)
        source.document = {"source_binding_sha256": "c" * 64}
        source_set = mock.Mock()

        class Journal:
            def assert_bindings(self, **_kwargs):
                return started.journal

        class SignalAuthority:
            def __enter__(self):
                return self

            def __exit__(self, _type, _value, _traceback):
                return False

        class BrokenLiveness:
            def __enter__(self):
                raise PreparedCloneInventoryError("boom")

            def __exit__(self, _type, _value, _traceback):
                return False

        original_import = __import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                name == "scripts.orchestrate_production_shadow_prepared_clone_inventory"
                or (
                    name == "scripts"
                    and "orchestrate_production_shadow_prepared_clone_inventory" in fromlist
                )
            ):
                raise AssertionError("prepared runtime must remain lazy")
            return original_import(name, globals, locals, fromlist, level)

        with (
            mock.patch.object(MODULE, "load_request", return_value=(started, request, source, source_set)),
            mock.patch.object(MODULE, "_validate_context", return_value=({}, {})),
            mock.patch.object(MODULE, "build_plan", return_value={"required_confirmation": "exact"}),
            mock.patch("builtins.__import__", side_effect=guarded_import),
        ):
            with self.assertRaisesRegex(
                MODULE.ConvergenceGateError,
                "convergence phase apply failed closed",
            ):
                MODULE.apply_phase(
                    self.root / "request.json",
                    confirm="exact",
                    control_fd=0,
                    liveness_factory=lambda _fd: BrokenLiveness(),
                    signal_authority_factory=lambda: SignalAuthority(),
                    journal_factory=lambda _path: Journal(),
                )

    def test_missing_producer_source_set_fails_closed_without_live_tools(self) -> None:
        unavailable = MODULE.Reference(
            MODULE._canonical_source_set_path(self.context.manifest, "a" * 64),
            "a" * 64,
        )
        # Source validation must not call a host collector.  It only notices
        # that the immutable source producer output is missing.
        with (
            mock.patch.object(MODULE, "_validate_context", return_value=({}, {})),
            mock.patch.object(MODULE, "_private_directory", return_value=None),
            mock.patch.object(MODULE, "subprocess", create=True) as process,
        ):
            with self.assertRaises(MODULE.ConvergenceSourceUnavailable):
                MODULE._validate_source_set(
                    self.context,
                    unavailable,
                    now=NOW,
                    require_fresh=True,
                )
        self.assertFalse(process.mock_calls)

    def test_unavailable_plan_is_explicit_and_has_no_live_io(self) -> None:
        plan = MODULE.build_plan(context=None, source_available=False)
        self.assertFalse(plan["source_available"])
        self.assertFalse(plan["trusted_production_observer_available"])
        self.assertEqual(plan["missing_producer_behavior"], "fail-closed")
        self.assertFalse(plan["bridge_network_io"])
        self.assertFalse(plan["bridge_docker_io"])
        self.assertFalse(plan["bridge_ssh_io"])
        self.assertIsNone(plan["required_confirmation"])

    def test_gate_rejects_a_runtime_role_without_compose_receipt_closure(self) -> None:
        source_set = mock.Mock(
            role_validation={
                role: MODULE.Reference(
                    self.root / f"{role}.validation.json",
                    role[0] * 64,
                )
                for role in MODULE.ROLES
            }
        )
        with mock.patch.object(
            MODULE.VERIFY,
            "_read_role_validation_records",
            side_effect=MODULE.VERIFY.PhaseEvidenceError(
                "bot_fi convergence Compose execution proof closure differs"
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ConvergenceGateError,
                "role validation inputs are invalid",
            ):
                MODULE._read_role_validation_inputs(
                    self.context,
                    source_set,
                    now=NOW,
                )

    def test_source_set_uses_only_persisted_production_observations(self) -> None:
        phase_root = MODULE._phase_root(self.context.manifest)
        source_input = MODULE._source_input_root(self.context.manifest)
        source_sets = MODULE._source_set_root(self.context.manifest)
        role_root = MODULE._role_validation_root(self.context.manifest)
        observation_root = MODULE._observation_root(self.context.manifest)
        for directory in (phase_root, source_input, source_sets, role_root, observation_root):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        incoming_root = source_input / "incoming"
        incoming_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        incoming_root.chmod(0o700)
        for name in ("requests", "attestations", "transport-receipts"):
            directory = incoming_root / name
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        observed_at = (NOW + timedelta(seconds=1)).isoformat()
        roles = {
            role: write_private(
                MODULE._canonical_role_validation_path(
                    self.context.manifest, role=role, digest=(role[0] * 64)
                ),
                {"record": role},
            )
            for role in MODULE.ROLES
        }
        # File names are content-addressed, so build each record once then
        # move it to the digest-derived canonical target.
        normalized_roles = {}
        for role, reference in roles.items():
            actual = reference.sha256
            target = MODULE._canonical_role_validation_path(
                self.context.manifest, role=role, digest=actual
            )
            if reference.path != target:
                reference.path.replace(target)
            normalized_roles[role] = MODULE.Reference(target, actual)
        comparisons = [
            {
                "scope": scope,
                "source_site": source,
                "target_site": target,
                "table_set_sha256": "a" * 64,
                "source_business_fingerprint_sha256": "b" * 64,
                "target_business_fingerprint_sha256": "b" * 64,
                "source_row_count": 1,
                "target_row_count": 1,
                "table_count": 1,
                "business_drift_count": 0,
                "critical_drift_count": 0,
                "incomplete_count": 0,
                "local_only_difference_count": 0,
                "volatile_difference_count": 0,
            }
            for scope, source, target in sorted(MODULE.DATABASE_PAIRS)
        ]
        streams = [
            {
                "origin_site": origin,
                "destination_site": destination,
                "producer_epoch": 1,
                "source_sequence": 0,
                "received_sequence": 0,
                "applied_sequence": 0,
                "source_transaction_hash": "0" * 64,
                "received_transaction_hash": "0" * 64,
                "applied_transaction_hash": "0" * 64,
            }
            for origin, destination in sorted(MODULE.DR_PAIRS)
        ]
        blob_scopes = [
            {
                "scope": scope,
                "source_site": source,
                "target_site": target,
                "source_set_sha256": "c" * 64,
                "target_set_sha256": "c" * 64,
                "source_object_count": 0,
                "target_object_count": 0,
                "readback_sample_count": 0,
                "source_keyring_sha256": "d" * 64,
                "target_keyring_sha256": "d" * 64,
            }
            for scope, source, target in sorted(MODULE.BLOB_PAIRS)
        ]
        peers = [
            {
                "origin_role": origin,
                "destination_role": destination,
                "protocol": "TLSv1.3",
                "status_code": 200,
                "certificate_sha256": "e" * 64,
                "peer_handshake_sha256": "f" * 64,
                "ca_bundle_sha256": "a" * 64,
            }
            for origin, destination in sorted(MODULE.TLS_PAIRS)
        ]
        firewall_roles = {
            role: {
                "expected_allowlist_sha256": "b" * 64,
                "observed_allowlist_sha256": "b" * 64,
                "operation_rule_count": 1,
                "unexpected_destination_count": 0,
                "missing_destination_count": 0,
                "forbidden_egress_count": 0,
                "readback_sha256": "c" * 64,
            }
            for role in MODULE.ROLES
        }
        proof = {
            "proof": "opaque",
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        }
        witness_readback = {
            "proof_sha256": MODULE._proof_sha256(proof),
            "status_receipt_sha256": "d" * 64,
            "observed_at": observed_at,
        }
        documents = {
            "database_parity": {
                "schema": MODULE.DATABASE_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "comparisons": comparisons,
                "mismatch_count": 0, "database_state_sha256": "1" * 64,
            },
            "dr_convergence": {
                "schema": MODULE.DR_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "streams": streams,
                "conflict_count": 0, "dr_state_sha256": "2" * 64,
            },
            "blob_roundtrip": {
                "schema": MODULE.BLOB_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "object_storage_versioning": True,
                "missing_object_count": 0, "corrupt_object_count": 0,
                "scopes": blob_scopes, "blob_state_sha256": "3" * 64,
            },
            "queue_state": {
                "schema": MODULE.QUEUE_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "running_business_mutator_count": 0,
                "due_otp_job_count": 0, "inflight_effect_count": 0,
                "telegram_lease_count": 0, "provider_attempt_delta_count": 0,
                "queue_state_sha256": "4" * 64,
            },
            "dr_tls": {
                "schema": MODULE.TLS_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "peers": peers,
                "peer_set_sha256": digest(peers),
            },
            "destination_firewall": {
                "schema": MODULE.FIREWALL_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "roles": firewall_roles,
                "allowlist_set_sha256": digest(firewall_roles),
            },
            "witness_live": {
                "schema": MODULE.WITNESS_OBSERVATION_SCHEMA,
                "status": "observed", **identity(self.context),
                "observed_at": observed_at, "witness_public_key": "public-key",
                "witness_public_key_sha256": hashlib.sha256(b"public-key").hexdigest(),
                "signed_proof": proof, "signed_proof_sha256": MODULE._proof_sha256(proof),
                "witness_status_receipt_sha256": "d" * 64,
                "lease_live_readback_sha256": digest(witness_readback),
            },
        }
        observations = {}
        for label, document in documents.items():
            initial = write_private(observation_root / f"temporary-{label}.json", document)
            target = MODULE._canonical_observation_path(
                self.context.manifest, label=label, digest=initial.sha256
            )
            initial.path.replace(target)
            observations[label] = MODULE.Reference(target, initial.sha256)
        source_set_document = {
            "schema": MODULE.SOURCE_SET_SCHEMA,
            "status": "ready", **identity(self.context),
            "phase": MODULE.PHASE,
            "phase_started_at": self.context.journal["started_at"],
            "role_validation": {
                role: MODULE._reference_document(normalized_roles[role])
                for role in MODULE.ROLES
            },
            "observations": {
                label: MODULE._reference_document(observations[label])
                for label in MODULE.SOURCE_LABELS
            },
            "source_set_closure_sha256": MODULE._source_set_closure(
                phase_started_at=self.context.journal["started_at"],
                role_validation=normalized_roles,
                observations=observations,
            ),
        }
        first = write_private(source_sets / "temporary-source-set.json", source_set_document)
        source_set = MODULE.Reference(
            MODULE._canonical_source_set_path(self.context.manifest, first.sha256),
            first.sha256,
        )
        first.path.replace(source_set.path)
        role_requests = {role: (role[0] * 64) for role in MODULE.ROLES}
        role_sources = {role: normalized_roles[role].sha256 for role in MODULE.ROLES}
        role_times = {role: observed_at for role in MODULE.ROLES}

        class Verified:
            canonical_payload = proof

        with (
            mock.patch.object(MODULE, "_validate_context", return_value=({}, {})),
            mock.patch.object(MODULE.VERIFY, "_read_role_validation_records", return_value=(role_requests, role_sources, role_times)),
            mock.patch.object(MODULE, "witness_public_key_is_valid", return_value=True),
            mock.patch.object(MODULE, "validate_witness_lease_proof", return_value=Verified()),
        ):
            validated = MODULE._validate_source_set(
                self.context, source_set, now=NOW + timedelta(seconds=2), require_fresh=True
            )
        self.assertEqual(validated.captured_at, NOW + timedelta(seconds=1))
        self.assertEqual(set(validated.observations), set(MODULE.SOURCE_LABELS))
        self.assertFalse(hasattr(validated, "claims"))


if __name__ == "__main__":
    unittest.main()
