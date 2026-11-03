from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from collections.abc import Callable
import copy
import hashlib
import json
import pathlib
from typing import Any
import unittest

import tests.test_p1_00b_characterization_current_session_group_union as group_union

from tests.p1_00b_membership_history import historical_manifest, read_pin

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

CLOSEOUT_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/current-session-five-source-closeout.json"
)
UNION_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/current-session-five-source-union.json"
)
MANIFEST_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
LEDGER_REL_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"
CANARY_FUTURE_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/surface-api.json"
)

UNION_MODULE_REL_PATH = (
    "tests/test_p1_00b_characterization_current_session_group_union.py"
)
UNION_MODULE_UTF8_BYTES = 32117
UNION_MODULE_SHA256 = (
    "b8813da90ae2462d33dff4cd0e4566a04b288edd5ee4b5ed53b0c871895e71fe"
)

PRE_BL_MANIFEST_UTF8_BYTES = 4864
PRE_BL_MANIFEST_SHA256 = (
    "1514c287c34a7a61f906e2b9e5fef6138f6c8e429175146f1fa9b71935d8cfe4"
)

LEDGER_PREFIX_UTF8_BYTES = 109329
LEDGER_PREFIX_SHA256 = (
    "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
)
LEDGER_EOF_ANCHOR = (
    "\nNo Source implementation, main/protected refs, SDK/CP/status/reservation/epoch, "
    "bridge publication/startup, provider/profile policy, production/deploy/DNS/Web "
    "Writer or memory change is granted.\n"
)

ASSIGNMENT_ID = "WA-P1-00B-COMPLETION-20261102-001"
ASSIGNMENT_SCOPE_SHA256 = (
    "80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1"
)
PARENT_ASSIGNMENT_ID = "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001"
ORIGINAL_PARENT_ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
TASK_ID = "p1-00b-current-session-group-ledger-closeout-20261103-002"
SCENARIO_KEY = "P1-00B-CURRENT-SESSION-GROUP-BL-V2.corrected-local-profile-closeout"
SOURCE_HEAD = "b2d4ef0b1695f1e7a234648895ce644d7d4649a4"

EXPECTED_ACCEPTED_UNION_REF = {
    "path": UNION_PACKET_REL_PATH,
    "utf8_bytes": 10792,
    "sha256": "5d9734c108edd3269cd56c264bed8079ded467d5c63a648c84db6eb639f6ab82",
    "accepted_commit": "b2d4ef0b1695f1e7a234648895ce644d7d4649a4",
}
UNION_REF_KEYS = ("path", "utf8_bytes", "sha256", "accepted_commit")

CLOSEOUT_PACKET_TOP_KEYS = (
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
    "accepted_union_reference",
    "raw_membership_delta",
    "counts_after_closeout",
    "accounting",
    "strict_canonical_progress",
    "origin_matrix_unresolved_cells",
    "canonical_blockers",
    "coverage_status",
    "seed_acceptance_status",
    "closures_authorized",
    "stages",
)

MANIFEST_TOP_KEYS = (
    "schema_version",
    "stage",
    "assignment_id",
    "inventory",
    "evidence_sources",
    "counts",
    "coverage_status",
    "canonical_blockers",
)

EXPECTED_COUNTS_AFTER_CLOSEOUT = {
    "expected_seed_count": 465,
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}

EXPECTED_CLOSEOUT_ACCOUNTING = {
    "current_group_status_on_exact_acceptance": "COMPLETED_PROVENANCE",
    "source_union_complete": True,
    "full_group_union_complete": True,
    "physical_union_sources_verified": 5,
    "physical_union_scenarios_verified": 7,
    "remaining_union_units": 0,
    "remaining_union_scenarios": 0,
    "remaining_current_group_units": 0,
    "remaining_current_group_scenarios": 0,
    "group_reconciliation_complete": True,
    "canonical_manifest_and_ledger_integration_complete": True,
    "ledger_integration_status": "COMPLETE",
    "outcome_b_l_required": False,
    "no_sixth_behavior_accepted_before_complete_reconciliation": True,
    "pending_five": 5,
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "total_handler_scenarios": 7,
    "added_behavior_units": 0,
    "added_distinct_seeds": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
}

