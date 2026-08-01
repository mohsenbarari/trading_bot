from __future__ import annotations

import ast
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_contract as v2r


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


class PhysicalWalV2rWitnessRoundtripContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ir = Ed25519PrivateKey.generate()
        self.witness_forward = Ed25519PrivateKey.generate()
        self.fi = Ed25519PrivateKey.generate()
        self.witness_return = Ed25519PrivateKey.generate()
        self.config = v2r.PhysicalWalV2rWitnessRoundtripConfig(
            cluster_id="gold-trade-prod",
            release_sha="a" * 40,
            stream_generation_id="v2r-generation-000001",
            route_commitment_sha256="1" * 64,
            reverse_frontier_sha256="2" * 64,
            recovery_frontier_sha256="3" * 64,
            blob_frontier_sha256="4" * 64,
            v2r_iam_policy_sha256="5" * 64,
            normal_v2_protocol_domain="gold-trade-physical-wal-v2-normal-v1",
            normal_v2_mailbox_prefix="physical-wal-v2-normal/",
            normal_v2_iam_policy_sha256="6" * 64,
            normal_v2_public_key_sha256s=("7" * 64,),
            ir_export_public_key=_public(self.ir),
            witness_forward_public_key=_public(self.witness_forward),
            fi_ack_public_key=_public(self.fi),
            witness_return_public_key=_public(self.witness_return),
            enabled=True,
        )

    def _chain(self) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
        export = v2r.build_physical_wal_v2r_witness_reverse_export(
            config=self.config, correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
            reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
            reverse_payload_commitment_sha256="8" * 64,
            expires_at=NOW + timedelta(seconds=40), ir_export_signer=self.ir, now=NOW,
        )
        verified_export = v2r.verify_physical_wal_v2r_witness_reverse_export(
            export, config=self.config, now=NOW,
            replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
            local_site="witness", local_role="witness-v2r-reverse-ingress",
        )
        forward = v2r.build_physical_wal_v2r_witness_forward_envelope(
            config=self.config, ir_reverse_export=verified_export,
            forward_id="v2r-witness-forward-000001", forward_nonce="F" * 22,
            expires_at=NOW + timedelta(seconds=35),
            witness_forward_signer=self.witness_forward, now=NOW,
        )
        verified_forward = v2r.verify_physical_wal_v2r_witness_forward_envelope(
            forward, config=self.config, now=NOW,
            replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
            local_site="wa-fi", local_role="wa-fi-v2r-recovery-inbox",
        )
        ack = v2r.build_physical_wal_v2r_witness_fi_ack(
            config=self.config, witness_forward=verified_forward,
            fi_ack_id="v2r-fi-ack-000001", fi_ack_nonce="A" * 22,
            expires_at=NOW + timedelta(seconds=30), fi_ack_signer=self.fi, now=NOW,
        )
        verified_ack = v2r.verify_physical_wal_v2r_witness_fi_ack(
            ack, config=self.config, now=NOW,
            replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
            local_site="witness", local_role="witness-v2r-ack-ingress",
        )
        returned = v2r.build_physical_wal_v2r_witness_return_envelope(
            config=self.config, fi_ack=verified_ack,
            return_id="v2r-witness-return-000001", return_nonce="R" * 22,
            expires_at=NOW + timedelta(seconds=25),
            witness_return_signer=self.witness_return, now=NOW,
        )
        return export, forward, ack, returned

    def _resign(self, stage: str, record: dict[str, object], signer: Ed25519PrivateKey) -> dict[str, object]:
        unsigned = dict(record)
        unsigned.pop("signature_base64")
        return v2r._sign(stage, unsigned, signer, _public(signer))

    def test_default_off_and_exact_four_hop_return_correlation(self) -> None:
        disabled = replace(self.config, enabled=False)
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_DEFAULT_DISABLED"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=disabled, correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        export, forward, ack, returned = self._chain()
        verified = v2r.verify_physical_wal_v2r_witness_return_envelope(
            returned, config=self.config, now=NOW,
            replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
            local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
        )
        self.assertEqual("v2r-correlation-000001", verified.correlation_id)
        self.assertEqual(v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN, returned["protocol_domain"])
        self.assertEqual("ir-to-witness", export["mailbox"])
        self.assertEqual("witness-to-fi", forward["mailbox"])
        self.assertEqual("fi-to-witness", ack["mailbox"])
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_REPLAY_GUARD_REQUIRED"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                returned, config=self.config, now=NOW, replay_guard=None,  # type: ignore[arg-type]
                local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
            )
        self.assertEqual("witness-to-ir", returned["mailbox"])

    def test_replay_tampering_and_exact_chain_substitution_fail_closed(self) -> None:
        _export, _forward, ack, returned = self._chain()
        guard = v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard()
        v2r.verify_physical_wal_v2r_witness_return_envelope(returned, config=self.config, now=NOW, replay_guard=guard, local_site="wa-ir", local_role="wa-ir-v2r-return-inbox")
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_REPLAY_DETECTED"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(returned, config=self.config, now=NOW, replay_guard=guard, local_site="wa-ir", local_role="wa-ir-v2r-return-inbox")
        tampered = dict(returned)
        tampered["recipient_site"] = "wa-fi"
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_ROLE_ROUTE_INVALID"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                tampered, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
            )
        missing_ack = dict(returned)
        del missing_ack["fi_ack_base64"]
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_FIELD_SET_INVALID"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                missing_ack, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
            )
        self.assertEqual("fi-to-witness", ack["mailbox"])

    def test_validly_signed_wrong_ack_chain_and_storage_authority_are_still_rejected(self) -> None:
        _export, _forward, _ack, returned = self._chain()
        wrong_prior = dict(returned)
        wrong_prior["prior_hop_sha256"] = "d" * 64
        wrong_prior = self._resign("return", wrong_prior, self.witness_return)
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_EXACT_ACK_CHAIN_INVALID"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                wrong_prior, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
            )
        authority = dict(returned)
        authority["object_storage_election_authority"] = True
        authority = self._resign("return", authority, self.witness_return)
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_OBJECT_STORAGE_AUTHORITY_FORBIDDEN"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                authority, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
            )

    def test_direct_ir_to_fi_and_wrong_local_receiver_are_rejected(self) -> None:
        export, _forward, _ack, returned = self._chain()
        direct = dict(export)
        direct["recipient_site"] = "wa-fi"
        direct["recipient_role"] = "wa-fi-v2r-recovery-inbox"
        direct = self._resign("export", direct, self.ir)
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_ROLE_ROUTE_INVALID"):
            v2r.verify_physical_wal_v2r_witness_reverse_export(
                direct, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="witness", local_role="witness-v2r-reverse-ingress",
            )
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_LOCAL_RECIPIENT_MISMATCH"):
            v2r.verify_physical_wal_v2r_witness_return_envelope(
                returned, config=self.config, now=NOW,
                replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                local_site="wa-fi", local_role="wa-fi-v2r-recovery-inbox",
            )

    def test_normal_v2_identity_reuse_and_cross_pins_are_rejected(self) -> None:
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_NORMAL_DOMAIN_REUSED"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=replace(self.config, normal_v2_protocol_domain=v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_PROTOCOL_DOMAIN),
                correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_NORMAL_PREFIX_REUSED"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=replace(self.config, normal_v2_mailbox_prefix=v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX),
                correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_NORMAL_IAM_PIN_REUSED"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=replace(self.config, normal_v2_iam_policy_sha256=self.config.v2r_iam_policy_sha256),
                correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_SIGNER_KEY_REUSE_FORBIDDEN"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=replace(self.config, fi_ack_public_key=_public(self.ir)),
                correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        with self.assertRaisesRegex(v2r.PhysicalWalV2rWitnessRoundtripError, "V2R_NORMAL_KEY_REUSE_FORBIDDEN"):
            v2r.build_physical_wal_v2r_witness_reverse_export(
                config=replace(self.config, normal_v2_public_key_sha256s=(hashlib.sha256(_public(self.ir)).hexdigest(),)),
                correlation_id="v2r-correlation-000001", chain_nonce="C" * 22,
                reverse_export_id="v2r-reverse-export-000001", reverse_export_nonce="E" * 22,
                reverse_payload_commitment_sha256="8" * 64, expires_at=NOW + timedelta(seconds=40),
                ir_export_signer=self.ir, now=NOW,
            )
        _export, _forward, _ack, returned = self._chain()
        for changed in ("release_sha", "stream_generation_id", "reverse_frontier_sha256"):
            target = {"release_sha": "b" * 40, "stream_generation_id": "other-generation-0001", "reverse_frontier_sha256": "9" * 64}[changed]
            with self.subTest(changed=changed), self.assertRaises(v2r.PhysicalWalV2rWitnessRoundtripError):
                v2r.verify_physical_wal_v2r_witness_return_envelope(
                    returned, config=replace(self.config, **{changed: target}), now=NOW,
                    replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                    local_site="wa-ir", local_role="wa-ir-v2r-return-inbox",
                )

    def test_normal_v2_mailbox_role_aliases_are_rejected_at_every_v2r_receiver(self) -> None:
        """Shared mailbox labels cannot turn a normal V2 role into a V2R role."""

        export, forward, ack, returned = self._chain()
        cases = (
            (
                v2r.verify_physical_wal_v2r_witness_reverse_export,
                export,
                "witness",
                "witness-fi-ingress",
            ),
            (
                v2r.verify_physical_wal_v2r_witness_forward_envelope,
                forward,
                "wa-fi",
                "fi-writer-ack-inbox",
            ),
            (
                v2r.verify_physical_wal_v2r_witness_fi_ack,
                ack,
                "witness",
                "witness-ir-ingress",
            ),
            (
                v2r.verify_physical_wal_v2r_witness_return_envelope,
                returned,
                "wa-ir",
                "ir-standby-ack-inbox",
            ),
        )
        for verify, record, site, normal_v2_role in cases:
            with self.subTest(normal_v2_role=normal_v2_role), self.assertRaisesRegex(
                v2r.PhysicalWalV2rWitnessRoundtripError,
                "V2R_LOCAL_RECIPIENT_MISMATCH",
            ):
                verify(
                    record,
                    config=self.config,
                    now=NOW,
                    replay_guard=v2r.PhysicalWalV2rWitnessRoundtripInMemoryReplayGuard(),
                    local_site=site,
                    local_role=normal_v2_role,
                )

    def test_static_import_boundary_has_no_transport_or_existing_v2_contract(self) -> None:
        source_path = Path(v2r.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & {"boto3", "botocore", "requests", "socket", "subprocess", "os", "pathlib", "sqlite3"})
        self.assertNotIn("core", imported)


if __name__ == "__main__":
    unittest.main()
