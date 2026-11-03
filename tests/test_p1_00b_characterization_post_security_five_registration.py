from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
from collections.abc import Callable
import copy
import hashlib
import json
import pathlib
import stat
from typing import Any
import unittest

import tests.test_p1_00b_characterization_current_session_group_ledger_closeout as group_closeout

from tests.p1_00b_membership_history import historical_manifest, read_pin

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "post-security-five-source-registration.json"
)
MANIFEST_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
OLD_CLOSEOUT_MODULE_REL_PATH = (
    "tests/test_p1_00b_characterization_current_session_group_ledger_closeout.py"
)
OLD_CLOSEOUT_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "current-session-five-source-closeout.json"
)
FUTURE_CANARY_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/surface-api.json"
)

OLD_CLOSEOUT_MODULE_UTF8_BYTES = 29707
OLD_CLOSEOUT_MODULE_SHA256 = (
    "b901faa9502d86bc95dd6a9ce0c3adeb9d9ca0b2eb9afeb971f6addc04560fec"
)
OLD_CLOSEOUT_PACKET_UTF8_BYTES = 2509
OLD_CLOSEOUT_PACKET_SHA256 = (
    "8eabc7b2fde077e6404eaee184a359ad2dc79add1a4a7b5ab1a2ed2d5c6b31b2"
)
PRE_REGISTRATION_MANIFEST_UTF8_BYTES = 5010
PRE_REGISTRATION_MANIFEST_SHA256 = (
    "390d43899fa2e3cd1ee3f9e7d7491524565ccaafdb08801676d8c77b6eca3f2e"
)

OWN_MANIFEST_KEY = "business_768_post_security_five_registration_path"
PRIOR_MANIFEST_LAST_KEY = "business_768_current_session_group_closeout_path"

ASSIGNMENT_ID = "WA-P1-00B-COMPLETION-20261102-001"
ASSIGNMENT_SCOPE_SHA256 = (
    "80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1"
)
PARENT_ASSIGNMENT_ID = "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001"
ORIGINAL_PARENT_ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
TASK_ID = "p1-00b-post-security-five-source-registration-20261103-002"
SCENARIO_KEY = (
    "P1-00B-POST-SECURITY-FIVE-REGISTRATION-V1."
    "all-five-sources-six-existing-scenarios"
)
SOURCE_HEAD = "18ce99fb00fa985f5d7df6cce13323cd410fa546"
CANONICAL_GAP_REFERENCE = (
    "docs/refactor/two-server/section-1/inventory/"
    "scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
)

PACKET_TOP_KEYS: tuple[str, ...] = (
    "schema_version",
    "stage",
    "assignment_id",
    "assignment_scope_sha256",
    "parent_assignment_id",
    "original_parent_assignment_id",
    "task_id",
    "scenario_key",
    "source_head",
    "canonical_gap_reference",
    "prior_completed_group_provenance",
    "authority_source_provenance",
    "records",
    "selector_associations",
    "shared_contract_and_exclusions",
    "raw_membership_delta",
    "counts",
    "accounting",
    "strict_canonical_progress",
    "origin_matrix_unresolved_cells",
    "canonical_blockers",
    "coverage_status",
    "seed_acceptance_status",
    "closures_authorized",
    "stages",
)

RECORD_KEYS: tuple[str, ...] = (
    "path",
    "sha256",
    "utf8_bytes",
    "mode",
    "last_commit",
    "parent",
    "test_selectors",
    "seed_ids",
)

ASSOCIATION_KEYS: tuple[str, ...] = (
    "record_index",
    "selector_index",
    "source_path",
    "test_selector",
    "handler",
    "seed_id",
    "contract_summary",
)

MANIFEST_ROOT_KEYS: tuple[str, ...] = (
    "schema_version",
    "stage",
    "assignment_id",
    "inventory",
    "evidence_sources",
    "counts",
    "coverage_status",
    "canonical_blockers",
)

PRE_REGISTRATION_EVIDENCE_KEYS: tuple[str, ...] = (
    "baseline_path",
    "base_path",
    "continuation_path",
    "continuation_2_path",
    "continuation_3_path",
    "continuation_4_path",
    "continuation_5_path",
    "continuation_6_path",
    "continuation_7_path",
    "continuation_8_path",
    "continuation_9_path",
    "continuation_10_path",
    "continuation_11_path",
    "continuation_13_path",
    "continuation_16_path",
    "continuation_17_path",
    "notification_mark_all_750_path",
    "trade_read_prequery_757_path",
    "business_766_a2_path",
    "business_766_b3_path",
    "business_768_market_overrides_a2_path",
    "business_768_market_overrides_b3_path",
    "business_768_live786_manual783_path",
    "business_768_live786_five_source_closeout_path",
    "business_768_live786_reset_execute_singleton_path",
    "business_768_live786_replace_execute_singleton_path",
    "business_768_live786_verifier_only_path",
    "business_768_live786_authority_registration_after_security_path",
    "business_768_current_session_body_registration_path",
    "business_768_current_session_group_union_path",
    "business_768_current_session_group_closeout_path",
)

