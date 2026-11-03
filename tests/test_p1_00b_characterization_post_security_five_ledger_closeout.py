from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import hashlib
import json
import pathlib
from typing import Any
import unittest

import tests.test_p1_00b_characterization_post_security_five_registration as registration

from tests.p1_00b_membership_history import historical_manifest, read_pin

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

CLOSEOUT_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/post-security-five-source-closeout.json"
)
REGISTRATION_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/post-security-five-source-registration.json"
)
REGISTRATION_MODULE_REL_PATH = (
    "tests/test_p1_00b_characterization_post_security_five_registration.py"
)
MANIFEST_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
LEDGER_REL_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"

REGISTRATION_PACKET_UTF8_BYTES = 11779
REGISTRATION_PACKET_SHA256 = (
    "56f5d4e76c4cd9edc97689fda6025e9fae9e059a80f3c8a995f457fa7d709bbe"
)
REGISTRATION_ACCEPTED_COMMIT = "057bfdd7a33b6ef21954f8c22adb4bf1fb95cb67"

REGISTRATION_MODULE_UTF8_BYTES = 40982
REGISTRATION_MODULE_SHA256 = (
    "2f45dfc6d051e684da6a16c9002168c2e6c2a236b10a6dcc3de7a54524a7f780"
)

PRE_CLOSEOUT_MANIFEST_UTF8_BYTES = 5159
PRE_CLOSEOUT_MANIFEST_SHA256 = (
    "06eca7deee14ce9d3126c5b7aa5f528881e2cf02bcd4175cc8e1e703f8ded366"
)

LEDGER_PREFIX_UTF8_BYTES = 112060
LEDGER_PREFIX_SHA256 = (
    "d2fa00d90befbe7c422d9200a7187cbc0b51a4e4d30a06e5f819721840bee572"
)

REGISTRATION_MANIFEST_KEY = "business_768_post_security_five_registration_path"
CLOSEOUT_MANIFEST_KEY = "business_768_post_security_five_closeout_path"
LEDGER_SECTION_ANCHOR = "post-security-five-source-six-scenario-closeout"

