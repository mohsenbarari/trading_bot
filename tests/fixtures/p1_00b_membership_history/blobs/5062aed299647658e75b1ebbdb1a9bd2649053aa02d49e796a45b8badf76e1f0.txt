"""Primary authority registration after security characterization and shared oracle.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-COMPLETION-20261102-001
Parent assignment: WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001
Original parent: WA-P1-00B-FREEZE-20260904-001
Task: p1-00b-security-resume-authority-registration-20261102-007
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
import unittest

import tests.test_p1_00b_characterization_authority_membership_correspondence as correspondence
import tests.test_p1_00b_characterization_authority_record_contract_oracle as record_oracle
import tests.test_p1_00b_characterization_canonical_membership_snapshot as snapshot_oracle
import tests.test_p1_00b_characterization_ledger_prefix_helper as ledger_prefix
import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting

PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-authority-registration-security.json"
)
PRIOR_VERIFIER_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "recent-characterization-business-768-live786-verifier-only.json"
)
PRIOR_VERIFIER_KEY = "business_768_live786_verifier_only_path"
AUTHORITY_MANIFEST_KEY = (
    "business_768_live786_authority_registration_after_security_path"
)

TASK_ID = "p1-00b-security-resume-authority-registration-20261102-007"
ASSIGNMENT_ID = "WA-P1-00B-COMPLETION-20261102-001"
ASSIGNMENT_SCOPE_SHA256 = (
    "60cea71c873c6ec93c1191634d7eb48954918646c5683717a38cf0ab0bd1448d"
)
PARENT_ASSIGNMENT_ID = "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001"
ORIGINAL_PARENT_ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
SCENARIO_KEY = (
    "P1-00B-RECONCILE-SECURITY-RESUME-V6.authority-registration-framed-full-seven"
)
SOURCE_HEAD = "a4b1fd512e45960da6df319c002142a30352aaac"

PRE_REGISTRATION_MANIFEST_UTF8_BYTES = 4377
PRE_REGISTRATION_MANIFEST_SHA256 = (
    "821de3225e4609a586db6ee337f933eb9740a23d860239688bc2c7fea2ec258b"
)
PRIOR_VERIFIER_UTF8_BYTES = 11436
PRIOR_VERIFIER_SHA256 = (
    "09c03b798d7c268a987874c1ebe3e834ed534edc97eade4be9b68e097d27f960"
)
PRIOR_VERIFIER_ACCEPTED_COMMIT = "bfad4a12866719f255f003d15625c87ccd8da1b5"

HANDLER_KEYS = (
    "scenario",
    "scenario_key",
    "selector",
    "canonical_symbol",
    "seed_id",
    "source_path",
    "source_file_sha256",
    "source_commit",
)

EXPECTED_HELPER_PINS = [
    {
        "path": "tests/test_p1_00b_characterization_authority_record_contract_oracle.py",
        "bytes": 19824,
        "sha256": "52cd08169b177c16e5576fddb86b6b5c57afd685be473012ead410ee4687b29c",
        "accepted_commit": "46b3807a8257ff3a67554a1c6ed1c681b631aa85",
    },
    {
        "path": "tests/test_p1_00b_characterization_canonical_membership_snapshot.py",
        "bytes": 11785,
        "sha256": "7186a5eb551f50a8b35b6eceec38f896b58e7340cc0c5d6ac80086624d217deb",
        "accepted_commit": "d83f5057c6df6f3da8db7cafb1175e822922433d",
    },
    {
        "path": "tests/test_p1_00b_characterization_authority_membership_correspondence.py",
        "bytes": 12413,
        "sha256": "e0e7801944bf04e6ec92857bdafaf718039b214b218fc46e3a4344d02fc60072",
        "accepted_commit": "6f3bdbe7fc3e0864a25c432bae01df46457b9d4e",
    },
    {
        "path": "tests/test_p1_00b_characterization_notification_mark_all_accounting.py",
        "bytes": 20531,
        "sha256": "0a07c8597233a701a5b115de52b68ed043db74ed075cdf58a3ef3fc4d2c80407",
    },
    {
        "path": "tests/test_p1_00b_characterization_ledger_prefix_helper.py",
        "bytes": 7834,
        "sha256": "b1c06db3e7cfa6c18c8653d27d0df294e843e20ba0474134c5eafacc6b3c0738",
    },
]

EXPECTED_PROMOTION_COUNTS = {
    "expected": 465,
    "baseline": 135,
    "reconciled_new": 263,
    "evidenced": 398,
    "remaining": 67,
}

EXPECTED_PROMOTION_ASSESSMENT = {
    "promoted_seed_ids": [],
    "already_registered_seed_ids": [record_oracle.SEED_ID],
    "delta_distinct_seed_count": 0,
    "removed_seed_ids": [],
    "duplicate_new_seed_ids": [],
    "before_manifest_sha256": PRE_REGISTRATION_MANIFEST_SHA256,
    "before_manifest_utf8_bytes": PRE_REGISTRATION_MANIFEST_UTF8_BYTES,
    "canonical_before": EXPECTED_PROMOTION_COUNTS,
    "canonical_after_on_exact_acceptance": EXPECTED_PROMOTION_COUNTS,
    "added_behavior_units": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
    "full_seed_acceptance": "NOT_ASSESSED",
}

EXPECTED_ACCOUNTING = {
    "pending_five": 5,
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "total_handler_scenarios": 7,
    "prior_reconciled_verifier_provenance": {
        "path": PRIOR_VERIFIER_REL_PATH,
        "sha256": PRIOR_VERIFIER_SHA256,
        "utf8_bytes": PRIOR_VERIFIER_UTF8_BYTES,
        "accepted_commit": PRIOR_VERIFIER_ACCEPTED_COMMIT,
        "cumulative_reconciled_units": 3,
        "cumulative_reconciled_scenarios": 4,
    },
    "reconciled_units_in_this_packet_on_exact_acceptance": 1,
    "reconciled_scenarios_in_this_packet_on_exact_acceptance": 1,
    "cumulative_reconciled_units_on_exact_acceptance": 4,
    "cumulative_reconciled_scenarios_on_exact_acceptance": 5,
    "remaining_unreconciled_units_on_exact_acceptance": 1,
    "remaining_unreconciled_scenarios_on_exact_acceptance": 2,
    "group_reconciliation_complete": False,
    "canonical_manifest_and_ledger_integration_complete": False,
    "ledger_integration_status": "PENDING",
    "no_sixth_behavior_accepted_before_complete_reconciliation": True,
    "added_behavior_units": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
    "unreconciled_units_after_authority_on_exact_acceptance": [
        {
            "path": "tests/test_p1_00b_characterization_internal_session_body_failures_batch.py",
            "sha256": "b5f2772f283424f94c0f8436b8575f58f7ec65a2e57500ea4422bc9c9785c4e9",
            "source_commit": "777ed75419ed7083d875c262da2beb8103017d03",
            "handler_scenarios": 2,
        }
    ],
    "preserved_deferred_and_failed_reservations": {
        "deferred_a2_task_id": "p1-00b-799b-c006-session-prequery-reconciliation-a2",
        "deferred_a2_status": "UNACCEPTED_RESERVED_NEVER_REPLAY_OR_RELEASE",
        "failed_authority_singleton_task_id": "p1-00b-799b-c009-authority-execute-singleton-reconciliation",
        "failed_authority_singleton_status": "UNACCEPTED_RESERVED_NEVER_REPLAY_OR_RELEASE",
        "prior_attempt_006_proposal_sha256": "cf7a47345ce3b9018138c3873e3967225db02f5f95f77aa341b0e44ae7b7e617",
        "prior_attempt_006_status": "UNACCEPTED_GENUINE_PROPOSAL_ONE_PASS_ONE_FAIL_CONTEXT_BLOCKED_CORRECTION_PRESERVED",
    },
    "previous_completed_group_provenance": {
        "assignment_id": "WA-P1-00B-FIVE-SOURCE-RECONCILIATION-20261023-798D",
        "accepted_closeout_commit": "e5c08c61895961bce399da9d8c69f561d7538ea1",
        "status": "COMPLETED_PROVENANCE",
        "reconciled_behavior_units": 5,
        "reconciled_handler_scenarios": 7,
        "remaining_in_group": 0,
    },
    "ledger_prefix_binding": {
        "path": ledger_prefix.LEDGER_REL_PATH,
        "unchanged_ledger_sha256": ledger_prefix.HISTORICAL_PREFIX_SHA256,
        "unchanged_ledger_utf8_bytes": ledger_prefix.HISTORICAL_PREFIX_BYTES,
    },
}

PACKET_TOP_KEYS = (
    "schema_version",
    "stage",
    "assignment_id",
    "assignment_scope_sha256",
    "parent_assignment_id",
    "original_parent_assignment_id",
    "task_id",
    "scenario_key",
    "source_head",
    "records",
    "registered_handlers",
    "raw_membership_delta",
    "counts_after_union",
    "promotion_assessment",
    "accepted_prerequisite_helpers",
    "accounting",
    "strict_canonical_progress",
    "origin_matrix_unresolved_cells",
    "canonical_blockers",
    "seed_acceptance_status",
    "closures_authorized",
    "stages",
)

_READER = accounting.NotificationMarkAllAccountingTests("runTest")


def _assert_typed_equal(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected):
        raise AssertionError(
            f"{label}: expected type {type(expected).__name__}, "
            f"got {type(actual).__name__}"
        )
    if isinstance(expected, dict):
        if list(actual.keys()) != list(expected.keys()):
            raise AssertionError(
                f"{label}: ordered keys {list(actual.keys())!r} != "
                f"{list(expected.keys())!r}"
            )
        for key, exp_val in expected.items():
            if type(key) is not str:
                raise AssertionError(f"{label}: non-str key {key!r}")
            _assert_typed_equal(actual[key], exp_val, f"{label}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(
                f"{label}: length {len(actual)} != {len(expected)}"
            )
        for idx, (act_item, exp_item) in enumerate(zip(actual, expected)):
            _assert_typed_equal(act_item, exp_item, f"{label}[{idx}]")
        return
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def _expected_handler_from_record(record: dict[str, Any]) -> dict[str, str]:
    return {
        "scenario": record["scenarios"][0],
        "scenario_key": record["scenarios"][0],
        "selector": record["test_selectors"][0],
        "canonical_symbol": record["canonical_symbol"],
        "seed_id": record["seed_ids"][0],
        "source_path": record["path"],
        "source_file_sha256": record["sha256"],
        "source_commit": record["last_commit"],
    }


def load_physical_controls() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return fresh (actual_packet, actual_manifest) from accepted physical readers."""
    packet = copy.deepcopy(_READER._json(PACKET_REL_PATH))
    manifest = copy.deepcopy(_READER._json(accounting.MANIFEST))
    return packet, manifest