SEED_RESET = "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562"
SEED_REPLACE = "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593"
SEED_AUTHORITY = "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539"

HANDLER_RESET = "api/routers/sessions.py:internal_reset_user_sessions"
HANDLER_REPLACE = "api/routers/sessions.py:internal_replace_user_sessions"
HANDLER_AUTHORITY = "api/routers/sessions.py:internal_session_authority_check"

SOURCE_FACTS: tuple[tuple[str, str, int, str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "tests/test_p1_00b_characterization_internal_session_service_failures_batch.py",
        "cef0bd510cfc5403c6afe571a772da228fc1c6efb1e3505f3230b81b41b4a8d4",
        17624,
        "43ddd90bd668f2832437878a160b55934211a78b",
        "7f1e230134e948ab27858392224ba3fb57c64c58",
        (
            "tests/test_p1_00b_characterization_internal_session_service_failures_batch.py::InternalSessionServiceFailuresCharacterizationTests::test_reset_service_exception_propagates_after_matching_user",
            "tests/test_p1_00b_characterization_internal_session_service_failures_batch.py::InternalSessionServiceFailuresCharacterizationTests::test_replace_service_exception_propagates_after_matching_user",
        ),
        (SEED_RESET, SEED_REPLACE),
    ),
    (
        "tests/test_p1_00b_characterization_internal_reset_scalar_failure.py",
        "ba68ed364c673d95c835cd534ca9222f1776058769317edfa321b7855be32526",
        21185,
        "515f70dca516c9f3ee502159bd74c2d42536e474",
        "43ddd90bd668f2832437878a160b55934211a78b",
        (
            "tests/test_p1_00b_characterization_internal_reset_scalar_failure.py::InternalResetScalarFailureCharacterizationTests::test_scalar_exception_propagates_before_reset_side_effects",
        ),
        (SEED_RESET,),
    ),
    (
        "tests/test_p1_00b_characterization_internal_replace_scalar_failure.py",
        "02c27ebe502868efdccb02cbaa7e35ca4c1d0216260cd606935822c43747566f",
        21635,
        "5eba653fdb0390b51dbb1970cb62fc1c0c4c8573",
        "515f70dca516c9f3ee502159bd74c2d42536e474",
        (
            "tests/test_p1_00b_characterization_internal_replace_scalar_failure.py::InternalReplaceScalarFailureCharacterizationTests::test_scalar_exception_propagates_before_replace_side_effects",
        ),
        (SEED_REPLACE,),
    ),
    (
        "tests/test_p1_00b_characterization_internal_replace_success_response.py",
        "266b4644cb8e5f5cdbe34765b204e4f22a81c02df3608588ea8534b7dff949b8",
        21537,
        "cc3a6645d6b8cc7f4bd6957fbaa6a73fadace936",
        "5eba653fdb0390b51dbb1970cb62fc1c0c4c8573",
        (
            "tests/test_p1_00b_characterization_internal_replace_success_response.py::InternalReplaceSuccessResponseCharacterizationTests::test_successful_service_result_is_converted_to_integer_response",
        ),
        (SEED_REPLACE,),
    ),
    (
        "tests/test_p1_00b_characterization_internal_authority_body_failure.py",
        "73d1dbc52c0fc0fceab6f3831beba69117c82b7124665f6a0d8e008df1a2d560",
        8694,
        "18ce99fb00fa985f5d7df6cce13323cd410fa546",
        "2a0b6a76d9a90e6fee5853c121fed253ec54b0d8",
        (
            "tests/test_p1_00b_characterization_internal_authority_body_failure.py::InternalAuthorityBodyFailureCharacterizationTests::test_body_error_propagates_before_headers_verifier_and_query",
        ),
        (SEED_AUTHORITY,),
    ),
)

ASSOCIATION_FACTS: tuple[tuple[int, int, str, str, str, tuple[str, ...]], ...] = (
    (
        0,
        0,
        HANDLER_RESET,
        SEED_RESET,
        "selected reset_user_session_state RuntimeError propagates after stubbed signature verification and matching User lookup",
        ("reset_user_session_state", "RuntimeError"),
    ),
    (
        0,
        1,
        HANDLER_REPLACE,
        SEED_REPLACE,
        "selected force_clear_sessions RuntimeError propagates after stubbed signature verification and matching User lookup",
        ("force_clear_sessions", "RuntimeError"),
    ),
    (
        1,
        0,
        HANDLER_RESET,
        SEED_RESET,
        "synchronous scalar_one_or_none sentinel RuntimeError propagates before reset service side effects after actual execute/ORM path",
        ("scalar_one_or_none", "execute"),
    ),
    (
        2,
        0,
        HANDLER_REPLACE,
        SEED_REPLACE,
        "synchronous scalar_one_or_none sentinel RuntimeError propagates before force_clear_sessions side effects after actual execute/ORM path",
        ("scalar_one_or_none", "force_clear"),
    ),
    (
        3,
        0,
        HANDLER_REPLACE,
        SEED_REPLACE,
        "successful stubbed force_clear_sessions synthetic result object is converted once via __int__ to exact integer 7 response",
        ("__int__", "7"),
    ),
    (
        4,
        0,
        HANDLER_AUTHORITY,
        SEED_AUTHORITY,
        "await request.body() sentinel RuntimeError propagates before headers access, signature verifier, and DB query",
        ("request.body", "headers"),
    ),
)

