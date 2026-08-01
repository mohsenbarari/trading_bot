"""Focused tests for the local-only strict runtime installation gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import unittest
from unittest.mock import patch

import core.physical_postgres_deployment_scaffold as scaffold
import core.physical_postgres_strict_runtime_installation_gate as gate


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _adapter_document(
    kind: str,
    *,
    profile: str,
    route_hash: str,
) -> dict[str, object]:
    adapter_id = f"{kind.replace('_', '-')}-adapter-0001"
    result: dict[str, object] = {
        "adapter_id": adapter_id,
        "site": (
            "webapp_fi"
            if kind
            in {"primary_term_guard", "wal_spool", "wal_uploader", "writer_ack"}
            else "webapp_ir"
        ),
        "contract": scaffold.ADAPTER_CONTRACTS[kind],
        "binary_path": (
            "/opt/trading-bot/physical-postgres/adapters/"
            f"{adapter_id}/{scaffold.ADAPTER_BINARY_NAMES[kind]}"
        ),
        "binary_sha256": digest(f"{kind}:binary"),
        "contract_sha256": digest(f"{kind}:contract"),
        "installation_attestation_sha256": digest(f"{kind}:attestation"),
        "route_binding_sha256": route_hash,
    }
    if kind == "writer_ack":
        result["writer_admission_integration_sha256"] = digest("writer-admission")
        if profile == scaffold.PROFILE_STRICT_ZERO_LOSS:
            result["acknowledgement_mode"] = scaffold.ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY
            result["strict_remote_durable_replay_identity_sha256"] = digest("strict-ack")
        else:
            result["acknowledgement_mode"] = scaffold.ACK_MODE_BOUNDED_RPO_ARCHIVE
            result["maximum_rpo_seconds"] = 30
    return result


def _manifest_document(
    *, profile: str = scaffold.PROFILE_STRICT_ZERO_LOSS
) -> dict[str, object]:
    route_hash = digest("route-binding")
    return {
        "schema": scaffold.PHYSICAL_POSTGRES_DEPLOYMENT_MANIFEST_SCHEMA,
        "mode": "default-off",
        "campaign_id": "physical-strict-runtime-20260731",
        "release_sha": "a" * 40,
        "postgres_image": (
            "registry.example/postgres@sha256:"
            "fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786"
        ),
        "postgres_major": 15,
        "postgres_runtime_identity": {
            "image_digest": "sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786",
            "platform": "linux/amd64",
            "effective_uid": 999,
            "effective_gid": 999,
            "attestation_sha256": digest("postgres-image-runtime"),
        },
        "deployment_profile": profile,
        "baseline": {
            "base_generation_id": "fi-base-generation-0001",
            "timeline": 1,
            "consistent_wal_lsn": "0/16B6C50",
            "base_backup_object_key": (
                "physical-postgres/physical-strict-runtime-20260731/"
                "fi-base-generation-0001/base.tar.age"
            ),
            "base_backup_object_version_id": "version-physical-base-0001",
            "base_backup_ciphertext_sha256": digest("base:ciphertext"),
            "base_backup_plaintext_sha256": digest("base:plaintext"),
        },
        "writer_term": {
            "holder_site": "webapp_fi",
            "writer_epoch": 41,
            "writer_lease_id": "writer-lease-00000041",
            "witness_transition_id": "witness-transition-00000041",
            "term_proof_sha256": digest("witness-term"),
        },
        "route": {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "delivery_route": "private-versioned-object-storage-pull-ack-v1",
            "route_binding_sha256": route_hash,
            "direct_fi_to_ir_ssh": False,
            "direct_fi_to_ir_scp": False,
            "direct_fi_to_ir_postgres_control": False,
        },
        "primary": {
            "site": "webapp_fi",
            "postgres_data_volume": "physical_fi_postgres_data",
            "postgres_socket_volume": "physical_fi_postgres_socket",
            "wal_spool_volume": "physical_fi_wal_spool",
            "adapter_state_volume": "physical_fi_adapter_state",
            "runtime_network_name": "physical_fi_runtime",
            "local_base_backup": {
                "transport": "unix-socket-only",
                "socket_directory": "/var/run/postgresql",
                "port": 5432,
                "replication_role": "physical_backup",
                "peer_os_users": ["postgres"],
                "max_wal_senders": 1,
                "tcp_hba": "reject",
                "helper_execution": "digest-pinned-image-attested-container-v1",
            },
        },
        "standby": {
            "site": "webapp_ir",
            "postgres_data_volume": "physical_ir_postgres_data",
            "restore_spool_volume": "physical_ir_restore_spool",
            "receiver_state_volume": "physical_ir_receiver_state",
            "runtime_network_name": "physical_ir_runtime",
        },
        "adapters": {
            kind: _adapter_document(kind, profile=profile, route_hash=route_hash)
            for kind in scaffold.ADAPTER_KINDS
        },
    }


class _StaticInspector:
    def __init__(
        self,
        records: dict[str, gate.PhysicalPostgresStrictRuntimeInstallationAttestation],
        *,
        absent_component: str | None = None,
    ) -> None:
        self.records = records
        self.absent_component = absent_component
        self.calls: list[tuple[str, object]] = []

    def inspect(self, *, component: str, attestation_path):
        self.calls.append((component, attestation_path))
        if component == self.absent_component:
            raise FileNotFoundError(component)
        return self.records[component]


class PhysicalPostgresStrictRuntimeInstallationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = scaffold.validate_physical_postgres_deployment_manifest(
            _manifest_document()
        )

    def _expectations(
        self, hashes: dict[str, str]
    ) -> dict[str, gate.StrictDurableReplayComponentExpectation]:
        return {
            component: gate.StrictDurableReplayComponentExpectation(
                component_id=f"{component}-runtime-0001",
                contract_sha256=digest(f"{component}:contract"),
                implementation_sha256=digest(f"{component}:implementation"),
                configuration_sha256=digest(f"{component}:configuration"),
                installation_attestation_sha256=hashes[component],
            )
            for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS
        }

    @staticmethod
    def _attestation_payload(
        *,
        request: gate.PhysicalPostgresStrictRuntimeInstallationRequest,
        component: str,
        expectation: gate.StrictDurableReplayComponentExpectation,
        attested_at: datetime,
        expires_at: datetime,
        changes: dict[str, object] | None = None,
    ) -> bytes:
        document: dict[str, object] = {
            "schema": gate.PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_SCHEMA,
            "version": 1,
            "status": "installed-default-off-not-launch-authorized",
            "component": component,
            "component_id": expectation.component_id,
            "contract_schema": gate.STRICT_DURABLE_REPLAY_COMPONENT_CONTRACT_SCHEMAS[
                component
            ],
            "contract_sha256": expectation.contract_sha256,
            "implementation_sha256": expectation.implementation_sha256,
            "configuration_sha256": expectation.configuration_sha256,
            "installation_binding_sha256": request.installation_binding_sha256,
            "manifest_lock_sha256": request.manifest_lock_sha256,
            "campaign_id": request.campaign_id,
            "release_sha": request.release_sha,
            "route_binding_sha256": request.route_binding_sha256,
            "writer_term_sha256": request.writer_term_sha256,
            "strict_remote_durable_replay_identity_sha256": (
                request.strict_remote_durable_replay_identity_sha256
            ),
            "writer_admission_integration_sha256": (
                request.writer_admission_integration_sha256
            ),
            "attested_at": attested_at.astimezone(timezone.utc).isoformat(),
            "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
            "direct_fi_to_ir_ssh": False,
            "direct_fi_to_ir_scp": False,
            "direct_fi_to_ir_postgres_control": False,
            "not_a_launch_authorization": True,
        }
        if changes is not None:
            document.update(changes)
        return gate.canonical_physical_postgres_strict_runtime_installation_attestation_bytes(
            document
        )

    def _scenario(
        self,
        *,
        document_changes: dict[str, dict[str, object]] | None = None,
        payload_transforms: dict[str, object] | None = None,
        attested_at: datetime = NOW - timedelta(seconds=1),
        expires_at: datetime = NOW + timedelta(seconds=60),
    ) -> tuple[
        gate.PhysicalPostgresStrictRuntimeInstallationRequest,
        _StaticInspector,
    ]:
        # The binding excludes the expected attestation hashes.  Build once to
        # obtain it, construct the canonical payloads, then bind their hashes.
        provisional = gate.build_physical_postgres_strict_runtime_installation_request(
            self.manifest,
            component_expectations=self._expectations(
                {
                    component: digest(f"provisional:{component}")
                    for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS
                }
            ),
        )
        raw_payloads: dict[str, bytes] = {}
        for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS:
            payload = self._attestation_payload(
                request=provisional,
                component=component,
                expectation=provisional.component(component),
                attested_at=attested_at,
                expires_at=expires_at,
                changes=(document_changes or {}).get(component),
            )
            transform = (payload_transforms or {}).get(component)
            if transform is not None:
                assert callable(transform)
                payload = transform(payload)
            raw_payloads[component] = payload
        request = gate.build_physical_postgres_strict_runtime_installation_request(
            self.manifest,
            component_expectations=self._expectations(
                {
                    component: hashlib.sha256(payload).hexdigest()
                    for component, payload in raw_payloads.items()
                }
            ),
        )
        self.assertEqual(
            provisional.installation_binding_sha256,
            request.installation_binding_sha256,
        )
        records = {
            component: gate.PhysicalPostgresStrictRuntimeInstallationAttestation(
                path=(
                    gate.FIXED_PHYSICAL_POSTGRES_STRICT_RUNTIME_ATTESTATION_ROOT
                    / component
                    / "installation-attestation.json"
                ),
                payload=payload,
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                owner_uid=0,
                mode=0o600,
                regular_file=True,
                single_link=True,
                ancestors_root_controlled=True,
            )
            for component, payload in raw_payloads.items()
        }
        return request, _StaticInspector(records)

    @staticmethod
    def _config(
        request: gate.PhysicalPostgresStrictRuntimeInstallationRequest,
        *,
        enabled: bool = True,
        maximum_evidence_age_seconds: int = 300,
    ) -> gate.PhysicalPostgresStrictRuntimeInstallationConfig:
        return gate.PhysicalPostgresStrictRuntimeInstallationConfig(
            request=request,
            enabled=enabled,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
        )

    def test_four_exact_root_owned_fresh_attestations_produce_only_non_authorizing_observation(
        self,
    ) -> None:
        request, inspector = self._scenario()
        with patch.object(gate.os, "geteuid", return_value=0):
            observed = gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )
        self.assertEqual(
            tuple(gate.STRICT_DURABLE_REPLAY_COMPONENTS),
            tuple(component for component, _digest in observed.attestation_sha256es),
        )
        self.assertTrue(observed.strict_rendering_still_refused_by_scaffold)
        self.assertTrue(observed.not_a_launch_authorization)
        self.assertNotIn("secret", repr(observed).lower())
        self.assertEqual(
            gate.STRICT_DURABLE_REPLAY_COMPONENTS,
            tuple(component for component, _path in inspector.calls),
        )
        self.assertIs(
            observed,
            gate.require_verified_physical_postgres_strict_runtime_installations(
                observed, request=request, now=NOW
            ),
        )

    def test_disabled_or_nonroot_gate_never_calls_the_inspector(self) -> None:
        request, inspector = self._scenario()
        with self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_DISABLED",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request, enabled=False), inspector=inspector, now=NOW
            )
        self.assertEqual([], inspector.calls)
        with patch.object(gate.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ROOT_RUNTIME_REQUIRED",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )
        self.assertEqual([], inspector.calls)

    def test_missing_noncanonical_stale_and_future_attestations_fail_closed(self) -> None:
        request, inspector = self._scenario()
        inspector.absent_component = "witness_locator_ledger"
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_UNAVAILABLE",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario(
            payload_transforms={
                "witness_locator_ledger": lambda payload: payload[:-1] + b" \n"
            }
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario(
            attested_at=NOW - timedelta(seconds=301),
            expires_at=NOW - timedelta(seconds=241),
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_STALE",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario(
            attested_at=NOW + timedelta(seconds=6),
            expires_at=NOW + timedelta(seconds=60),
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_FUTURE",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

    def test_mode_ownership_hash_and_component_binding_mismatches_fail_closed(self) -> None:
        request, inspector = self._scenario()
        component = "wa_fi_local_wal_archive_capture"
        inspector.records[component] = replace(inspector.records[component], mode=0o640)
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_MODE_INVALID",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario()
        inspector.records[component] = replace(
            inspector.records[component], owner_uid=1000
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_OWNERSHIP_INVALID",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario()
        inspector.records[component] = replace(
            inspector.records[component], payload_sha256=digest("wrong-payload")
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_HASH_MISMATCH",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario(
            document_changes={
                component: {"configuration_sha256": digest("changed-configuration")}
            }
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

        request, inspector = self._scenario(
            document_changes={component: {"version": 1.0}}
        )
        with patch.object(gate.os, "geteuid", return_value=0), self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        ):
            gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )

    def test_request_requires_all_components_and_a_validated_strict_manifest(self) -> None:
        all_hashes = {
            component: digest(component)
            for component in gate.STRICT_DURABLE_REPLAY_COMPONENTS
        }
        expectations = self._expectations(all_hashes)
        missing = dict(expectations)
        del missing["witness_locator_ledger"]
        with self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_COMPONENT_SET_INVALID",
        ):
            gate.build_physical_postgres_strict_runtime_installation_request(
                self.manifest, component_expectations=missing
            )
        bounded = scaffold.validate_physical_postgres_deployment_manifest(
            _manifest_document(profile=scaffold.PROFILE_BOUNDED_RPO_ARCHIVE)
        )
        with self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_MANIFEST_NOT_STRICT",
        ):
            gate.build_physical_postgres_strict_runtime_installation_request(
                bounded, component_expectations=expectations
            )

    def test_verified_observation_expires_and_cannot_be_relabelled_as_authority(self) -> None:
        request, inspector = self._scenario()
        with patch.object(gate.os, "geteuid", return_value=0):
            observed = gate.verify_physical_postgres_strict_runtime_installations(
                config=self._config(request), inspector=inspector, now=NOW
            )
        with self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_EXPIRED",
        ):
            gate.require_verified_physical_postgres_strict_runtime_installations(
                observed, request=request, now=NOW + timedelta(seconds=61)
            )
        relabelled = replace(observed, not_a_launch_authorization=False)
        with self.assertRaisesRegex(
            gate.PhysicalPostgresStrictRuntimeInstallationError,
            "STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_INVALID",
        ):
            gate.require_verified_physical_postgres_strict_runtime_installations(
                relabelled, request=request, now=NOW
            )


if __name__ == "__main__":
    unittest.main()
