"""Negative and physical-corruption scenarios for authority registration after security.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-COMPLETION-20261102-001
Task: p1-00b-security-resume-authority-registration-20261102-007
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from typing import Any, Callable
import unittest
from unittest.mock import patch

import tests.test_p1_00b_characterization_authority_registration_after_security as primary


def _typed_ordered_fingerprint(packet: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Return a type-and-order aware JSON fingerprint without sort_keys."""
    return json.dumps(
        [packet, manifest],
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _insert_intervening_mapping(manifest: dict[str, Any]) -> None:
    rebuilt: dict[str, str] = {}
    for key, val in manifest["evidence_sources"].items():
        if key == primary.AUTHORITY_MANIFEST_KEY:
            rebuilt["intervening_before_authority_path"] = (
                primary.PRIOR_VERIFIER_REL_PATH
            )
        rebuilt[key] = val
    manifest["evidence_sources"] = rebuilt


def _reorder_manifest_root_keys(manifest: dict[str, Any]) -> None:
    items = list(manifest.items())
    manifest.clear()
    for key, val in reversed(items):
        manifest[key] = val


def _reorder_packet_root_keys(packet: dict[str, Any]) -> None:
    items = list(packet.items())
    packet.clear()
    for key, val in reversed(items):
        packet[key] = val


class AuthorityRegistrationSecurityNegativeTests(unittest.TestCase):
    def test_registration_and_physical_corruptions_reject_and_restore(
        self,
    ) -> None:
        healthy_packet, healthy_manifest = primary.load_physical_controls()
        healthy_fp = _typed_ordered_fingerprint(healthy_packet, healthy_manifest)
        primary.assert_authority_registration(healthy_packet, healthy_manifest)

        # Positive support for valid future evidence mappings AFTER authority
        after_suffix_packet, after_suffix_manifest = primary.load_physical_controls()
        after_suffix_manifest["evidence_sources"]["future_after_authority_path"] = (
            primary.PRIOR_VERIFIER_REL_PATH
        )
        self.assertNotEqual(
            _typed_ordered_fingerprint(after_suffix_packet, after_suffix_manifest),
            healthy_fp,
        )
        primary.assert_authority_registration(
            after_suffix_packet,
            after_suffix_manifest,
        )

        mutations: list[
            tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]
        ] = [
            (
                "wrong_current_top_parent_assignment_799b",
                lambda p, m: p.__setitem__(
                    "parent_assignment_id",
                    "WA-P1-00B-BUSINESS-LIVE-20261023-799B",
                ),
            ),
            (
                "extra_handler_field",
                lambda p, m: p["registered_handlers"][0].__setitem__(
                    "extra_ninth_field",
                    "unexpected",
                ),
            ),
            (
                "missing_handler_field",
                lambda p, m: p["registered_handlers"][0].pop("source_commit"),
            ),
            (
                "wrong_handler_selector",
                lambda p, m: p["registered_handlers"][0].__setitem__(
                    "selector",
                    "tests/test_p1_00b_characterization_internal_authority_execute_failure.py::Wrong::test_none",
                ),
            ),
            (
                "wrong_handler_source_sha256",
                lambda p, m: p["registered_handlers"][0].__setitem__(
                    "source_file_sha256",
                    "0" * 64,
                ),
            ),
            (
                "record_verifier_body_identity_overclaim",
                lambda p, m: p["records"][0]["characterized_contract"][
                    "synthetic_verifier"
                ].__setitem__("positional_body_identity_claimed", True),
            ),
            (
                "record_persistence_exclusion_dropped",
                lambda p, m: p["records"][0]["exclusions"].__setitem__(
                    "persistence_side_effects",
                    False,
                ),
            ),
            (
                "lost_manifest_authority_mapping",
                lambda p, m: m["evidence_sources"].pop(
                    primary.AUTHORITY_MANIFEST_KEY
                ),
            ),
            (
                "wrong_manifest_authority_target",
                lambda p, m: m["evidence_sources"].__setitem__(
                    primary.AUTHORITY_MANIFEST_KEY,
                    primary.PRIOR_VERIFIER_REL_PATH,
                ),
            ),
            (
                "intervening_mapping_before_authority",
                lambda p, m: _insert_intervening_mapping(m),
            ),
            (
                "manifest_extra_top_level_field",
                lambda p, m: m.__setitem__("extra_root_key", "invalid"),
            ),
            (
                "manifest_reordered_root_keys",
                lambda p, m: _reorder_manifest_root_keys(m),
            ),
            (
                "packet_extra_top_level_field",
                lambda p, m: p.__setitem__("extra_packet_key", "invalid"),
            ),
            (
                "packet_reordered_root_keys",
                lambda p, m: _reorder_packet_root_keys(p),
            ),
            (
                "nonzero_raw_membership_delta",
                lambda p, m: p["raw_membership_delta"].append(
                    "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539"
                ),
            ),
            (
                "float_count_in_packet_counts_after_union",
                lambda p, m: p["counts_after_union"].__setitem__(
                    "evidenced_seed_count",
                    398.0,
                ),
            ),
            (
                "float_count_in_manifest_counts",
                lambda p, m: m["counts"].__setitem__(
                    "evidenced_seed_count",
                    398.0,
                ),
            ),
            (
                "bool_in_packet_raw_registration_delta",
                lambda p, m: p["accounting"].__setitem__(
                    "raw_registration_delta",
                    False,
                ),
            ),
            (
                "int_zero_for_group_reconciliation_complete_bool",
                lambda p, m: p["accounting"].__setitem__(
                    "group_reconciliation_complete",
                    0,
                ),
            ),
            (
                "wrong_cumulative_reconciled_units",
                lambda p, m: p["accounting"].__setitem__(
                    "cumulative_reconciled_units_on_exact_acceptance",
                    5,
                ),
            ),
            (
                "wrong_remaining_unreconciled_scenarios",
                lambda p, m: p["accounting"].__setitem__(
                    "remaining_unreconciled_scenarios_on_exact_acceptance",
                    0,
                ),
            ),
            (
                "premature_group_reconciliation_complete",
                lambda p, m: p["accounting"].__setitem__(
                    "group_reconciliation_complete",
                    True,
                ),
            ),
            (
                "premature_ledger_complete_status",
                lambda p, m: p["accounting"].__setitem__(
                    "ledger_integration_status",
                    "COMPLETE",
                ),
            ),
            (
                "closures_authorized_overclaim",
                lambda p, m: p.__setitem__("closures_authorized", True),
            ),
        ]

        for label, mutate in mutations:
            with self.subTest(case=label):
                bad_packet = copy.deepcopy(healthy_packet)
                bad_manifest = copy.deepcopy(healthy_manifest)
                mutate(bad_packet, bad_manifest)
                mutated_fp = _typed_ordered_fingerprint(bad_packet, bad_manifest)
                self.assertNotEqual(
                    mutated_fp,
                    healthy_fp,
                    msg=f"{label} did not change typed ordered fingerprint",
                )
                with self.assertRaises(AssertionError, msg=label):
                    primary.assert_authority_registration(bad_packet, bad_manifest)
                self.assertEqual(
                    _typed_ordered_fingerprint(bad_packet, bad_manifest),
                    mutated_fp,
                )
                fresh_packet, fresh_manifest = primary.load_physical_controls()
                self.assertEqual(
                    _typed_ordered_fingerprint(fresh_packet, fresh_manifest),
                    healthy_fp,
                )
                primary.assert_authority_registration(fresh_packet, fresh_manifest)

        # Same-length physical source-byte corruption in a TemporaryDirectory via primary.accounting.ROOT
        source_rel = healthy_packet["records"][0]["path"]
        real_source_path = primary.accounting.ROOT / source_rel
        original_bytes = real_source_path.read_bytes()
        self.assertEqual(len(original_bytes), 15378)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            tmp_source = tmp_root / source_rel
            tmp_source.parent.mkdir(parents=True, exist_ok=True)

            corrupted = bytearray(original_bytes)
            corrupted[-1] = (
                ord("#") if corrupted[-1] != ord("#") else ord("\n")
            )
            corrupted_bytes = bytes(corrupted)
            self.assertEqual(len(corrupted_bytes), len(original_bytes))
            self.assertNotEqual(corrupted_bytes, original_bytes)

            tmp_source.write_bytes(corrupted_bytes)
            tmp_source.chmod(0o644)
            try:
                with patch.object(primary.accounting, "ROOT", tmp_root):
                    with self.assertRaises(AssertionError) as exc_ctx:
                        primary.assert_authority_registration(
                            copy.deepcopy(healthy_packet),
                            copy.deepcopy(healthy_manifest),
                        )
                    self.assertIn("Physical SHA256", str(exc_ctx.exception))
            finally:
                tmp_source.write_bytes(original_bytes)
                tmp_source.chmod(0o644)

        self.assertEqual(real_source_path.read_bytes(), original_bytes)
        restored_packet, restored_manifest = primary.load_physical_controls()
        self.assertEqual(
            _typed_ordered_fingerprint(restored_packet, restored_manifest),
            healthy_fp,
        )
        primary.assert_authority_registration(restored_packet, restored_manifest)


if __name__ == "__main__":
    unittest.main()