EXPECTED_CLOSEOUT_PACKET: dict[str, Any] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-COMPLETION-20261102-001",
    "assignment_scope_sha256": "80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1",
    "parent_assignment_id": "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001",
    "original_parent_assignment_id": "WA-P1-00B-FREEZE-20260904-001",
    "task_id": "p1-00b-post-security-five-ledger-closeout-20261103-001",
    "scenario_key": "P1-00B-POST-SECURITY-FIVE-LEDGER-V1.accepted-registration-final-integration",
    "source_head": "057bfdd7a33b6ef21954f8c22adb4bf1fb95cb67",
    "canonical_gap_reference": "docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "accepted_registration_reference": {
        "path": REGISTRATION_PACKET_REL_PATH,
        "utf8_bytes": REGISTRATION_PACKET_UTF8_BYTES,
        "sha256": REGISTRATION_PACKET_SHA256,
        "accepted_commit": REGISTRATION_ACCEPTED_COMMIT,
    },
    "prior_completed_group_provenance": {
        "accepted_commit": "5953f799f737780e8bcdfb57e8621030cb520422",
        "closeout_source_head": "b2d4ef0b1695f1e7a234648895ce644d7d4649a4",
        "closeout_packet_path": "docs/refactor/two-server/section-1/inventory/current-session-five-source-closeout.json",
        "closeout_packet_sha256": "8eabc7b2fde077e6404eaee184a359ad2dc79add1a4a7b5ab1a2ed2d5c6b31b2",
        "closeout_packet_utf8_bytes": 2509,
        "closeout_module_path": "tests/test_p1_00b_characterization_current_session_group_ledger_closeout.py",
        "closeout_module_sha256": "b901faa9502d86bc95dd6a9ce0c3adeb9d9ca0b2eb9afeb971f6addc04560fec",
        "closeout_module_utf8_bytes": 29707,
        "status": "COMPLETED_PROVENANCE",
        "behavior_sources": 5,
        "handler_scenarios": 7,
    },
    "authority_source_provenance": {
        "original_behavior_commit": "2a0b6a76d9a90e6fee5853c121fed253ec54b0d8",
        "metadata_repair_commit": "18ce99fb00fa985f5d7df6cce13323cd410fa546",
        "counted_behavior_units": 1,
        "added_behavior_units_in_metadata_repair": 0,
        "added_handler_scenarios_in_metadata_repair": 0,
    },
    "records": [],
    "raw_membership_delta": [],
    "counts_after_closeout": {
        "expected_seed_count": 465,
        "baseline_evidenced_seed_count": 135,
        "reconciled_new_seed_count": 263,
        "evidenced_seed_count": 398,
        "remaining_seed_count": 67,
    },
    "accounting": {
        "current_group_status_on_exact_acceptance": "COMPLETED_PROVENANCE",
        "registration_complete": True,
        "group_reconciliation_complete": True,
        "canonical_manifest_and_ledger_integration_complete": True,
        "ledger_integration_status": "COMPLETE",
        "physical_registration_sources_verified": 5,
        "physical_registration_scenarios_verified": 6,
        "remaining_current_group_units": 0,
        "remaining_current_group_scenarios": 0,
        "pending_five": 5,
        "units_since_reconciliation": 5,
        "total_behavior_units": 5,
        "total_handler_scenarios": 6,
        "added_behavior_units": 0,
        "added_handler_scenarios": 0,
        "added_distinct_seeds": 0,
        "accepted_full_seeds": 0,
        "raw_registration_delta": 0,
        "no_sixth_behavior_accepted_before_complete_reconciliation": True,
    },
    "strict_canonical_progress": {
        "proven": 288,
        "partial": 177,
        "total": 465,
    },
    "origin_matrix_unresolved_cells": 24,
    "canonical_blockers": [
        "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
        "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
    ],
    "coverage_status": "PARTIAL",
    "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
    "closures_authorized": False,
    "stages": {
        "P1-00": "COMPLETE",
        "P1-00b": "IN_PROGRESS",
    },
}