EXPECTED_PRIOR_PROVENANCE: dict[str, Any] = {
    "accepted_commit": "5953f799f737780e8bcdfb57e8621030cb520422",
    "closeout_source_head": "b2d4ef0b1695f1e7a234648895ce644d7d4649a4",
    "closeout_packet_path": OLD_CLOSEOUT_PACKET_REL_PATH,
    "closeout_packet_sha256": OLD_CLOSEOUT_PACKET_SHA256,
    "closeout_packet_utf8_bytes": OLD_CLOSEOUT_PACKET_UTF8_BYTES,
    "closeout_module_path": OLD_CLOSEOUT_MODULE_REL_PATH,
    "closeout_module_sha256": OLD_CLOSEOUT_MODULE_SHA256,
    "closeout_module_utf8_bytes": OLD_CLOSEOUT_MODULE_UTF8_BYTES,
    "status": "COMPLETED_PROVENANCE",
    "behavior_sources": 5,
    "handler_scenarios": 7,
}

EXPECTED_AUTHORITY_PROVENANCE: dict[str, Any] = {
    "original_behavior_commit": "2a0b6a76d9a90e6fee5853c121fed253ec54b0d8",
    "metadata_repair_commit": SOURCE_HEAD,
    "counted_behavior_units": 1,
    "added_behavior_units_in_metadata_repair": 0,
    "added_handler_scenarios_in_metadata_repair": 0,
}

EXPECTED_SHARED_EXCLUSIONS: dict[str, str] = {
    "synthetic_service_count_witness": (
        "Router int(revoked_count) coercion witness only; does not claim "
        "production force_clear_sessions returns non-int objects."
    ),
    "synthetic_lookup_and_result_witnesses": (
        "Bounded router/ORM witnesses only; do not prove database persistence, "
        "session revocation correctness, or actual database driver failure."
    ),
    "signature_and_transport_exclusions": (
        "Stubbed verify_session_authority_signature proves neither cryptographic "
        "HMAC correctness nor FastAPI HTTP/Depends transport wiring."
    ),
    "closure_exclusions": (
        "Does not promote any seed, close canonical High blockers, resolve origin "
        "matrix cells, close stage P1-00b, or perform current-group ledger closeout."
    ),
}

EXPECTED_COUNTS: dict[str, int] = {
    "expected_seed_count": 465,
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}

EXPECTED_ACCOUNTING: dict[str, Any] = {
    "current_group_status_on_exact_acceptance": "PHYSICAL_REGISTRATION_VERIFIED_ONLY",
    "behavior_sources": 5,
    "handler_scenarios": 6,
    "distinct_existing_seed_ids": 3,
    "registration_complete": True,
    "registration_remaining_sources": 0,
    "registration_remaining_scenarios": 0,
    "group_reconciliation_complete": False,
    "canonical_manifest_and_ledger_integration_complete": False,
    "ledger_integration_status": "PENDING",
    "no_sixth_behavior_before_ledger_closeout": True,
    "added_behavior_units": 0,
    "added_handler_scenarios": 0,
    "added_distinct_seeds": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
}

EXPECTED_STRICT_PROGRESS: dict[str, int] = {
    "proven": 288,
    "partial": 177,
    "total": 465,
}

EXPECTED_BLOCKERS: list[str] = [
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
]

EXPECTED_STAGES: dict[str, str] = {
    "P1-00": "COMPLETE",
    "P1-00b": "IN_PROGRESS",
}


def _read_bytes(rel_path: str) -> bytes:
    if type(rel_path) is not str or not rel_path:
        raise AssertionError(f"Invalid relative path: {rel_path!r}")
    return (REPO_ROOT / rel_path).read_bytes()


def _read_json(rel_path: str) -> dict[str, Any]:
    data = json.loads(_read_bytes(rel_path).decode("utf-8"))
    if type(data) is not dict:
        raise AssertionError(f"Expected JSON object in {rel_path}, got {type(data)!r}")
    return data


def _canonical_git_mode(rel_path: str) -> str:
    path_obj = REPO_ROOT / rel_path
    st = path_obj.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise AssertionError(f"Source {rel_path} must be a regular non-symlink file")
    if st.st_mode & 0o111:
        raise AssertionError(f"Source {rel_path} must not be executable")
    return "100644"