EXPECTED_BLOCKERS = [
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
]

OWN_LEDGER_HEADING = (
    "### 2026-11-03 UTC — Current-session five-source seven-scenario group closeout "
    'checkpoint <a id="current-session-five-source-seven-scenario-closeout"></a>'
)

OWN_LEDGER_REQUIRED_SUBSTRINGS = (
    OWN_LEDGER_HEADING,
    CLOSEOUT_PACKET_REL_PATH,
    "business_768_current_session_group_closeout_path",
    UNION_PACKET_REL_PATH,
    "utf8_bytes=10792",
    "sha256=5d9734c108edd3269cd56c264bed8079ded467d5c63a648c84db6eb639f6ab82",
    "accepted_commit=b2d4ef0b1695f1e7a234648895ce644d7d4649a4",
    f"assignment_id={ASSIGNMENT_ID}",
    f"assignment_scope_sha256={ASSIGNMENT_SCOPE_SHA256}",
    f"parent_assignment_id={PARENT_ASSIGNMENT_ID}",
    f"original_parent_assignment_id={ORIGINAL_PARENT_ASSIGNMENT_ID}",
    f"task_id={TASK_ID}",
    f"scenario_key={SCENARIO_KEY}",
    f"source_head={SOURCE_HEAD}",
    "current_group_status_on_exact_acceptance=COMPLETED_PROVENANCE",
    "source_union_complete=true",
    "full_group_union_complete=true",
    "group_reconciliation_complete=true",
    "canonical_manifest_and_ledger_integration_complete=true",
    "ledger_integration_status=COMPLETE",
    "physical_union_sources_verified=5",
    "physical_union_scenarios_verified=7",
    "remaining_union_units=0",
    "remaining_union_scenarios=0",
    "remaining_current_group_units=0",
    "remaining_current_group_scenarios=0",
    "pending_five=5",
    "units_since_reconciliation=5",
    "total_behavior_units=5",
    "total_handler_scenarios=7",
    "added_behavior_units=0",
    "added_distinct_seeds=0",
    "accepted_full_seeds=0",
    "raw_registration_delta=0",
    "raw_membership_delta=[]",
    "expected_seed_count=465",
    "baseline_evidenced_seed_count=135",
    "reconciled_new_seed_count=263",
    "evidenced_seed_count=398",
    "remaining_seed_count=67",
    "PROVEN_FOR_REQUESTED_SCENARIO=288",
    "PARTIAL_OR_SEED_ONLY=177",
    "total=465",
    "origin_matrix_unresolved_cells=24",
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
    "OPEN",
    "coverage_status=PARTIAL",
    "seed_acceptance_status=FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
    "closures_authorized=false",
    "P1-00=COMPLETE",
    "P1-00b=IN_PROGRESS",
)

OWN_LEDGER_FORBIDDEN_SUBSTRINGS = (
    "closures_authorized=true",
    "P1-00b=COMPLETE",
    "coverage_status=COMPLETE",
    "seed_acceptance_status=COMPLETE",
    "origin_matrix_unresolved_cells=0",
)


def _read_bytes(rel_path: str) -> bytes:
    return (REPO_ROOT / rel_path).read_bytes()


def _read_json(rel_path: str) -> Any:
    return json.loads(_read_bytes(rel_path).decode("utf-8"))


def _fingerprint(value: Any) -> Any:
    if type(value) is dict:
        return ("dict", tuple((k, _fingerprint(v)) for k, v in value.items()))
    if type(value) is list:
        return ("list", tuple(_fingerprint(v) for v in value))
    return (type(value).__name__, value)


def _assert_typed_equal(actual: Any, expected: Any, label: str) -> None:
    if _fingerprint(actual) != _fingerprint(expected):
        raise AssertionError(f"{label} mismatch: {actual!r} != {expected!r}")


def load_current_group_ledger_closeout_controls() -> tuple[
    dict[str, Any], dict[str, Any], bytes
]:
    """Return fresh (final_packet, manifest, ledger_bytes) loaded from disk."""
    return (
        copy.deepcopy(_read_json(CLOSEOUT_PACKET_REL_PATH)),
        copy.deepcopy(_read_json(MANIFEST_REL_PATH)),
        _read_bytes(LEDGER_REL_PATH),
    )