EXPECTED_NEW_LEDGER_SECTION = (
    "\n### 2026-11-03 UTC — Post-security five-source six-scenario group closeout checkpoint "
    "<a id=\"post-security-five-source-six-scenario-closeout\"></a>\n\n"
    "- Final closeout packet: [post-security-five-source-closeout.json](inventory/post-security-five-source-closeout.json) "
    "(`docs/refactor/two-server/section-1/inventory/post-security-five-source-closeout.json`); "
    "manifest mapping `business_768_post_security_five_closeout_path`.\n"
    "- Accepted immutable registration reference: "
    "`docs/refactor/two-server/section-1/inventory/post-security-five-source-registration.json` "
    "(`utf8_bytes=11779`, `sha256=56f5d4e76c4cd9edc97689fda6025e9fae9e059a80f3c8a995f457fa7d709bbe`, "
    "`accepted_commit=057bfdd7a33b6ef21954f8c22adb4bf1fb95cb67`), retained forever at its "
    "`PHYSICAL_REGISTRATION_VERIFIED_ONLY` / `ledger_integration_status=PENDING` snapshot; "
    "accepted registration witness module `tests/test_p1_00b_characterization_post_security_five_registration.py` "
    "(`utf8_bytes=40982`, `sha256=2f45dfc6d051e684da6a16c9002168c2e6c2a236b10a6dcc3de7a54524a7f780`).\n"
    "- Scope and task identity: `assignment_id=WA-P1-00B-COMPLETION-20261102-001`, "
    "`assignment_scope_sha256=80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1`, "
    "`parent_assignment_id=WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001`, "
    "`original_parent_assignment_id=WA-P1-00B-FREEZE-20260904-001`, "
    "`task_id=p1-00b-post-security-five-ledger-closeout-20261103-001`, "
    "`scenario_key=P1-00B-POST-SECURITY-FIVE-LEDGER-V1.accepted-registration-final-integration`, "
    "`source_head=057bfdd7a33b6ef21954f8c22adb4bf1fb95cb67`, "
    "`canonical_gap_reference=docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`.\n"
    "- Prior-group and authority provenance distinction: prior completed 5-source/7-scenario group at "
    "`5953f799f737780e8bcdfb57e8621030cb520422` (`closeout_source_head=b2d4ef0b1695f1e7a234648895ce644d7d4649a4`, "
    "`docs/refactor/two-server/section-1/inventory/current-session-five-source-closeout.json`, "
    "`utf8_bytes=2509`, `sha256=8eabc7b2fde077e6404eaee184a359ad2dc79add1a4a7b5ab1a2ed2d5c6b31b2`) "
    "remains separate immutable `COMPLETED_PROVENANCE`, not this 5-source/6-scenario group. "
    "Original authority behavior `2a0b6a76d9a90e6fee5853c121fed253ec54b0d8` plus metadata repair "
    "`18ce99fb00fa985f5d7df6cce13323cd410fa546` count once (`counted_behavior_units=1`, "
    "`added_behavior_units_in_metadata_repair=0`, `added_handler_scenarios_in_metadata_repair=0`), "
    "with zero new behavior, seed, or origin credit.\n"
    "- Conditional ONLY on exact independent Reviewer, native witness, Root semantic-byte, Git, and CP acceptance "
    "(no future receipt, commit, or CP acceptance claimed from a draft): "
    "`current_group_status_on_exact_acceptance=COMPLETED_PROVENANCE`, `registration_complete=true`, "
    "`group_reconciliation_complete=true`, `canonical_manifest_and_ledger_integration_complete=true`, "
    "`ledger_integration_status=COMPLETE`, `physical_registration_sources_verified=5` (5/5 sources), "
    "`physical_registration_scenarios_verified=6` (6/6 scenarios), `remaining_current_group_units=0`, "
    "`remaining_current_group_scenarios=0`, `no_sixth_behavior_accepted_before_complete_reconciliation=true`. "
    "Provenance counters `pending_five=5`, `units_since_reconciliation=5`, `total_behavior_units=5`, "
    "`total_handler_scenarios=6` remain completed provenance without reset or new debt; "
    "`added_behavior_units=0`, `added_handler_scenarios=0`, `added_distinct_seeds=0`, "
    "`accepted_full_seeds=0`, `raw_registration_delta=0`, `raw_membership_delta=[]`.\n"
    "- Counts and open gates: raw `expected_seed_count=465`, `baseline_evidenced_seed_count=135`, "
    "`reconciled_new_seed_count=263`, `evidenced_seed_count=398`, `remaining_seed_count=67`; "
    "strict `proven=288` (`PROVEN_FOR_REQUESTED_SCENARIO=288`), `partial=177` (`PARTIAL_OR_SEED_ONLY=177`), "
    "`total=465`; `origin_matrix_unresolved_cells=24`; `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` "
    "and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN` remain `OPEN`; `coverage_status=PARTIAL`, "
    "`seed_acceptance_status=FULL_SEED_ACCEPTANCE_NOT_ASSESSED`, `closures_authorized=false`, "
    "`P1-00=COMPLETE`, `P1-00b=IN_PROGRESS`.\n"
)


def _read_bytes(rel_path: str) -> bytes:
    return (REPO_ROOT / rel_path).read_bytes()


def _read_json(rel_path: str) -> dict[str, Any]:
    data = json.loads(_read_bytes(rel_path).decode("utf-8"))
    if type(data) is not dict:
        raise AssertionError(f"{rel_path} must decode to exact dict")
    return data