def _assert_typed_equal(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected):
        raise AssertionError(
            f"{label}: type {type(actual).__name__} != {type(expected).__name__}"
        )
    if type(expected) is dict:
        actual_keys = list(actual.keys())
        expected_keys = list(expected.keys())
        if actual_keys != expected_keys:
            raise AssertionError(
                f"{label}: dict keys {actual_keys!r} != {expected_keys!r}"
            )
        for k in expected_keys:
            _assert_typed_equal(actual[k], expected[k], f"{label}.{k}")
        return
    if type(expected) in (list, tuple):
        if len(actual) != len(expected):
            raise AssertionError(
                f"{label}: length {len(actual)} != {len(expected)}"
            )
        for idx, (a_item, e_item) in enumerate(zip(actual, expected)):
            _assert_typed_equal(a_item, e_item, f"{label}[{idx}]")
        return
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def _verify_ast_source_and_selectors(
    rel_path: str,
    raw_bytes: bytes,
    expected_selectors: tuple[str, ...],
) -> None:
    try:
        source_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(f"{rel_path}: invalid UTF-8") from exc
    tree = ast.parse(source_text, filename=rel_path)
    class_nodes: dict[str, ast.ClassDef] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name.endswith("Tests") or alias.name == "TestCase":
                    raise AssertionError(
                        f"{rel_path}: forbidden imported TestCase class {alias.name!r}"
                    )
        elif isinstance(node, ast.ClassDef):
            class_nodes[node.name] = node

    for selector in expected_selectors:
        parts = selector.split("::")
        if len(parts) != 3 or parts[0] != rel_path:
            raise AssertionError(f"Malformed selector {selector!r} for {rel_path!r}")
        _, cls_name, method_name = parts
        cls_node = class_nodes.get(cls_name)
        if cls_node is None:
            raise AssertionError(f"{rel_path}: missing AST class {cls_name!r}")
        method_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        for item in cls_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                method_node = item
                break
        if method_node is None or not method_node.body:
            raise AssertionError(
                f"{rel_path}: missing or empty AST method {cls_name}.{method_name}"
            )
        has_assertion = False
        for sub in ast.walk(method_node):
            if isinstance(sub, ast.Assert):
                has_assertion = True
                break
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and (
                    func.attr.startswith("assert") or func.attr.startswith("_assert")
                ):
                    has_assertion = True
                    break
        if not has_assertion:
            raise AssertionError(
                f"{rel_path}: method {cls_name}.{method_name} lacks assertion evidence"
            )


def _reconstruct_pre_registration_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if list(manifest.keys()) != list(MANIFEST_ROOT_KEYS):
        raise AssertionError(
            f"manifest root keys {list(manifest.keys())!r} != {list(MANIFEST_ROOT_KEYS)!r}"
        )
    evidence_sources = manifest.get("evidence_sources")
    if type(evidence_sources) is not dict:
        raise AssertionError("manifest.evidence_sources must be exact dict")
    ev_keys = list(evidence_sources.keys())
    prefix_len = len(PRE_REGISTRATION_EVIDENCE_KEYS)
    if len(ev_keys) < prefix_len + 1:
        raise AssertionError("manifest.evidence_sources missing required registration entry")
    if tuple(ev_keys[:prefix_len]) != PRE_REGISTRATION_EVIDENCE_KEYS:
        raise AssertionError("manifest.evidence_sources frozen prefix order/keys mismatch")
    if ev_keys[prefix_len - 1] != PRIOR_MANIFEST_LAST_KEY:
        raise AssertionError("manifest prior last key mismatch")
    if ev_keys[prefix_len] != OWN_MANIFEST_KEY:
        raise AssertionError(
            f"Expected {OWN_MANIFEST_KEY!r} immediately after {PRIOR_MANIFEST_LAST_KEY!r}, got {ev_keys[prefix_len]!r}"
        )
    if evidence_sources[OWN_MANIFEST_KEY] != PACKET_REL_PATH:
        raise AssertionError(
            f"manifest {OWN_MANIFEST_KEY} value {evidence_sources[OWN_MANIFEST_KEY]!r} != {PACKET_REL_PATH!r}"
        )
    for extra_key in ev_keys[prefix_len + 1 :]:
        if type(extra_key) is not str or not extra_key:
            raise AssertionError(f"Invalid additive manifest key {extra_key!r}")
        extra_val = evidence_sources[extra_key]
        if type(extra_val) is not str or not extra_val.startswith(
            "docs/refactor/two-server/section-1/inventory/"
        ) or not extra_val.endswith(".json"):
            raise AssertionError(f"Invalid additive manifest target {extra_val!r}")
        if extra_val == PACKET_REL_PATH:
            raise AssertionError("Duplicate target path in manifest.evidence_sources")
        extra_bytes = _read_bytes(extra_val)
        if not extra_bytes:
            raise AssertionError(f"Additive manifest target {extra_val} is empty")

    reconstructed = historical_manifest(manifest)
    reconstructed["evidence_sources"] = {
        k: evidence_sources[k] for k in PRE_REGISTRATION_EVIDENCE_KEYS
    }
    raw_reconstructed = (json.dumps(reconstructed, indent=2) + "\n").encode("utf-8")
    if len(raw_reconstructed) != PRE_REGISTRATION_MANIFEST_UTF8_BYTES:
        raise AssertionError(
            f"Reconstructed pre-registration manifest byte length {len(raw_reconstructed)} != {PRE_REGISTRATION_MANIFEST_UTF8_BYTES}"
        )
    if hashlib.sha256(raw_reconstructed).hexdigest() != PRE_REGISTRATION_MANIFEST_SHA256:
        raise AssertionError("Reconstructed pre-registration manifest sha256 mismatch")
    return reconstructed