def _reconstruct_pre_registration_manifest(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    if type(manifest) is not dict:
        raise AssertionError("manifest must be a dict")
    evidence_sources = manifest.get("evidence_sources")
    if type(evidence_sources) is not dict:
        raise AssertionError("manifest.evidence_sources must be a dict")
    for k, v in evidence_sources.items():
        if type(k) is not str or type(v) is not str or not k or not v:
            raise AssertionError("manifest.evidence_sources entries must be non-empty str")

    ordered_items = list(evidence_sources.items())
    keys = [k for k, _ in ordered_items]
    if PRIOR_VERIFIER_KEY not in keys:
        raise AssertionError("prior verifier key missing from manifest.evidence_sources")
    if AUTHORITY_MANIFEST_KEY not in keys:
        raise AssertionError("authority registration key missing from manifest.evidence_sources")

    verifier_index = keys.index(PRIOR_VERIFIER_KEY)
    authority_index = keys.index(AUTHORITY_MANIFEST_KEY)
    if authority_index != verifier_index + 1:
        raise AssertionError(
            f"authority mapping index {authority_index} != verifier_index + 1 ({verifier_index + 1})"
        )
    if evidence_sources[PRIOR_VERIFIER_KEY] != PRIOR_VERIFIER_REL_PATH:
        raise AssertionError("prior verifier mapping path mismatch")
    if evidence_sources[AUTHORITY_MANIFEST_KEY] != PACKET_REL_PATH:
        raise AssertionError("authority registration mapping path mismatch")

    prefix_sources = dict(ordered_items[: verifier_index + 1])
    if PACKET_REL_PATH in prefix_sources.values():
        raise AssertionError("authority packet path must not appear in pre-registration prefix")

    reconstructed = copy.deepcopy(manifest)
    reconstructed["evidence_sources"] = prefix_sources
    serialized = (json.dumps(reconstructed, indent=2) + "\n").encode("utf-8")
    if len(serialized) != PRE_REGISTRATION_MANIFEST_UTF8_BYTES:
        raise AssertionError(
            f"pre-registration manifest UTF-8 bytes {len(serialized)} != "
            f"{PRE_REGISTRATION_MANIFEST_UTF8_BYTES}"
        )
    actual_sha = hashlib.sha256(serialized).hexdigest()
    if actual_sha != PRE_REGISTRATION_MANIFEST_SHA256:
        raise AssertionError(
            f"pre-registration manifest SHA256 {actual_sha} != "
            f"{PRE_REGISTRATION_MANIFEST_SHA256}"
        )
    return reconstructed, serialized


def assert_authority_registration(packet: dict, manifest: dict) -> None:
    """Validate supplied authority registration packet and manifest against physical oracles."""
    if type(packet) is not dict:
        raise AssertionError("packet must be exact dict")
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if list(packet.keys()) != list(PACKET_TOP_KEYS):
        raise AssertionError(
            f"packet top keys {list(packet.keys())!r} != {list(PACKET_TOP_KEYS)!r}"
        )

    records = packet.get("records")
    if type(records) is not list or len(records) != 1:
        raise AssertionError("packet.records must be a singleton list")
    record = records[0]
    # Must validate record FIRST on packet.records[0] before physical canonical reads
    record_oracle.assert_authority_record(record)

    handlers = packet.get("registered_handlers")
    if type(handlers) is not list or len(handlers) != 1:
        raise AssertionError("packet.registered_handlers must be a singleton list")
    handler_row = handlers[0]
    if type(handler_row) is not dict or list(handler_row.keys()) != list(HANDLER_KEYS):
        raise AssertionError(
            f"handler_row must have exact ordered 8 keys {HANDLER_KEYS!r}"
        )
    expected_handler = _expected_handler_from_record(record)
    _assert_typed_equal(handler_row, expected_handler, "registered_handlers[0]")

    expected_header = {
        "schema_version": 1,
        "stage": "P1-00b",
        "assignment_id": ASSIGNMENT_ID,
        "assignment_scope_sha256": ASSIGNMENT_SCOPE_SHA256,
        "parent_assignment_id": PARENT_ASSIGNMENT_ID,
        "original_parent_assignment_id": ORIGINAL_PARENT_ASSIGNMENT_ID,
        "task_id": TASK_ID,
        "scenario_key": SCENARIO_KEY,
        "source_head": SOURCE_HEAD,
    }
    for key, exp_val in expected_header.items():
        _assert_typed_equal(packet[key], exp_val, f"packet.{key}")

    _assert_typed_equal(
        packet["promotion_assessment"],
        EXPECTED_PROMOTION_ASSESSMENT,
        "packet.promotion_assessment",
    )
    _assert_typed_equal(
        packet["accepted_prerequisite_helpers"],
        EXPECTED_HELPER_PINS,
        "packet.accepted_prerequisite_helpers",
    )
    _assert_typed_equal(
        packet["accounting"],
        EXPECTED_ACCOUNTING,
        "packet.accounting",
    )
    _assert_typed_equal(
        packet["strict_canonical_progress"],
        {"proven": 288, "partial": 177, "total": 465},
        "packet.strict_canonical_progress",
    )
    _assert_typed_equal(
        packet["origin_matrix_unresolved_cells"],
        24,
        "packet.origin_matrix_unresolved_cells",
    )
    _assert_typed_equal(
        packet["canonical_blockers"],
        accounting.BLOCKERS,
        "packet.canonical_blockers",
    )
    _assert_typed_equal(
        packet["seed_acceptance_status"],
        "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        "packet.seed_acceptance_status",
    )
    _assert_typed_equal(
        packet["closures_authorized"],
        False,
        "packet.closures_authorized",
    )
    _assert_typed_equal(
        packet["stages"],
        {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        "packet.stages",
    )

    reconstructed_before, _ = _reconstruct_pre_registration_manifest(manifest)
    _assert_typed_equal(manifest["schema_version"], 1, "manifest.schema_version")
    _assert_typed_equal(manifest["stage"], "P1-00b", "manifest.stage")
    _assert_typed_equal(
        manifest["assignment_id"],
        ORIGINAL_PARENT_ASSIGNMENT_ID,
        "manifest.assignment_id",
    )
    _assert_typed_equal(manifest["coverage_status"], "PARTIAL", "manifest.coverage_status")
    _assert_typed_equal(
        manifest["canonical_blockers"],
        accounting.BLOCKERS,
        "manifest.canonical_blockers",
    )
    _assert_typed_equal(
        manifest["counts"],
        correspondence.EXPECTED_COUNTS_AFTER_UNION,
        "manifest.counts",
    )

    for helper_pin in EXPECTED_HELPER_PINS:
        helper_bytes = _READER._read(helper_pin["path"])
        if len(helper_bytes) != helper_pin["bytes"]:
            raise AssertionError(f"helper size mismatch for {helper_pin['path']}")
        if hashlib.sha256(helper_bytes).hexdigest() != helper_pin["sha256"]:
            raise AssertionError(f"helper sha256 mismatch for {helper_pin['path']}")

    prior_verifier_bytes = _READER._read(PRIOR_VERIFIER_REL_PATH)
    if len(prior_verifier_bytes) != PRIOR_VERIFIER_UTF8_BYTES:
        raise AssertionError("prior verifier utf8_bytes mismatch")
    if hashlib.sha256(prior_verifier_bytes).hexdigest() != PRIOR_VERIFIER_SHA256:
        raise AssertionError("prior verifier sha256 mismatch")
    prior_verifier_doc = json.loads(prior_verifier_bytes.decode("utf-8"))
    prior_unreconciled = prior_verifier_doc["accounting"][
        "unreconciled_units_after_verifier_on_exact_acceptance"
    ]
    expected_authority_unreconciled = {
        "path": record["path"],
        "sha256": record["sha256"],
        "source_commit": record["last_commit"],
        "handler_scenarios": 1,
    }
    if expected_authority_unreconciled not in prior_unreconciled:
        raise AssertionError("authority unit missing from prior verifier unreconciled list")
    remaining_units = [
        u for u in prior_unreconciled if u != expected_authority_unreconciled
    ]
    _assert_typed_equal(
        packet["accounting"]["unreconciled_units_after_authority_on_exact_acceptance"],
        remaining_units,
        "remaining_unreconciled_units",
    )

    physical_snapshot = snapshot_oracle.make_canonical_membership_snapshot()
    witness = {
        "authority_record": record,
        "canonical_snapshot": physical_snapshot,
        "raw_membership_delta": packet["raw_membership_delta"],
        "counts_after_union": packet["counts_after_union"],
    }
    correspondence.assert_authority_membership_correspondence(witness)

    receipt = _READER._json(accounting.RECEIPT)
    before_surface, before_baseline, before_evidenced = _READER._derive(
        reconstructed_before,
        receipt,
    )
    after_surface, after_baseline, after_evidenced = _READER._derive(
        manifest,
        receipt,
        extra_documents={PACKET_REL_PATH: packet},
    )
    if (
        before_surface != after_surface
        or before_baseline != after_baseline
        or before_evidenced != after_evidenced
    ):
        raise AssertionError("authority registration must have zero raw membership delta")
    if (
        len(after_surface),
        len(after_baseline),
        len(after_evidenced - after_baseline),
        len(after_evidenced),
        len(after_surface - after_evidenced),
    ) != (465, 135, 263, 398, 67):
        raise AssertionError("derived before/after set counts mismatch 465/135/263/398/67")
    if record_oracle.SEED_ID in after_baseline:
        raise AssertionError("authority seed must be absent from baseline")
    if record_oracle.SEED_ID not in after_evidenced or record_oracle.SEED_ID not in after_surface:
        raise AssertionError("authority seed must be present in surface and evidenced")
    if sorted(after_evidenced - before_evidenced) != [] or sorted(before_evidenced - after_evidenced) != []:
        raise AssertionError("added/removed raw seed sets must be empty")

    ledger_binding = packet["accounting"]["ledger_prefix_binding"]
    prefix_bytes = ledger_prefix.read_ledger_prefix(
        accounting.ROOT / ledger_binding["path"],
        ledger_binding["unchanged_ledger_utf8_bytes"],
        ledger_binding["unchanged_ledger_sha256"],
    )
    if len(prefix_bytes) != ledger_prefix.HISTORICAL_PREFIX_BYTES:
        raise AssertionError("ledger prefix byte count mismatch")


class AuthorityRegistrationAfterSecurityTests(unittest.TestCase):
    def test_actual_registration_consumes_correspondence_without_raw_credit(
        self,
    ) -> None:
        source_before = _READER._read(record_oracle.SOURCE_PATH)
        prior_before = _READER._read(PRIOR_VERIFIER_REL_PATH)
        ledger_before = (accounting.ROOT / ledger_prefix.LEDGER_REL_PATH).read_bytes()

        packet_a, manifest_a = load_physical_controls()
        packet_b, manifest_b = load_physical_controls()
        self.assertIsNot(packet_a, packet_b)
        self.assertIsNot(manifest_a, manifest_b)
        self.assertEqual(packet_a, packet_b)
        self.assertEqual(manifest_a, manifest_b)

        assert_authority_registration(packet_a, manifest_a)

        self.assertEqual(packet_a["records"][0], record_oracle.make_authority_record())
        self.assertEqual(packet_a["raw_membership_delta"], [])
        self.assertEqual(
            packet_a["counts_after_union"],
            correspondence.EXPECTED_COUNTS_AFTER_UNION,
        )
        self.assertEqual(
            manifest_a["evidence_sources"][AUTHORITY_MANIFEST_KEY],
            PACKET_REL_PATH,
        )

        self.assertEqual(_READER._read(record_oracle.SOURCE_PATH), source_before)
        self.assertEqual(_READER._read(PRIOR_VERIFIER_REL_PATH), prior_before)
        self.assertEqual(
            (accounting.ROOT / ledger_prefix.LEDGER_REL_PATH).read_bytes(),
            ledger_before,
        )


if __name__ == "__main__":
    unittest.main()