def _assert_typed_equal(actual: Any, expected: Any, path_label: str) -> None:
    if type(actual) is not type(expected):
        raise AssertionError(
            f"Type mismatch at {path_label}: {type(actual).__name__} != {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        if list(actual.keys()) != list(expected.keys()):
            raise AssertionError(
                f"Key order mismatch at {path_label}: {list(actual.keys())!r} != {list(expected.keys())!r}"
            )
        for key, exp_val in expected.items():
            _assert_typed_equal(actual[key], exp_val, f"{path_label}.{key}")
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(
                f"List length mismatch at {path_label}: {len(actual)} != {len(expected)}"
            )
        for idx, (act_item, exp_item) in enumerate(zip(actual, expected)):
            _assert_typed_equal(act_item, exp_item, f"{path_label}[{idx}]")
        return
    if actual != expected:
        raise AssertionError(f"Value mismatch at {path_label}: {actual!r} != {expected!r}")


def load_post_security_five_closeout_controls() -> tuple[
    dict[str, Any], dict[str, Any], bytes
]:
    """Return fresh (own_packet, full_manifest, ledger_bytes) loaded from disk."""
    _, full_manifest = registration.load_post_security_five_registration_controls()
    return (
        copy.deepcopy(_read_json(CLOSEOUT_PACKET_REL_PATH)),
        copy.deepcopy(full_manifest),
        _read_bytes(LEDGER_REL_PATH),
    )


def _validate_and_reconstruct_pre_closeout_manifest(full_manifest: dict[str, Any]) -> None:
    if type(full_manifest) is not dict:
        raise AssertionError("full_manifest must be exact dict")
    expected_top_keys = [
        "schema_version",
        "stage",
        "assignment_id",
        "inventory",
        "evidence_sources",
        "counts",
        "coverage_status",
        "canonical_blockers",
    ]
    if list(full_manifest.keys()) != expected_top_keys:
        raise AssertionError(
            f"full_manifest keys {list(full_manifest.keys())!r} != {expected_top_keys!r}"
        )
    evidence_sources = full_manifest["evidence_sources"]
    if type(evidence_sources) is not dict:
        raise AssertionError("full_manifest.evidence_sources must be exact dict")

    source_keys = list(evidence_sources.keys())
    if REGISTRATION_MANIFEST_KEY not in source_keys:
        raise AssertionError(f"Missing {REGISTRATION_MANIFEST_KEY!r} in evidence_sources")
    if CLOSEOUT_MANIFEST_KEY not in source_keys:
        raise AssertionError(f"Missing {CLOSEOUT_MANIFEST_KEY!r} in evidence_sources")

    reg_idx = source_keys.index(REGISTRATION_MANIFEST_KEY)
    closeout_idx = source_keys.index(CLOSEOUT_MANIFEST_KEY)
    if closeout_idx != reg_idx + 1:
        raise AssertionError(
            f"{CLOSEOUT_MANIFEST_KEY!r} must immediately follow {REGISTRATION_MANIFEST_KEY!r}"
        )
    _assert_typed_equal(
        evidence_sources[CLOSEOUT_MANIFEST_KEY],
        CLOSEOUT_PACKET_REL_PATH,
        f"full_manifest.evidence_sources.{CLOSEOUT_MANIFEST_KEY}",
    )

    seen_paths: set[str] = set()
    for key, val in evidence_sources.items():
        if type(key) is not str or not key.endswith("_path"):
            raise AssertionError(f"Invalid evidence_sources key {key!r}")
        if type(val) is not str or not val.startswith(
            "docs/refactor/two-server/section-1/inventory/"
        ) or not val.endswith(".json"):
            raise AssertionError(f"Invalid evidence_sources path {val!r} for {key!r}")
        if val in seen_paths:
            raise AssertionError(f"Duplicate evidence_sources path {val!r}")
        seen_paths.add(val)

    reconstructed = historical_manifest(full_manifest)
    reconstructed["evidence_sources"] = {
        k: evidence_sources[k] for k in source_keys[: reg_idx + 1]
    }
    raw_pre = (json.dumps(reconstructed, indent=2) + "\n").encode("utf-8")
    if len(raw_pre) != PRE_CLOSEOUT_MANIFEST_UTF8_BYTES:
        raise AssertionError(
            f"Reconstructed pre-closeout manifest byte length {len(raw_pre)} != {PRE_CLOSEOUT_MANIFEST_UTF8_BYTES}"
        )
    if hashlib.sha256(raw_pre).hexdigest() != PRE_CLOSEOUT_MANIFEST_SHA256:
        raise AssertionError("Reconstructed pre-closeout manifest sha256 mismatch")


def _validate_own_ledger_section(ledger_bytes: bytes) -> None:
    if type(ledger_bytes) is not bytes:
        raise AssertionError("ledger_bytes must be exact bytes")
    if len(ledger_bytes) <= LEDGER_PREFIX_UTF8_BYTES:
        raise AssertionError("ledger_bytes shorter than required immutable prefix")
    prefix = ledger_bytes[:LEDGER_PREFIX_UTF8_BYTES]
    if hashlib.sha256(prefix).hexdigest() != LEDGER_PREFIX_SHA256:
        raise AssertionError("Immutable 112060-byte ledger prefix sha256 mismatch")

    suffix_text = ledger_bytes[LEDGER_PREFIX_UTF8_BYTES:].decode("utf-8")
    if not suffix_text.startswith(EXPECTED_NEW_LEDGER_SECTION):
        raise AssertionError("New ledger section does not match expected closeout section")
    if suffix_text.count(LEDGER_SECTION_ANCHOR) != 1:
        raise AssertionError("Ledger closeout section anchor must appear exactly once")

    trailing = suffix_text[len(EXPECTED_NEW_LEDGER_SECTION) :]
    if trailing != "":
        if not trailing.startswith("\n### ") or not trailing.endswith("\n"):
            raise AssertionError("Trailing ledger content must consist of valid ### sections")
        if CLOSEOUT_MANIFEST_KEY in trailing or EXPECTED_CLOSEOUT_PACKET["task_id"] in trailing:
            raise AssertionError("Trailing ledger section duplicates or contradicts own closeout")


def assert_post_security_five_closeout(
    own_packet: dict[str, Any],
    full_manifest: dict[str, Any],
    ledger_bytes: bytes,
) -> None:
    """Validate post-security five-source closeout packet, full manifest, and ledger."""
    _assert_typed_equal(own_packet, EXPECTED_CLOSEOUT_PACKET, "own_packet")

    disk_closeout_bytes = _read_bytes(CLOSEOUT_PACKET_REL_PATH)
    expected_closeout_bytes = (json.dumps(EXPECTED_CLOSEOUT_PACKET, indent=2) + "\n").encode(
        "utf-8"
    )
    if disk_closeout_bytes != expected_closeout_bytes:
        raise AssertionError("Physical closeout packet on disk does not match canonical JSON")

    reg_mod_bytes = read_pin(REGISTRATION_MODULE_REL_PATH, REGISTRATION_MODULE_SHA256, REGISTRATION_MODULE_UTF8_BYTES, root=REPO_ROOT)
    if len(reg_mod_bytes) != REGISTRATION_MODULE_UTF8_BYTES:
        raise AssertionError("Accepted registration module byte length mismatch")
    if hashlib.sha256(reg_mod_bytes).hexdigest() != REGISTRATION_MODULE_SHA256:
        raise AssertionError("Accepted registration module sha256 mismatch")

    reg_pkt_bytes = _read_bytes(REGISTRATION_PACKET_REL_PATH)
    if len(reg_pkt_bytes) != REGISTRATION_PACKET_UTF8_BYTES:
        raise AssertionError("Accepted registration packet physical byte length mismatch")
    if hashlib.sha256(reg_pkt_bytes).hexdigest() != REGISTRATION_PACKET_SHA256:
        raise AssertionError("Accepted registration packet physical sha256 mismatch")

    _validate_and_reconstruct_pre_closeout_manifest(full_manifest)
    _validate_own_ledger_section(ledger_bytes)

    reg_packet, _ = registration.load_post_security_five_registration_controls()
    raw_reg_loaded = (json.dumps(reg_packet, indent=2) + "\n").encode("utf-8")
    if len(raw_reg_loaded) != REGISTRATION_PACKET_UTF8_BYTES:
        raise AssertionError("Loaded registration packet byte length mismatch")
    if hashlib.sha256(raw_reg_loaded).hexdigest() != REGISTRATION_PACKET_SHA256:
        raise AssertionError("Loaded registration packet sha256 mismatch")
    _assert_typed_equal(
        reg_packet["accounting"]["current_group_status_on_exact_acceptance"],
        "PHYSICAL_REGISTRATION_VERIFIED_ONLY",
        "reg_packet.accounting.current_group_status_on_exact_acceptance",
    )
    _assert_typed_equal(
        reg_packet["accounting"]["ledger_integration_status"],
        "PENDING",
        "reg_packet.accounting.ledger_integration_status",
    )

    registration.assert_post_security_five_registration(reg_packet, full_manifest)


class PostSecurityFiveLedgerCloseoutTests(unittest.TestCase):
    def test_actual_closeout_integrates_accepted_registration(self) -> None:
        packet, manifest, ledger_bytes = load_post_security_five_closeout_controls()
        packet_snapshot = copy.deepcopy(packet)
        manifest_snapshot = copy.deepcopy(manifest)
        ledger_snapshot = bytes(ledger_bytes)

        assert_post_security_five_closeout(packet, manifest, ledger_bytes)

        self.assertEqual(packet, packet_snapshot)
        self.assertEqual(manifest, manifest_snapshot)
        self.assertEqual(ledger_bytes, ledger_snapshot)

        future_manifest = copy.deepcopy(manifest)
        future_manifest["evidence_sources"]["business_768_future_additive_witness_path"] = (
            "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        future_ledger = (
            ledger_bytes
            + b"\n### 2026-11-04 UTC \xe2\x80\x94 Future additive checkpoint "
            + b"<a id=\"future-additive-checkpoint\"></a>\n\n- Future witness only.\n"
        )
        assert_post_security_five_closeout(
            copy.deepcopy(packet), future_manifest, future_ledger
        )

    def test_closeout_rejects_reference_manifest_ledger_and_accounting_drift(
        self,
    ) -> None:
        base_packet, base_manifest, base_ledger = (
            load_post_security_five_closeout_controls()
        )

        def assert_drift_rejected(
            pkt_mutator: Any = None,
            manifest_mutator: Any = None,
            ledger_override: bytes | None = None,
        ) -> None:
            pkt = copy.deepcopy(base_packet)
            man = copy.deepcopy(base_manifest)
            led = base_ledger if ledger_override is None else ledger_override
            if pkt_mutator is not None:
                pkt_mutator(pkt)
            if manifest_mutator is not None:
                manifest_mutator(man)
            with self.assertRaises(AssertionError):
                assert_post_security_five_closeout(pkt, man, led)

        # 1. Changed or omitted registration reference or non-empty records
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accepted_registration_reference"].__setitem__(
                "sha256", "0" * 64
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accepted_registration_reference"].__setitem__(
                "utf8_bytes", REGISTRATION_PACKET_UTF8_BYTES + 1
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p.pop("accepted_registration_reference")
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["records"].append({"path": "unexpected.py"})
        )

        # 2. Missing/wrong own manifest mapping or changed frozen counts
        assert_drift_rejected(
            manifest_mutator=lambda m: m["evidence_sources"].pop(CLOSEOUT_MANIFEST_KEY)
        )
        assert_drift_rejected(
            manifest_mutator=lambda m: m["evidence_sources"].__setitem__(
                CLOSEOUT_MANIFEST_KEY,
                "docs/refactor/two-server/section-1/inventory/wrong.json",
            )
        )
        assert_drift_rejected(
            manifest_mutator=lambda m: m["counts"].__setitem__(
                "evidenced_seed_count", 399
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["counts_after_closeout"].__setitem__(
                "remaining_seed_count", 66
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["strict_canonical_progress"].__setitem__(
                "proven", 289
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p.__setitem__("closures_authorized", True)
        )

        # 3. Historical ledger prefix change, missing, contradictory, or duplicate own section
        corrupted_prefix_ledger = (
            bytes([base_ledger[0] ^ 0x01]) + base_ledger[1:]
        )
        assert_drift_rejected(ledger_override=corrupted_prefix_ledger)
        assert_drift_rejected(ledger_override=base_ledger[:LEDGER_PREFIX_UTF8_BYTES])
        original_prefix = base_ledger[:LEDGER_PREFIX_UTF8_BYTES]
        own_suffix = base_ledger[LEDGER_PREFIX_UTF8_BYTES:]
        old_counter = b"remaining_current_group_scenarios=0"
        new_counter = b"remaining_current_group_scenarios=1"
        self.assertEqual(own_suffix.count(old_counter), 1)
        self.assertEqual(
            hashlib.sha256(original_prefix).hexdigest(), LEDGER_PREFIX_SHA256
        )
        changed_suffix = own_suffix.replace(old_counter, new_counter, 1)
        self.assertNotEqual(changed_suffix, own_suffix)
        contradictory_ledger = original_prefix + changed_suffix
        self.assertEqual(
            contradictory_ledger[:LEDGER_PREFIX_UTF8_BYTES], original_prefix
        )
        self.assertEqual(
            hashlib.sha256(contradictory_ledger[:LEDGER_PREFIX_UTF8_BYTES]).hexdigest(),
            LEDGER_PREFIX_SHA256,
        )
        assert_drift_rejected(ledger_override=contradictory_ledger)
        duplicate_ledger = base_ledger + EXPECTED_NEW_LEDGER_SECTION.encode("utf-8")
        assert_drift_rejected(ledger_override=duplicate_ledger)

        # 4. Inflated/reset accounting or conflated earlier 7-scenario group
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accounting"].__setitem__("pending_five", 0)
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accounting"].__setitem__(
                "units_since_reconciliation", 0
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accounting"].__setitem__(
                "added_behavior_units", 1
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accounting"].__setitem__(
                "added_distinct_seeds", 1
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["accounting"].__setitem__(
                "total_handler_scenarios", 7
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["prior_completed_group_provenance"].__setitem__(
                "handler_scenarios", 6
            )
        )
        assert_drift_rejected(
            pkt_mutator=lambda p: p["authority_source_provenance"].__setitem__(
                "counted_behavior_units", 2
            )
        )

        # 5. Changed immutable registration snapshot via temporary public-API patch
        orig_load = registration.load_post_security_five_registration_controls
        try:
            def _drifted_registration_controls() -> tuple[dict[str, Any], dict[str, Any]]:
                reg_pkt, man = orig_load()
                reg_pkt["accounting"]["ledger_integration_status"] = "COMPLETE"
                return reg_pkt, man

            registration.load_post_security_five_registration_controls = (
                _drifted_registration_controls
            )
            with self.assertRaises(AssertionError):
                assert_post_security_five_closeout(
                    copy.deepcopy(base_packet),
                    copy.deepcopy(base_manifest),
                    base_ledger,
                )
        finally:
            registration.load_post_security_five_registration_controls = orig_load

        assert_post_security_five_closeout(
            copy.deepcopy(base_packet), copy.deepcopy(base_manifest), base_ledger
        )


if __name__ == "__main__":
    unittest.main()