def load_post_security_five_registration_controls() -> tuple[
    dict[str, Any], dict[str, Any]
]:
    """Return fresh (packet, manifest) loaded from disk."""
    return (
        copy.deepcopy(_read_json(PACKET_REL_PATH)),
        copy.deepcopy(_read_json(MANIFEST_REL_PATH)),
    )


def assert_post_security_five_registration(
    packet: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    """Validate supplied post-security five-source registration packet and manifest."""
    if type(packet) is not dict:
        raise AssertionError("packet must be exact dict")
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")

    if list(packet.keys()) != list(PACKET_TOP_KEYS):
        raise AssertionError(
            f"packet top keys {list(packet.keys())!r} != {list(PACKET_TOP_KEYS)!r}"
        )

    expected_header: dict[str, Any] = {
        "schema_version": 1,
        "stage": "P1-00b",
        "assignment_id": ASSIGNMENT_ID,
        "assignment_scope_sha256": ASSIGNMENT_SCOPE_SHA256,
        "parent_assignment_id": PARENT_ASSIGNMENT_ID,
        "original_parent_assignment_id": ORIGINAL_PARENT_ASSIGNMENT_ID,
        "task_id": TASK_ID,
        "scenario_key": SCENARIO_KEY,
        "source_head": SOURCE_HEAD,
        "canonical_gap_reference": CANONICAL_GAP_REFERENCE,
    }
    for key, exp_val in expected_header.items():
        _assert_typed_equal(packet[key], exp_val, f"packet.{key}")

    _assert_typed_equal(
        packet["prior_completed_group_provenance"],
        EXPECTED_PRIOR_PROVENANCE,
        "packet.prior_completed_group_provenance",
    )
    _assert_typed_equal(
        packet["authority_source_provenance"],
        EXPECTED_AUTHORITY_PROVENANCE,
        "packet.authority_source_provenance",
    )
    _assert_typed_equal(
        packet["shared_contract_and_exclusions"],
        EXPECTED_SHARED_EXCLUSIONS,
        "packet.shared_contract_and_exclusions",
    )
    _assert_typed_equal(
        packet["raw_membership_delta"],
        [],
        "packet.raw_membership_delta",
    )
    _assert_typed_equal(
        packet["counts"],
        EXPECTED_COUNTS,
        "packet.counts",
    )
    _assert_typed_equal(
        packet["accounting"],
        EXPECTED_ACCOUNTING,
        "packet.accounting",
    )
    _assert_typed_equal(
        packet["strict_canonical_progress"],
        EXPECTED_STRICT_PROGRESS,
        "packet.strict_canonical_progress",
    )
    _assert_typed_equal(
        packet["origin_matrix_unresolved_cells"],
        24,
        "packet.origin_matrix_unresolved_cells",
    )
    _assert_typed_equal(
        packet["canonical_blockers"],
        EXPECTED_BLOCKERS,
        "packet.canonical_blockers",
    )
    _assert_typed_equal(
        packet["coverage_status"],
        "PARTIAL",
        "packet.coverage_status",
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
        EXPECTED_STAGES,
        "packet.stages",
    )

    records = packet["records"]
    if type(records) is not list or len(records) != len(SOURCE_FACTS):
        raise AssertionError("packet.records must be a list of exactly 5 records")

    seen_paths: set[str] = set()
    seen_selectors: list[str] = []
    all_seed_ids: set[str] = set()

    for idx, (rec, fact) in enumerate(zip(records, SOURCE_FACTS)):
        if type(rec) is not dict or list(rec.keys()) != list(RECORD_KEYS):
            raise AssertionError(f"packet.records[{idx}] keys mismatch")
        (
            exp_path,
            exp_sha256,
            exp_bytes,
            exp_commit,
            exp_parent,
            exp_selectors,
            exp_seeds,
        ) = fact
        expected_record: dict[str, Any] = {
            "path": exp_path,
            "sha256": exp_sha256,
            "utf8_bytes": exp_bytes,
            "mode": "100644",
            "last_commit": exp_commit,
            "parent": exp_parent,
            "test_selectors": list(exp_selectors),
            "seed_ids": list(exp_seeds),
        }
        _assert_typed_equal(rec, expected_record, f"packet.records[{idx}]")
        if exp_path in seen_paths:
            raise AssertionError(f"Duplicate record path {exp_path!r}")
        seen_paths.add(exp_path)
        for sel in rec["test_selectors"]:
            if sel in seen_selectors:
                raise AssertionError(f"Duplicate selector {sel!r}")
            seen_selectors.append(sel)
        all_seed_ids.update(rec["seed_ids"])

        raw_source = _read_bytes(exp_path)
        if len(raw_source) != exp_bytes:
            raise AssertionError(
                f"Physical byte length mismatch for {exp_path}: {len(raw_source)} != {exp_bytes}"
            )
        if hashlib.sha256(raw_source).hexdigest() != exp_sha256:
            raise AssertionError(f"Physical sha256 mismatch for {exp_path}")
        if _canonical_git_mode(exp_path) != "100644":
            raise AssertionError(f"Physical mode mismatch for {exp_path}")
        _verify_ast_source_and_selectors(exp_path, raw_source, exp_selectors)

    if len(seen_selectors) != 6:
        raise AssertionError(f"Expected 6 total selectors, got {len(seen_selectors)}")
    if all_seed_ids != {SEED_RESET, SEED_REPLACE, SEED_AUTHORITY}:
        raise AssertionError(f"Unexpected distinct seed set {all_seed_ids!r}")

    associations = packet["selector_associations"]
    if type(associations) is not list or len(associations) != len(ASSOCIATION_FACTS):
        raise AssertionError(
            "packet.selector_associations must be a list of exactly 6 associations"
        )
    assoc_selectors: list[str] = []
    for idx, (assoc, afact) in enumerate(zip(associations, ASSOCIATION_FACTS)):
        if type(assoc) is not dict or list(assoc.keys()) != list(ASSOCIATION_KEYS):
            raise AssertionError(f"packet.selector_associations[{idx}] keys mismatch")
        rec_idx, sel_idx, exp_handler, exp_seed, exp_summary, req_tokens = afact
        if not (0 <= rec_idx < len(records)):
            raise AssertionError(f"Invalid record_index {rec_idx} at association {idx}")
        target_rec = records[rec_idx]
        if not (0 <= sel_idx < len(target_rec["test_selectors"])):
            raise AssertionError(f"Invalid selector_index {sel_idx} at association {idx}")
        expected_assoc: dict[str, Any] = {
            "record_index": rec_idx,
            "selector_index": sel_idx,
            "source_path": target_rec["path"],
            "test_selector": target_rec["test_selectors"][sel_idx],
            "handler": exp_handler,
            "seed_id": exp_seed,
            "contract_summary": exp_summary,
        }
        _assert_typed_equal(
            assoc, expected_assoc, f"packet.selector_associations[{idx}]"
        )
        if target_rec["seed_ids"][sel_idx] != exp_seed:
            raise AssertionError(
                f"Association {idx} seed_id does not align with record seed_ids"
            )
        for tok in req_tokens:
            if tok not in assoc["contract_summary"]:
                raise AssertionError(
                    f"Association {idx} contract_summary missing required token {tok!r}"
                )
        assoc_selectors.append(assoc["test_selector"])

    if assoc_selectors != seen_selectors:
        raise AssertionError(
            "selector_associations order/membership does not match records.test_selectors"
        )

    _reconstruct_pre_registration_manifest(manifest)

    old_mod_bytes = read_pin(OLD_CLOSEOUT_MODULE_REL_PATH, OLD_CLOSEOUT_MODULE_SHA256, OLD_CLOSEOUT_MODULE_UTF8_BYTES, root=REPO_ROOT)
    if len(old_mod_bytes) != OLD_CLOSEOUT_MODULE_UTF8_BYTES:
        raise AssertionError("Frozen BL module byte length mismatch")
    if hashlib.sha256(old_mod_bytes).hexdigest() != OLD_CLOSEOUT_MODULE_SHA256:
        raise AssertionError("Frozen BL module sha256 mismatch")

    old_pkt_bytes = _read_bytes(OLD_CLOSEOUT_PACKET_REL_PATH)
    if len(old_pkt_bytes) != OLD_CLOSEOUT_PACKET_UTF8_BYTES:
        raise AssertionError("Frozen BL packet byte length mismatch")
    if hashlib.sha256(old_pkt_bytes).hexdigest() != OLD_CLOSEOUT_PACKET_SHA256:
        raise AssertionError("Frozen BL packet sha256 mismatch")

    old_closeout_packet, _, ledger_bytes = (
        group_closeout.load_current_group_ledger_closeout_controls()
    )
    raw_old_loaded = (json.dumps(old_closeout_packet, indent=2) + "\n").encode("utf-8")
    if len(raw_old_loaded) != OLD_CLOSEOUT_PACKET_UTF8_BYTES:
        raise AssertionError("Loaded BL packet byte length mismatch")
    if hashlib.sha256(raw_old_loaded).hexdigest() != OLD_CLOSEOUT_PACKET_SHA256:
        raise AssertionError("Loaded BL packet sha256 mismatch")

    group_closeout.assert_current_group_ledger_closeout(
        old_closeout_packet,
        manifest,
        ledger_bytes,
    )


def _type_order_fingerprint(value: Any) -> Any:
    t = type(value)
    if t is dict:
        return (
            "dict",
            tuple((_type_order_fingerprint(k), _type_order_fingerprint(v)) for k, v in value.items()),
        )
    if t is list:
        return ("list", tuple(_type_order_fingerprint(v) for v in value))
    if t is tuple:
        return ("tuple", tuple(_type_order_fingerprint(v) for v in value))
    return (t.__name__, repr(value))


class PostSecurityFiveRegistrationTests(unittest.TestCase):
    def test_all_five_sources_and_six_scenarios_registered_with_ledger_pending(
        self,
    ) -> None:
        packet, manifest = load_post_security_five_registration_controls()
        assert_post_security_five_registration(packet, manifest)

        disk_packet_bytes = _read_bytes(PACKET_REL_PATH)
        serialized_packet = (json.dumps(packet, indent=2) + "\n").encode("utf-8")
        self.assertEqual(disk_packet_bytes, serialized_packet)

        disk_manifest_bytes = _read_bytes(MANIFEST_REL_PATH)
        serialized_manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        self.assertEqual(disk_manifest_bytes, serialized_manifest)

        self.assertIs(packet["accounting"]["registration_complete"], True)
        self.assertIs(packet["accounting"]["group_reconciliation_complete"], False)
        self.assertIs(
            packet["accounting"]["canonical_manifest_and_ledger_integration_complete"],
            False,
        )
        self.assertEqual(packet["accounting"]["ledger_integration_status"], "PENDING")
        self.assertEqual(
            packet["accounting"]["current_group_status_on_exact_acceptance"],
            "PHYSICAL_REGISTRATION_VERIFIED_ONLY",
        )
        self.assertIs(packet["closures_authorized"], False)

        canary_manifest = copy.deepcopy(manifest)
        canary_manifest["evidence_sources"][
            "business_768_future_surface_api_canary_path"
        ] = FUTURE_CANARY_REL_PATH
        assert_post_security_five_registration(packet, canary_manifest)

        orig_bl_loader = group_closeout.load_current_group_ledger_closeout_controls
        old_pkt_orig, old_man_orig, orig_ledger_bytes = orig_bl_loader()
        appended_suffix = (
            b"\n### 2026-11-03 - Future Ledger Suffix Compatibility Witness\n"
            b"- Bounded in-memory suffix compatibility check after registration.\n"
        )
        try:
            def _patched_bl_loader() -> tuple[dict[str, Any], dict[str, Any], bytes]:
                return (
                    copy.deepcopy(old_pkt_orig),
                    copy.deepcopy(old_man_orig),
                    orig_ledger_bytes + appended_suffix,
                )

            group_closeout.load_current_group_ledger_closeout_controls = _patched_bl_loader
            assert_post_security_five_registration(packet, manifest)
        finally:
            group_closeout.load_current_group_ledger_closeout_controls = orig_bl_loader
        self.assertIs(
            group_closeout.load_current_group_ledger_closeout_controls,
            orig_bl_loader,
        )
        assert_post_security_five_registration(packet, manifest)

    def test_rejects_supplied_record_source_scenario_and_closure_drift(self) -> None:
        healthy_pair = load_post_security_five_registration_controls()
        healthy_fp = _type_order_fingerprint(healthy_pair)

        def _mutate_missing_record_field(p: dict[str, Any], m: dict[str, Any]) -> None:
            del p["records"][0]["parent"]

        def _mutate_extra_record_field(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["records"][0]["extra_field"] = "unexpected"

        def _mutate_reordered_record_field(p: dict[str, Any], m: dict[str, Any]) -> None:
            rec0 = p["records"][0]
            vals = {k: rec0[k] for k in RECORD_KEYS}
            rec0.clear()
            for k in ("sha256", "path", *RECORD_KEYS[2:]):
                rec0[k] = vals[k]

        def _mutate_float_byte_length(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["records"][1]["utf8_bytes"] = float(p["records"][1]["utf8_bytes"])

        def _mutate_bool_for_int(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["accounting"]["registration_remaining_sources"] = False

        def _mutate_wrong_source_hash(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["records"][2]["sha256"] = "0" * 64

        def _mutate_wrong_source_parent(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["records"][4]["parent"] = "f" * 40

        def _mutate_wrong_selector_association(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["selector_associations"][0]["handler"] = HANDLER_REPLACE

        def _mutate_nonexistent_selector(p: dict[str, Any], m: dict[str, Any]) -> None:
            bad_sel = (
                "tests/test_p1_00b_characterization_internal_authority_body_failure.py::"
                "InternalAuthorityBodyFailureCharacterizationTests::test_nonexistent_scenario"
            )
            p["records"][4]["test_selectors"][0] = bad_sel
            p["selector_associations"][5]["test_selector"] = bad_sel

        def _mutate_duplicate_selector_association(
            p: dict[str, Any], m: dict[str, Any]
        ) -> None:
            p["selector_associations"][1] = copy.deepcopy(p["selector_associations"][0])

        def _mutate_reordered_selector_association(
            p: dict[str, Any], m: dict[str, Any]
        ) -> None:
            a0 = copy.deepcopy(p["selector_associations"][0])
            a1 = copy.deepcopy(p["selector_associations"][1])
            p["selector_associations"][0] = a1
            p["selector_associations"][1] = a0

        def _mutate_missing_manifest_mapping(p: dict[str, Any], m: dict[str, Any]) -> None:
            del m["evidence_sources"][OWN_MANIFEST_KEY]

        def _mutate_incorrect_manifest_mapping(p: dict[str, Any], m: dict[str, Any]) -> None:
            m["evidence_sources"][OWN_MANIFEST_KEY] = FUTURE_CANARY_REL_PATH

        def _mutate_manifest_root_drift(p: dict[str, Any], m: dict[str, Any]) -> None:
            m["coverage_status"] = "COMPLETE"

        def _mutate_manifest_prefix_drift(p: dict[str, Any], m: dict[str, Any]) -> None:
            ev = m["evidence_sources"]
            items = list(ev.items())
            ev.clear()
            ev[items[1][0]] = items[1][1]
            ev[items[0][0]] = items[0][1]
            for k, v in items[2:]:
                ev[k] = v

        def _mutate_record_omission(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["records"].pop()

        def _mutate_count_drift(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["counts"]["evidenced_seed_count"] = 399

        def _mutate_premature_group_closure(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["accounting"]["group_reconciliation_complete"] = True

        def _mutate_premature_ledger_closure(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["accounting"]["ledger_integration_status"] = "COMPLETE"

        def _mutate_premature_seed_promotion(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["seed_acceptance_status"] = "ACCEPTED"

        def _mutate_premature_high_blocker_closure(
            p: dict[str, Any], m: dict[str, Any]
        ) -> None:
            p["canonical_blockers"] = ["P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN"]

        def _mutate_premature_origin_closure(p: dict[str, Any], m: dict[str, Any]) -> None:
            p["origin_matrix_unresolved_cells"] = 23

        supplied_cases: tuple[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]], ...] = (
            ("missing_record_field", _mutate_missing_record_field),
            ("extra_record_field", _mutate_extra_record_field),
            ("reordered_record_field", _mutate_reordered_record_field),
            ("float_byte_length", _mutate_float_byte_length),
            ("bool_for_int", _mutate_bool_for_int),
            ("wrong_source_hash", _mutate_wrong_source_hash),
            ("wrong_source_parent", _mutate_wrong_source_parent),
            ("wrong_selector_association", _mutate_wrong_selector_association),
            ("nonexistent_selector", _mutate_nonexistent_selector),
            ("duplicate_selector_association", _mutate_duplicate_selector_association),
            ("reordered_selector_association", _mutate_reordered_selector_association),
            ("missing_manifest_mapping", _mutate_missing_manifest_mapping),
            ("incorrect_manifest_mapping", _mutate_incorrect_manifest_mapping),
            ("manifest_root_drift", _mutate_manifest_root_drift),
            ("manifest_prefix_drift", _mutate_manifest_prefix_drift),
            ("record_omission", _mutate_record_omission),
            ("count_drift", _mutate_count_drift),
            ("premature_group_closure", _mutate_premature_group_closure),
            ("premature_ledger_closure", _mutate_premature_ledger_closure),
            ("premature_seed_promotion", _mutate_premature_seed_promotion),
            ("premature_high_blocker_closure", _mutate_premature_high_blocker_closure),
            ("premature_origin_closure", _mutate_premature_origin_closure),
        )

        for label, mutator in supplied_cases:
            with self.subTest(case=label):
                fresh_p, fresh_m = load_post_security_five_registration_controls()
                self.assertEqual(
                    _type_order_fingerprint((fresh_p, fresh_m)),
                    healthy_fp,
                )
                assert_post_security_five_registration(fresh_p, fresh_m)

                bad_p = copy.deepcopy(fresh_p)
                bad_m = copy.deepcopy(fresh_m)
                mutator(bad_p, bad_m)
                bad_fp = _type_order_fingerprint((bad_p, bad_m))
                self.assertNotEqual(bad_fp, healthy_fp, msg=f"Vacuous mutation for {label}")
                with self.assertRaises(AssertionError, msg=f"Expected rejection for {label}"):
                    assert_post_security_five_registration(bad_p, bad_m)

                assert_post_security_five_registration(fresh_p, fresh_m)

        with self.subTest(case="same_length_physical_source_corruption"):
            fresh_p, fresh_m = load_post_security_five_registration_controls()
            self.assertEqual(_type_order_fingerprint((fresh_p, fresh_m)), healthy_fp)
            assert_post_security_five_registration(fresh_p, fresh_m)

            target_rel = SOURCE_FACTS[3][0]
            orig_read_bytes = _read_bytes
            orig_target_bytes = orig_read_bytes(target_rel)
            corrupted_bytes = (
                (b"#" if orig_target_bytes[:1] != b"#" else b" ") + orig_target_bytes[1:]
            )
            self.assertEqual(len(corrupted_bytes), len(orig_target_bytes))
            self.assertNotEqual(corrupted_bytes, orig_target_bytes)

            try:
                def _corrupting_read_bytes(rel_path: str) -> bytes:
                    data = orig_read_bytes(rel_path)
                    if rel_path == target_rel:
                        return corrupted_bytes
                    return data

                globals()["_read_bytes"] = _corrupting_read_bytes
                self.assertIsNot(globals()["_read_bytes"], orig_read_bytes)
                with self.assertRaises(AssertionError):
                    assert_post_security_five_registration(fresh_p, fresh_m)
            finally:
                globals()["_read_bytes"] = orig_read_bytes

            self.assertIs(globals()["_read_bytes"], orig_read_bytes)
            self.assertEqual(_read_bytes(target_rel), orig_target_bytes)
            assert_post_security_five_registration(fresh_p, fresh_m)


if __name__ == "__main__":
    unittest.main()