def _reconstruct_pre_bl_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if list(manifest.keys()) != list(MANIFEST_TOP_KEYS):
        raise AssertionError("manifest root keys mismatch")
    ev = manifest.get("evidence_sources")
    if type(ev) is not dict:
        raise AssertionError("manifest.evidence_sources must be exact dict")
    keys = list(ev.keys())
    if "business_768_current_session_group_union_path" not in keys:
        raise AssertionError("missing business_768_current_session_group_union_path")
    if "business_768_current_session_group_closeout_path" not in keys:
        raise AssertionError("missing business_768_current_session_group_closeout_path")
    union_idx = keys.index("business_768_current_session_group_union_path")
    closeout_idx = keys.index("business_768_current_session_group_closeout_path")
    if closeout_idx != union_idx + 1:
        raise AssertionError(
            "business_768_current_session_group_closeout_path must immediately follow union path"
        )
    _assert_typed_equal(
        ev["business_768_current_session_group_closeout_path"],
        CLOSEOUT_PACKET_REL_PATH,
        "manifest.evidence_sources.business_768_current_session_group_closeout_path",
    )

    seen_paths: set[str] = set()
    for k, v in ev.items():
        if type(k) is not str or not k.endswith("_path"):
            raise AssertionError(f"invalid evidence_sources key {k!r}")
        if type(v) is not str or not v.endswith(".json"):
            raise AssertionError(f"invalid evidence_sources path {v!r}")
        if v in seen_paths:
            raise AssertionError(f"duplicate evidence_sources path {v!r}")
        seen_paths.add(v)

    for k in keys[closeout_idx + 1 :]:
        rel_path = ev[k]
        full_path = REPO_ROOT / rel_path
        if not full_path.is_file():
            raise AssertionError(f"future evidence source {rel_path!r} does not exist")
        doc = json.loads(full_path.read_bytes().decode("utf-8"))
        if type(doc) is not dict:
            raise AssertionError(f"future evidence source {rel_path!r} is not a JSON dict")

    pre_bl = historical_manifest(manifest)
    pre_bl["evidence_sources"] = {k: copy.deepcopy(ev[k]) for k in keys[: union_idx + 1]}
    raw_pre_bl = (json.dumps(pre_bl, indent=2) + "\n").encode("utf-8")
    if len(raw_pre_bl) != PRE_BL_MANIFEST_UTF8_BYTES:
        raise AssertionError("reconstructed pre-B-L manifest byte length mismatch")
    if hashlib.sha256(raw_pre_bl).hexdigest() != PRE_BL_MANIFEST_SHA256:
        raise AssertionError("reconstructed pre-B-L manifest sha256 mismatch")
    return pre_bl


def _validate_ledger_bytes(ledger_bytes: bytes) -> None:
    if type(ledger_bytes) is not bytes:
        raise AssertionError("ledger_bytes must be exact bytes")
    if len(ledger_bytes) <= LEDGER_PREFIX_UTF8_BYTES:
        raise AssertionError("ledger_bytes must contain immutable prefix plus B-L append")
    prefix = ledger_bytes[:LEDGER_PREFIX_UTF8_BYTES]
    if hashlib.sha256(prefix).hexdigest() != LEDGER_PREFIX_SHA256:
        raise AssertionError("immutable ledger prefix sha256 mismatch")
    anchor_bytes = LEDGER_EOF_ANCHOR.encode("utf-8")
    if len(anchor_bytes) != 196 or not prefix.endswith(anchor_bytes):
        raise AssertionError("immutable ledger prefix does not end with exact 196B anchor")
    if prefix.count(anchor_bytes) != 1:
        raise AssertionError("immutable ledger prefix anchor must occur exactly once")

    suffix_text = ledger_bytes[LEDGER_PREFIX_UTF8_BYTES:].decode("utf-8")
    if not suffix_text.startswith("\n" + OWN_LEDGER_HEADING + "\n"):
        raise AssertionError("ledger append must begin with exact B-L closeout heading")
    if not suffix_text.endswith("\n"):
        raise AssertionError("ledger append must end with newline")

    body_after_heading = suffix_text[1 + len(OWN_LEDGER_HEADING) :]
    next_sec_idx = body_after_heading.find("\n### ")
    if next_sec_idx == -1:
        own_section = suffix_text[1:]
    else:
        own_section = suffix_text[1 : 1 + len(OWN_LEDGER_HEADING) + next_sec_idx + 1]
        later_section = body_after_heading[next_sec_idx + 1 :]
        if not later_section.startswith("### ") or len(later_section.strip()) < 10:
            raise AssertionError("malformed later ledger section")

    for req in OWN_LEDGER_REQUIRED_SUBSTRINGS:
        if req not in own_section:
            raise AssertionError(f"own ledger section missing required token: {req!r}")
    for forbidden in OWN_LEDGER_FORBIDDEN_SUBSTRINGS:
        if forbidden in own_section:
            raise AssertionError(
                f"own ledger section contains forbidden closure token: {forbidden!r}"
            )


def assert_current_group_ledger_closeout(
    final_packet: dict[str, Any],
    manifest: dict[str, Any],
    ledger_bytes: bytes,
) -> None:
    """Validate supplied B-L closeout packet, manifest, and ledger bytes."""
    if type(final_packet) is not dict:
        raise AssertionError("final_packet must be exact dict")
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if type(ledger_bytes) is not bytes:
        raise AssertionError("ledger_bytes must be exact bytes")

    if list(final_packet.keys()) != list(CLOSEOUT_PACKET_TOP_KEYS):
        raise AssertionError(
            f"final_packet top keys {list(final_packet.keys())!r} != {list(CLOSEOUT_PACKET_TOP_KEYS)!r}"
        )

    union_mod_bytes = read_pin(UNION_MODULE_REL_PATH, UNION_MODULE_SHA256, UNION_MODULE_UTF8_BYTES, root=REPO_ROOT)
    if len(union_mod_bytes) != UNION_MODULE_UTF8_BYTES:
        raise AssertionError("accepted B-U module byte length mismatch")
    if hashlib.sha256(union_mod_bytes).hexdigest() != UNION_MODULE_SHA256:
        raise AssertionError("accepted B-U module sha256 mismatch")

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
        _assert_typed_equal(final_packet[key], exp_val, f"final_packet.{key}")

    _assert_typed_equal(final_packet["records"], [], "final_packet.records")

    union_ref = final_packet["accepted_union_reference"]
    if type(union_ref) is not dict or list(union_ref.keys()) != list(UNION_REF_KEYS):
        raise AssertionError("final_packet.accepted_union_reference keys mismatch")
    _assert_typed_equal(
        union_ref,
        EXPECTED_ACCEPTED_UNION_REF,
        "final_packet.accepted_union_reference",
    )

    _assert_typed_equal(
        final_packet["raw_membership_delta"],
        [],
        "final_packet.raw_membership_delta",
    )
    _assert_typed_equal(
        final_packet["counts_after_closeout"],
        EXPECTED_COUNTS_AFTER_CLOSEOUT,
        "final_packet.counts_after_closeout",
    )

    accounting = final_packet["accounting"]
    if type(accounting) is not dict or list(accounting.keys()) != list(
        EXPECTED_CLOSEOUT_ACCOUNTING.keys()
    ):
        raise AssertionError("final_packet.accounting keys mismatch")
    _assert_typed_equal(
        accounting,
        EXPECTED_CLOSEOUT_ACCOUNTING,
        "final_packet.accounting",
    )

    _assert_typed_equal(
        final_packet["strict_canonical_progress"],
        {"proven": 288, "partial": 177, "total": 465},
        "final_packet.strict_canonical_progress",
    )
    _assert_typed_equal(
        final_packet["origin_matrix_unresolved_cells"],
        24,
        "final_packet.origin_matrix_unresolved_cells",
    )
    _assert_typed_equal(
        final_packet["canonical_blockers"],
        EXPECTED_BLOCKERS,
        "final_packet.canonical_blockers",
    )
    _assert_typed_equal(
        final_packet["coverage_status"],
        "PARTIAL",
        "final_packet.coverage_status",
    )
    _assert_typed_equal(
        final_packet["seed_acceptance_status"],
        "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        "final_packet.seed_acceptance_status",
    )
    _assert_typed_equal(
        final_packet["closures_authorized"],
        False,
        "final_packet.closures_authorized",
    )
    _assert_typed_equal(
        final_packet["stages"],
        {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        "final_packet.stages",
    )

    raw_union_disk = _read_bytes(UNION_PACKET_REL_PATH)
    if len(raw_union_disk) != EXPECTED_ACCEPTED_UNION_REF["utf8_bytes"]:
        raise AssertionError("on-disk B-U packet byte length mismatch")
    if hashlib.sha256(raw_union_disk).hexdigest() != EXPECTED_ACCEPTED_UNION_REF["sha256"]:
        raise AssertionError("on-disk B-U packet sha256 mismatch")

    union_packet, _ = group_union.load_current_group_union_controls()
    raw_union_loaded = (json.dumps(union_packet, indent=2) + "\n").encode("utf-8")
    if len(raw_union_loaded) != EXPECTED_ACCEPTED_UNION_REF["utf8_bytes"]:
        raise AssertionError("loaded B-U packet byte length mismatch")
    if hashlib.sha256(raw_union_loaded).hexdigest() != EXPECTED_ACCEPTED_UNION_REF["sha256"]:
        raise AssertionError("loaded B-U packet sha256 mismatch")

    union_acc = union_packet["accounting"]
    _assert_typed_equal(
        union_acc["current_group_status_on_exact_acceptance"],
        "PHYSICAL_UNION_VERIFIED_ONLY",
        "union_packet.accounting.current_group_status_on_exact_acceptance",
    )
    _assert_typed_equal(
        union_acc["source_union_complete"],
        True,
        "union_packet.accounting.source_union_complete",
    )
    _assert_typed_equal(
        union_acc["full_group_union_complete"],
        True,
        "union_packet.accounting.full_group_union_complete",
    )
    _assert_typed_equal(
        union_acc["group_reconciliation_complete"],
        False,
        "union_packet.accounting.group_reconciliation_complete",
    )
    _assert_typed_equal(
        union_acc["canonical_manifest_and_ledger_integration_complete"],
        False,
        "union_packet.accounting.canonical_manifest_and_ledger_integration_complete",
    )
    _assert_typed_equal(
        union_acc["ledger_integration_status"],
        "PENDING",
        "union_packet.accounting.ledger_integration_status",
    )

    pre_bl_manifest = _reconstruct_pre_bl_manifest(manifest)
    group_union.assert_current_group_union(union_packet, pre_bl_manifest)
    group_union.assert_current_group_union(union_packet, manifest)

    _validate_ledger_bytes(ledger_bytes)


def _ordered_snapshot(
    packet: dict[str, Any], manifest: dict[str, Any], ledger_bytes: bytes
) -> tuple[str, str, bytes, Any, Any]:
    return (
        json.dumps(packet, indent=2),
        json.dumps(manifest, indent=2),
        bytes(ledger_bytes),
        _fingerprint(packet),
        _fingerprint(manifest),
    )


class CurrentSessionGroupLedgerCloseoutTests(unittest.TestCase):
    def test_actual_group_ledger_closeout_preserves_canonical_membership(self) -> None:
        packet, manifest, ledger_bytes = load_current_group_ledger_closeout_controls()
        raw_packet_disk = _read_bytes(CLOSEOUT_PACKET_REL_PATH)
        raw_manifest_disk = _read_bytes(MANIFEST_REL_PATH)
        self.assertEqual(
            (json.dumps(packet, indent=2) + "\n").encode("utf-8"),
            raw_packet_disk,
        )
        self.assertEqual(
            (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            raw_manifest_disk,
        )

        snap_before = _ordered_snapshot(packet, manifest, ledger_bytes)
        assert_current_group_ledger_closeout(packet, manifest, ledger_bytes)
        self.assertEqual(
            _ordered_snapshot(packet, manifest, ledger_bytes),
            snap_before,
        )

        future_manifest = copy.deepcopy(manifest)
        future_manifest["evidence_sources"]["future_surface_api_canary_path"] = (
            CANARY_FUTURE_REL_PATH
        )
        future_ledger = (
            ledger_bytes
            + b"\n### 2026-11-04 UTC \xe2\x80\x94 Later legitimate checkpoint\n\nRetained future append.\n"
        )
        future_snap = _ordered_snapshot(packet, future_manifest, future_ledger)
        assert_current_group_ledger_closeout(packet, future_manifest, future_ledger)
        self.assertEqual(
            _ordered_snapshot(packet, future_manifest, future_ledger),
            future_snap,
        )

    def test_ledger_closeout_faults_reject_and_restore(self) -> None:
        MutationFn = Callable[[dict[str, Any], dict[str, Any], bytes], bytes]

        def _mut_packet(fn: Callable[[dict[str, Any]], None]) -> MutationFn:
            def _apply(p: dict[str, Any], m: dict[str, Any], lb: bytes) -> bytes:
                fn(p)
                return lb

            return _apply

        def _mut_manifest(fn: Callable[[dict[str, Any]], None]) -> MutationFn:
            def _apply(p: dict[str, Any], m: dict[str, Any], lb: bytes) -> bytes:
                fn(m)
                return lb

            return _apply

        def _reinsert_top_key(d: dict[str, Any], key: str) -> None:
            val = d.pop(key)
            d[key] = val

        def _interpose_closeout_key(m: dict[str, Any]) -> None:
            ev = m["evidence_sources"]
            items = list(ev.items())
            closeout_item = items.pop()
            items.insert(0, closeout_item)
            m["evidence_sources"] = dict(items)

        fault_rows: tuple[tuple[str, MutationFn], ...] = (
            (
                "wrong_accepted_union_sha",
                _mut_packet(
                    lambda p: p["accepted_union_reference"].__setitem__(
                        "sha256", "0" * 64
                    )
                ),
            ),
            (
                "missing_accepted_union_key",
                _mut_packet(
                    lambda p: p["accepted_union_reference"].pop("accepted_commit")
                ),
            ),
            (
                "extra_accepted_union_key",
                _mut_packet(
                    lambda p: p["accepted_union_reference"].__setitem__("extra", 1)
                ),
            ),
            (
                "reordered_accepted_union_keys",
                _mut_packet(
                    lambda p: _reinsert_top_key(p["accepted_union_reference"], "path")
                ),
            ),
            (
                "wrong_task_id",
                _mut_packet(
                    lambda p: p.__setitem__(
                        "task_id",
                        "p1-00b-current-session-group-ledger-closeout-20261103-001",
                    )
                ),
            ),
            (
                "wrong_scenario_key",
                _mut_packet(lambda p: p.__setitem__("scenario_key", "WRONG_KEY")),
            ),
            (
                "reordered_packet_top_keys",
                _mut_packet(lambda p: _reinsert_top_key(p, "schema_version")),
            ),
            (
                "extra_packet_top_key",
                _mut_packet(lambda p: p.__setitem__("extra_root", True)),
            ),
            (
                "nonempty_records",
                _mut_packet(lambda p: p["records"].append({"id": 1})),
            ),
            (
                "bool_to_int_closures_authorized",
                _mut_packet(lambda p: p.__setitem__("closures_authorized", 0)),
            ),
            (
                "int_to_float_evidenced_count",
                _mut_packet(
                    lambda p: p["counts_after_closeout"].__setitem__(
                        "evidenced_seed_count", 398.0
                    )
                ),
            ),
            (
                "wrong_group_status_pending",
                _mut_packet(
                    lambda p: p["accounting"].__setitem__(
                        "current_group_status_on_exact_acceptance",
                        "PHYSICAL_UNION_VERIFIED_ONLY",
                    )
                ),
            ),
            (
                "nonzero_added_behavior_units",
                _mut_packet(
                    lambda p: p["accounting"].__setitem__("added_behavior_units", 1)
                ),
            ),
            (
                "nonzero_raw_registration_delta",
                _mut_packet(
                    lambda p: p["accounting"].__setitem__("raw_registration_delta", 1)
                ),
            ),
            (
                "nonempty_raw_membership_delta",
                _mut_packet(
                    lambda p: p["raw_membership_delta"].append(
                        "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562"
                    )
                ),
            ),
            (
                "counter_reset_pending_five",
                _mut_packet(lambda p: p["accounting"].__setitem__("pending_five", 0)),
            ),
            (
                "premature_high_blocker_closure",
                _mut_packet(lambda p: p["canonical_blockers"].pop()),
            ),
            (
                "premature_origin_closure",
                _mut_packet(
                    lambda p: p.__setitem__("origin_matrix_unresolved_cells", 23)
                ),
            ),
            (
                "premature_stage_closure",
                _mut_packet(lambda p: p["stages"].__setitem__("P1-00b", "COMPLETE")),
            ),
            (
                "manifest_missing_closeout_mapping",
                _mut_manifest(
                    lambda m: m["evidence_sources"].pop(
                        "business_768_current_session_group_closeout_path"
                    )
                ),
            ),
            (
                "manifest_wrong_closeout_target",
                _mut_manifest(
                    lambda m: m["evidence_sources"].__setitem__(
                        "business_768_current_session_group_closeout_path",
                        CANARY_FUTURE_REL_PATH,
                    )
                ),
            ),
            (
                "manifest_interposed_closeout_mapping",
                _mut_manifest(_interpose_closeout_key),
            ),
            (
                "manifest_extra_root_key",
                _mut_manifest(lambda m: m.__setitem__("extra_root", 1)),
            ),
            (
                "manifest_reordered_root_key",
                _mut_manifest(lambda m: _reinsert_top_key(m, "schema_version")),
            ),
            (
                "manifest_duplicate_future_path",
                _mut_manifest(
                    lambda m: m["evidence_sources"].__setitem__(
                        "duplicate_future_path", CLOSEOUT_PACKET_REL_PATH
                    )
                ),
            ),
            (
                "manifest_bad_future_key_suffix",
                _mut_manifest(
                    lambda m: m["evidence_sources"].__setitem__(
                        "bad_future_key", CANARY_FUTURE_REL_PATH
                    )
                ),
            ),
            (
                "ledger_prefix_corrupted",
                lambda p, m, lb: b"X" + lb[1:],
            ),
            (
                "ledger_missing_own_section",
                lambda p, m, lb: lb[:LEDGER_PREFIX_UTF8_BYTES],
            ),
            (
                "ledger_damaged_own_section_counter",
                lambda p, m, lb: lb.replace(
                    b"evidenced_seed_count=398", b"evidenced_seed_count=399"
                ),
            ),
            (
                "ledger_premature_closure_claim",
                lambda p, m, lb: lb.replace(
                    b"closures_authorized=false", b"closures_authorized=true"
                ),
            ),
        )

        for label, mutate_fn in fault_rows:
            with self.subTest(fault=label):
                good_p_before, good_m_before, good_lb_before = (
                    load_current_group_ledger_closeout_controls()
                )
                assert_current_group_ledger_closeout(
                    good_p_before, good_m_before, good_lb_before
                )

                bad_p = copy.deepcopy(good_p_before)
                bad_m = copy.deepcopy(good_m_before)
                healthy_snap = _ordered_snapshot(
                    good_p_before, good_m_before, good_lb_before
                )
                bad_lb = mutate_fn(bad_p, bad_m, good_lb_before)
                mutated_snap = _ordered_snapshot(bad_p, bad_m, bad_lb)
                self.assertNotEqual(
                    mutated_snap,
                    healthy_snap,
                    f"{label} did not mutate controls",
                )

                with self.assertRaises(AssertionError, msg=f"{label} did not fail"):
                    assert_current_group_ledger_closeout(bad_p, bad_m, bad_lb)

                self.assertEqual(
                    _ordered_snapshot(bad_p, bad_m, bad_lb),
                    mutated_snap,
                    f"{label} mutated supplied bad inputs during assertion",
                )

                good_p_after, good_m_after, good_lb_after = (
                    load_current_group_ledger_closeout_controls()
                )
                assert_current_group_ledger_closeout(
                    good_p_after, good_m_after, good_lb_after
                )
                self.assertEqual(
                    _ordered_snapshot(good_p_after, good_m_after, good_lb_after),
                    healthy_snap,
                )


if __name__ == "__main__":
    unittest.main()
