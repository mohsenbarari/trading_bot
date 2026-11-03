"""Current session body failure registration characterization and reusable public oracle.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-COMPLETION-20261102-001
Parent assignment: WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001
Original parent: WA-P1-00B-FREEZE-20260904-001
Task: p1-00b-current-session-body-registration-a-20261102-003
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import copy
import hashlib
import json
import pathlib
import stat
from typing import Any
import unittest

import tests.test_p1_00b_characterization_authority_registration_after_security as primary

ROOT = pathlib.Path(__file__).resolve().parents[1]

BODY_PACKET_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/"
    "current-session-body-registration.json"
)
MANIFEST_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
BODY_MANIFEST_KEY = "business_768_current_session_body_registration_path"
AUTHORITY_MANIFEST_KEY = primary.AUTHORITY_MANIFEST_KEY
AUTHORITY_PACKET_REL_PATH = primary.PACKET_REL_PATH

TASK_ID = "p1-00b-current-session-body-registration-a-20261102-003"
ASSIGNMENT_ID = "WA-P1-00B-COMPLETION-20261102-001"
ASSIGNMENT_SCOPE_SHA256 = (
    "6b6eff78bf62c60b839b42fb3cc9bd286fb096b3f8a58d41a59cf8f218d13257"
)
PARENT_ASSIGNMENT_ID = "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001"
ORIGINAL_PARENT_ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
SCENARIO_KEY = (
    "P1-00B-CURRENT-SESSION-REGISTRATION-A-V3.qualified-185-minimal-review-context"
)
SOURCE_HEAD = "8b073836d57b35e9758ac6fce9c63e87831f4865"

PRE_BODY_MANIFEST_UTF8_BYTES = 4578
PRE_BODY_MANIFEST_SHA256 = (
    "78fb57df9bde1afbc336b02f4026dcc418032efa39d7fdc050c5d057c94f023c"
)

BODY_SOURCE_PATH = (
    "tests/test_p1_00b_characterization_internal_session_body_failures_batch.py"
)
BODY_SOURCE_UTF8_BYTES = 7060
BODY_SOURCE_SHA256 = (
    "b5f2772f283424f94c0f8436b8575f58f7ec65a2e57500ea4422bc9c9785c4e9"
)
BODY_SOURCE_MODE = "100644"
BODY_SOURCE_LAST_COMMIT = "777ed75419ed7083d875c262da2beb8103017d03"
BODY_SOURCE_PARENT = "e5c08c61895961bce399da9d8c69f561d7538ea1"

RESET_CANONICAL_SYMBOL = "api/routers/sessions.py:internal_reset_user_sessions"
REPLACE_CANONICAL_SYMBOL = "api/routers/sessions.py:internal_replace_user_sessions"
RESET_SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562"
REPLACE_SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593"
RESET_SCENARIO = "reset_body_failure_before_headers_and_db"
REPLACE_SCENARIO = "replace_body_failure_before_headers_and_db"
RESET_SELECTOR = (
    "tests/test_p1_00b_characterization_internal_session_body_failures_batch.py::"
    "InternalSessionBodyFailuresCharacterizationTests::"
    "test_reset_body_failure_stops_before_headers_and_db"
)
REPLACE_SELECTOR = (
    "tests/test_p1_00b_characterization_internal_session_body_failures_batch.py::"
    "InternalSessionBodyFailuresCharacterizationTests::"
    "test_replace_body_failure_stops_before_headers_and_db"
)

EXPECTED_PRIOR_PACKET_REFERENCES = [
    {
        "manifest_key": "business_768_live786_reset_execute_singleton_path",
        "path": (
            "docs/refactor/two-server/section-1/inventory/"
            "recent-characterization-business-768-live786-reset-execute-singleton.json"
        ),
        "utf8_bytes": 9510,
        "sha256": "4ef4ffe40f33dfe4328573bdca380b220e33306e1bee5f18d5baf54aef7170a2",
        "accepted_commit": "226e3b111a393bb834c4f8a91ac473f2d766f915",
    },
    {
        "manifest_key": "business_768_live786_replace_execute_singleton_path",
        "path": (
            "docs/refactor/two-server/section-1/inventory/"
            "recent-characterization-business-768-live786-replace-execute-singleton.json"
        ),
        "utf8_bytes": 9738,
        "sha256": "cf251e42b72c41490e205499dbc269704d75ba422dbf730ef1027d29101d579c",
        "accepted_commit": "c9e9b30cd919472b1e253bc9b6da53da644171a8",
    },
    {
        "manifest_key": "business_768_live786_verifier_only_path",
        "path": (
            "docs/refactor/two-server/section-1/inventory/"
            "recent-characterization-business-768-live786-verifier-only.json"
        ),
        "utf8_bytes": 11436,
        "sha256": "09c03b798d7c268a987874c1ebe3e834ed534edc97eade4be9b68e097d27f960",
        "accepted_commit": "bfad4a12866719f255f003d15625c87ccd8da1b5",
    },
    {
        "manifest_key": "business_768_live786_authority_registration_after_security_path",
        "path": (
            "docs/refactor/two-server/section-1/inventory/"
            "recent-characterization-business-768-live786-authority-registration-security.json"
        ),
        "utf8_bytes": 12255,
        "sha256": "676426367cb4fd92ace6cd9f2bd04c90fb70bc8f24e357c3fe4293004f56c113",
        "accepted_commit": "041c5f5fdc06a7389238461dc29f3657ab19d8cd",
    },
]

EXPECTED_BODY_RECORD = {
    "path": BODY_SOURCE_PATH,
    "sha256": BODY_SOURCE_SHA256,
    "utf8_bytes": BODY_SOURCE_UTF8_BYTES,
    "mode": BODY_SOURCE_MODE,
    "last_commit": BODY_SOURCE_LAST_COMMIT,
    "parent": BODY_SOURCE_PARENT,
    "canonical_symbols": [RESET_CANONICAL_SYMBOL, REPLACE_CANONICAL_SYMBOL],
    "seed_ids": [RESET_SEED_ID, REPLACE_SEED_ID],
    "scenarios": [RESET_SCENARIO, REPLACE_SCENARIO],
    "test_selectors": [RESET_SELECTOR, REPLACE_SELECTOR],
    "historical_provenance": {
        "worker_run_id": "p1-00b-799b-c001-session-body-worker-1",
        "reviewer_run_id": "p1-00b-799b-c001-session-body-reviewer-1",
        "claims_new_native_or_provider_execution": False,
        "claims_future_acceptance": False,
    },
    "characterized_contract": {
        "entrypoints": [RESET_CANONICAL_SYMBOL, REPLACE_CANONICAL_SYMBOL],
        "request_body": {
            "call_count": 1,
            "await_count": 1,
            "positional_args": [],
            "keyword_args": {},
            "exception_type": "RuntimeError",
            "identical_exception_propagated": True,
            "escapes_before": [
                "request.headers_property",
                "request.headers.get",
                "verify_session_authority_signature",
                "select",
                "reset_user_session_state",
                "force_clear_sessions",
            ],
        },
        "scenario_bindings": [
            {
                "scenario": RESET_SCENARIO,
                "user_id": 9101,
                "mobile_number": "09120009101",
                "source_server": "synthetic-peer-reset-body-failure",
                "error_message": "synthetic-reset-request-body-read-failure",
            },
            {
                "scenario": REPLACE_SCENARIO,
                "user_id": 9202,
                "mobile_number": "09120009202",
                "source_server": "synthetic-peer-replace-body-failure",
                "error_message": "synthetic-replace-request-body-read-failure",
            },
        ],
        "zero_side_effect_and_fence_invariants": {
            "headers_property_access_count": 0,
            "poison_headers_access_count": 0,
            "verify_session_authority_signature_calls": 0,
            "select_calls": 0,
            "reset_user_session_state_calls": 0,
            "reset_user_session_state_awaits": 0,
            "force_clear_sessions_calls": 0,
            "force_clear_sessions_awaits": 0,
            "db_argument": "inert object()",
            "handler_identities_unchanged": True,
            "network_denied_connects_delta": 0,
            "live_redis_constructs_delta": 0,
        },
    },
    "exclusions": {
        "body_read_success_and_malformed_body_parsing": True,
        "signature_and_authentication_validation": True,
        "http_routing_and_depends_wiring": True,
        "sql_execution_and_scalar_result_consumption": True,
        "missing_user_404_and_mobile_mismatch_409": True,
        "successful_session_reset_and_revocation": True,
        "persistence_retry_reload_and_concurrency": True,
        "origin_matrix_interactions": True,
        "full_seed_acceptance": True,
        "high_blocker_closure": True,
        "stage_acceptance": True,
    },
    "credits": {
        "added_behavior_units": 0,
        "added_distinct_seeds": 0,
        "accepted_full_seeds": 0,
        "raw_registration_delta": 0,
        "stage_or_origin_or_optimization_credit": 0,
    },
}

EXPECTED_PROMOTION_COUNTS = {
    "expected": 465,
    "baseline": 135,
    "reconciled_new": 263,
    "evidenced": 398,
    "remaining": 67,
}

EXPECTED_COUNTS_AFTER_UNION = {
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}

EXPECTED_PROMOTION_ASSESSMENT = {
    "promoted_seed_ids": [],
    "already_registered_seed_ids": [RESET_SEED_ID, REPLACE_SEED_ID],
    "delta_distinct_seed_count": 0,
    "removed_seed_ids": [],
    "duplicate_new_seed_ids": [],
    "before_manifest_sha256": PRE_BODY_MANIFEST_SHA256,
    "before_manifest_utf8_bytes": PRE_BODY_MANIFEST_UTF8_BYTES,
    "canonical_before": EXPECTED_PROMOTION_COUNTS,
    "canonical_after_on_exact_acceptance": EXPECTED_PROMOTION_COUNTS,
    "added_behavior_units": 0,
    "added_distinct_seeds": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
    "full_seed_acceptance": "NOT_ASSESSED",
}

EXPECTED_ACCOUNTING = {
    "pending_five": 5,
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "total_handler_scenarios": 7,
    "prior_inventory_registered_units": 4,
    "prior_inventory_registered_scenarios": 5,
    "reconciled_units_in_this_packet_on_exact_acceptance": 1,
    "reconciled_scenarios_in_this_packet_on_exact_acceptance": 2,
    "cumulative_reconciled_units_on_exact_acceptance": 5,
    "cumulative_reconciled_scenarios_on_exact_acceptance": 7,
    "remaining_unreconciled_units_on_exact_acceptance": 0,
    "remaining_unreconciled_scenarios_on_exact_acceptance": 0,
    "unreconciled_units_after_body_on_exact_acceptance": [],
    "group_reconciliation_complete": False,
    "full_group_union_complete": False,
    "canonical_manifest_and_ledger_integration_complete": False,
    "ledger_integration_status": "PENDING",
    "outcome_b_full_group_closeout_required": True,
    "no_sixth_behavior_accepted_before_complete_reconciliation": True,
    "added_behavior_units": 0,
    "added_distinct_seeds": 0,
    "accepted_full_seeds": 0,
    "raw_registration_delta": 0,
}

EXPECTED_BLOCKERS = [
    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
]

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
    "prior_packet_references",
    "raw_membership_delta",
    "counts_after_union",
    "promotion_assessment",
    "accounting",
    "strict_canonical_progress",
    "origin_matrix_unresolved_cells",
    "canonical_blockers",
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

HANDLER_KEYS = primary.HANDLER_KEYS


def _read_bytes(rel_path: str) -> bytes:
    return (ROOT / rel_path).read_bytes()


def _read_json(rel_path: str) -> dict[str, Any]:
    return json.loads(_read_bytes(rel_path).decode("utf-8"))


def _typed_fingerprint(value: Any) -> Any:
    if isinstance(value, dict):
        return (
            "dict",
            tuple((_typed_fingerprint(k), _typed_fingerprint(v)) for k, v in value.items()),
        )
    if isinstance(value, list):
        return ("list", tuple(_typed_fingerprint(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_typed_fingerprint(item) for item in value))
    return (type(value).__name__, value)


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


def _inspect_body_source_ast(source_path: str) -> dict[str, Any]:
    full_path = ROOT / source_path
    if not full_path.exists():
        raise AssertionError(f"missing body source {source_path}")
    st_mode = full_path.stat().st_mode
    if not stat.S_ISREG(st_mode) or (st_mode & 0o111) != 0:
        raise AssertionError(f"body source {source_path} must be regular non-executable 100644")
    raw = full_path.read_bytes()
    tree = ast.parse(raw.decode("utf-8"), filename=source_path)

    imported_session_symbols: list[str] = []
    selectors: list[str] = []
    scenario_calls: dict[str, dict[str, Any]] = {}

    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "api.routers.sessions":
            for alias in node.names:
                if alias.name in ("internal_reset_user_sessions", "internal_replace_user_sessions"):
                    imported_session_symbols.append(f"api/routers/sessions.py:{alias.name}")
        if isinstance(node, ast.ClassDef) and node.name == "InternalSessionBodyFailuresCharacterizationTests":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name.startswith("test_"):
                    selector = f"{source_path}::{node.name}::{item.name}"
                    selectors.append(selector)
                    for stmt in item.body:
                        if (
                            isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Await)
                            and isinstance(stmt.value.value, ast.Call)
                        ):
                            call = stmt.value.value
                            if (
                                isinstance(call.func, ast.Attribute)
                                and call.func.attr == "_assert_body_failure_stops_before_headers_and_db"
                                and len(call.args) == 1
                                and isinstance(call.args[0], ast.Name)
                            ):
                                kw = {
                                    k.arg: ast.literal_eval(k.value)
                                    for k in call.keywords
                                    if k.arg is not None
                                }
                                scenario_calls[selector] = {
                                    "handler_symbol": f"api/routers/sessions.py:{call.args[0].id}",
                                    "kwargs": kw,
                                }

    return {
        "utf8_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "imported_session_symbols": imported_session_symbols,
        "selectors": selectors,
        "scenario_calls": scenario_calls,
    }


def _expected_handlers_from_record(record: dict[str, Any]) -> list[dict[str, str]]:
    scenarios = record.get("scenarios")
    selectors = record.get("test_selectors")
    symbols = record.get("canonical_symbols")
    seeds = record.get("seed_ids")
    if (
        type(scenarios) is not list
        or type(selectors) is not list
        or type(symbols) is not list
        or type(seeds) is not list
        or not (len(scenarios) == len(selectors) == len(symbols) == len(seeds) == 2)
    ):
        raise AssertionError("record must define exact two-item lists for scenarios, selectors, symbols, and seeds")
    rows: list[dict[str, str]] = []
    for idx in range(2):
        rows.append(
            {
                "scenario": scenarios[idx],
                "scenario_key": scenarios[idx],
                "selector": selectors[idx],
                "canonical_symbol": symbols[idx],
                "seed_id": seeds[idx],
                "source_path": record["path"],
                "source_file_sha256": record["sha256"],
                "source_commit": record["last_commit"],
            }
        )
    return rows


def _reconstruct_pre_body_manifest(manifest: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    if type(manifest) is not dict:
        raise AssertionError("manifest must be a dict")
    if list(manifest.keys()) != list(MANIFEST_TOP_KEYS):
        raise AssertionError(
            f"manifest top keys {list(manifest.keys())!r} != {list(MANIFEST_TOP_KEYS)!r}"
        )
    evidence_sources = manifest.get("evidence_sources")
    if type(evidence_sources) is not dict:
        raise AssertionError("manifest.evidence_sources must be a dict")
    for key, val in evidence_sources.items():
        if type(key) is not str or type(val) is not str or not key or not val:
            raise AssertionError("manifest.evidence_sources entries must be non-empty str")

    ordered_items = list(evidence_sources.items())
    keys = [k for k, _ in ordered_items]
    if AUTHORITY_MANIFEST_KEY not in keys:
        raise AssertionError("authority registration key missing from manifest.evidence_sources")
    if BODY_MANIFEST_KEY not in keys:
        raise AssertionError("body registration key missing from manifest.evidence_sources")

    authority_index = keys.index(AUTHORITY_MANIFEST_KEY)
    body_index = keys.index(BODY_MANIFEST_KEY)
    if body_index != authority_index + 1:
        raise AssertionError(
            f"body mapping index {body_index} != authority_index + 1 ({authority_index + 1})"
        )
    if evidence_sources[AUTHORITY_MANIFEST_KEY] != AUTHORITY_PACKET_REL_PATH:
        raise AssertionError("authority registration mapping path mismatch")
    if evidence_sources[BODY_MANIFEST_KEY] != BODY_PACKET_REL_PATH:
        raise AssertionError("body registration mapping path mismatch")

    prefix_sources = dict(ordered_items[: authority_index + 1])
    if BODY_PACKET_REL_PATH in prefix_sources.values():
        raise AssertionError("body packet path must not appear in pre-body prefix")

    reconstructed = copy.deepcopy(manifest)
    reconstructed["evidence_sources"] = prefix_sources
    serialized = (json.dumps(reconstructed, indent=2) + "\n").encode("utf-8")
    if len(serialized) != PRE_BODY_MANIFEST_UTF8_BYTES:
        raise AssertionError(
            f"pre-body manifest UTF-8 bytes {len(serialized)} != {PRE_BODY_MANIFEST_UTF8_BYTES}"
        )
    actual_sha = hashlib.sha256(serialized).hexdigest()
    if actual_sha != PRE_BODY_MANIFEST_SHA256:
        raise AssertionError(
            f"pre-body manifest SHA256 {actual_sha} != {PRE_BODY_MANIFEST_SHA256}"
        )
    return reconstructed, serialized


def load_body_registration_controls() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return fresh (fresh_body_packet, fresh_manifest) from disk."""
    return copy.deepcopy(_read_json(BODY_PACKET_REL_PATH)), copy.deepcopy(_read_json(MANIFEST_REL_PATH))


def assert_body_registration(packet: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Validate supplied body registration packet and manifest against physical and authority oracles."""
    if type(packet) is not dict:
        raise AssertionError("packet must be exact dict")
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if list(packet.keys()) != list(PACKET_TOP_KEYS):
        raise AssertionError(
            f"packet top keys {list(packet.keys())!r} != {list(PACKET_TOP_KEYS)!r}"
        )

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

    records = packet.get("records")
    if type(records) is not list or len(records) != 1:
        raise AssertionError("packet.records must be a singleton list")
    record = records[0]
    _assert_typed_equal(record, EXPECTED_BODY_RECORD, "packet.records[0]")

    handlers = packet.get("registered_handlers")
    if type(handlers) is not list or len(handlers) != 2:
        raise AssertionError("packet.registered_handlers must be a two-row list")
    expected_handlers = _expected_handlers_from_record(record)
    for idx, row in enumerate(handlers):
        if type(row) is not dict or list(row.keys()) != list(HANDLER_KEYS):
            raise AssertionError(f"registered_handlers[{idx}] must have exact ordered 8 keys {HANDLER_KEYS!r}")
    _assert_typed_equal(handlers, expected_handlers, "packet.registered_handlers")

    ast_info = _inspect_body_source_ast(record["path"])
    if ast_info["utf8_bytes"] != record["utf8_bytes"]:
        raise AssertionError("physical body source utf8_bytes mismatch")
    if ast_info["sha256"] != record["sha256"]:
        raise AssertionError("physical body source sha256 mismatch")
    if ast_info["selectors"] != record["test_selectors"]:
        raise AssertionError("AST-resolved selectors do not match record.test_selectors")
    if sorted(ast_info["imported_session_symbols"]) != sorted(record["canonical_symbols"]):
        raise AssertionError("AST-resolved canonical symbols do not match record.canonical_symbols")
    for idx, binding in enumerate(record["characterized_contract"]["scenario_bindings"]):
        selector = record["test_selectors"][idx]
        symbol = record["canonical_symbols"][idx]
        if binding["scenario"] != record["scenarios"][idx] or handlers[idx]["scenario"] != binding["scenario"]:
            raise AssertionError(f"scenario binding mismatch at {idx}")
        if (
            handlers[idx]["selector"] != selector
            or handlers[idx]["canonical_symbol"] != symbol
            or handlers[idx]["seed_id"] != record["seed_ids"][idx]
        ):
            raise AssertionError(f"handler association mismatch at {idx}")
        selector_call = ast_info["scenario_calls"].get(selector)
        if selector_call is None:
            raise AssertionError(f"missing AST scenario call for {selector}")
        if selector_call["handler_symbol"] != symbol:
            raise AssertionError(f"AST handler symbol mismatch for {selector}")
        expected_kw = {
            "user_id": binding["user_id"],
            "mobile_number": binding["mobile_number"],
            "source_server": binding["source_server"],
            "error_message": binding["error_message"],
        }
        _assert_typed_equal(selector_call["kwargs"], expected_kw, f"ast_kwargs[{binding['scenario']}]")

    _assert_typed_equal(
        packet["prior_packet_references"],
        EXPECTED_PRIOR_PACKET_REFERENCES,
        "packet.prior_packet_references",
    )
    for ref in packet["prior_packet_references"]:
        raw_prior = _read_bytes(ref["path"])
        if len(raw_prior) != ref["utf8_bytes"]:
            raise AssertionError(f"prior packet size mismatch for {ref['path']}")
        if hashlib.sha256(raw_prior).hexdigest() != ref["sha256"]:
            raise AssertionError(f"prior packet sha256 mismatch for {ref['path']}")
        prior_doc = json.loads(raw_prior.decode("utf-8"))
        prior_rec = prior_doc["records"][0]
        source_bytes = _read_bytes(prior_rec["path"])
        if len(source_bytes) != prior_rec["utf8_bytes"]:
            raise AssertionError(f"prior source size mismatch for {prior_rec['path']}")
        if hashlib.sha256(source_bytes).hexdigest() != prior_rec["sha256"]:
            raise AssertionError(f"prior source sha256 mismatch for {prior_rec['path']}")

    _assert_typed_equal(packet["raw_membership_delta"], [], "packet.raw_membership_delta")
    _assert_typed_equal(
        packet["counts_after_union"],
        EXPECTED_COUNTS_AFTER_UNION,
        "packet.counts_after_union",
    )
    _assert_typed_equal(
        packet["promotion_assessment"],
        EXPECTED_PROMOTION_ASSESSMENT,
        "packet.promotion_assessment",
    )
    _assert_typed_equal(packet["accounting"], EXPECTED_ACCOUNTING, "packet.accounting")
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
        EXPECTED_BLOCKERS,
        "packet.canonical_blockers",
    )
    _assert_typed_equal(
        packet["seed_acceptance_status"],
        "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        "packet.seed_acceptance_status",
    )
    _assert_typed_equal(packet["closures_authorized"], False, "packet.closures_authorized")
    _assert_typed_equal(
        packet["stages"],
        {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        "packet.stages",
    )

    reconstructed_pre_body, _ = _reconstruct_pre_body_manifest(manifest)
    for prior_ref in packet["prior_packet_references"]:
        m_key = prior_ref["manifest_key"]
        if manifest["evidence_sources"].get(m_key) != prior_ref["path"]:
            raise AssertionError(f"manifest mapping mismatch for {m_key}")

    authority_packet, _ = primary.load_physical_controls()
    authority_unreconciled = authority_packet["accounting"][
        "unreconciled_units_after_authority_on_exact_acceptance"
    ]
    expected_body_unreconciled = [
        {
            "path": record["path"],
            "sha256": record["sha256"],
            "source_commit": record["last_commit"],
            "handler_scenarios": 2,
        }
    ]
    _assert_typed_equal(
        authority_unreconciled,
        expected_body_unreconciled,
        "authority_unreconciled_body_unit",
    )

    primary.assert_authority_registration(authority_packet, reconstructed_pre_body)
    primary.assert_authority_registration(authority_packet, manifest)


class CurrentSessionBodyRegistrationTests(unittest.TestCase):
    def test_actual_body_registration_preserves_canonical_membership(self) -> None:
        body_source_before = _read_bytes(BODY_SOURCE_PATH)
        prior_snapshots = {
            ref["path"]: _read_bytes(ref["path"]) for ref in EXPECTED_PRIOR_PACKET_REFERENCES
        }

        packet_a, manifest_a = load_body_registration_controls()
        packet_b, manifest_b = load_body_registration_controls()
        self.assertIsNot(packet_a, packet_b)
        self.assertIsNot(manifest_a, manifest_b)
        self.assertEqual(_typed_fingerprint(packet_a), _typed_fingerprint(packet_b))
        self.assertEqual(_typed_fingerprint(manifest_a), _typed_fingerprint(manifest_b))

        fp_packet_before = _typed_fingerprint(packet_a)
        fp_manifest_before = _typed_fingerprint(manifest_a)
        assert_body_registration(packet_a, manifest_a)
        self.assertEqual(_typed_fingerprint(packet_a), fp_packet_before)
        self.assertEqual(_typed_fingerprint(manifest_a), fp_manifest_before)

        reconstructed_pre_body, serialized_pre_body = _reconstruct_pre_body_manifest(manifest_a)
        self.assertEqual(len(serialized_pre_body), PRE_BODY_MANIFEST_UTF8_BYTES)
        self.assertEqual(
            hashlib.sha256(serialized_pre_body).hexdigest(),
            PRE_BODY_MANIFEST_SHA256,
        )
        self.assertNotIn(BODY_MANIFEST_KEY, reconstructed_pre_body["evidence_sources"])

        manifest_with_after = copy.deepcopy(manifest_a)
        manifest_with_after["evidence_sources"][
            "business_768_future_after_body_append_compatibility_probe_path"
        ] = EXPECTED_PRIOR_PACKET_REFERENCES[2]["path"]
        fp_after_before = _typed_fingerprint(manifest_with_after)
        assert_body_registration(packet_a, manifest_with_after)
        self.assertEqual(_typed_fingerprint(manifest_with_after), fp_after_before)

        self.assertEqual(_read_bytes(BODY_SOURCE_PATH), body_source_before)
        for path, raw_bytes in prior_snapshots.items():
            self.assertEqual(_read_bytes(path), raw_bytes)

    def test_body_registration_faults_reject_and_restore(self) -> None:
        baseline_packet, baseline_manifest = load_body_registration_controls()
        baseline_fp = _typed_fingerprint((baseline_packet, baseline_manifest))

        def _interpose_before_body(p: dict[str, Any], m: dict[str, Any]) -> None:
            items = list(m["evidence_sources"].items())
            body_idx = [k for k, _ in items].index(BODY_MANIFEST_KEY)
            items.insert(
                body_idx,
                ("interposed_before_body_path", EXPECTED_PRIOR_PACKET_REFERENCES[0]["path"]),
            )
            m["evidence_sources"] = dict(items)

        def _reorder_manifest_root(p: dict[str, Any], m: dict[str, Any]) -> None:
            reordered = {k: m[k] for k in reversed(list(m.keys()))}
            m.clear()
            m.update(reordered)

        fault_table: list[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]]] = [
            ("wrong_scope_sha", lambda p, m: p.__setitem__("assignment_scope_sha256", primary.ASSIGNMENT_SCOPE_SHA256)),
            ("wrong_source_head", lambda p, m: p.__setitem__("source_head", primary.SOURCE_HEAD)),
            ("extra_packet_top_key", lambda p, m: p.__setitem__("extra_top_key", True)),
            ("single_handler_row", lambda p, m: p.__setitem__("registered_handlers", p["registered_handlers"][:1])),
            ("extra_handler_row_field", lambda p, m: p["registered_handlers"][0].__setitem__("extra_field", "x")),
            ("swapped_seed_association", lambda p, m: p["registered_handlers"][0].__setitem__("seed_id", REPLACE_SEED_ID)),
            ("wrong_source_sha", lambda p, m: p["records"][0].__setitem__("sha256", "0" * 64)),
            ("wrong_source_parent", lambda p, m: p["records"][0].__setitem__("parent", "f" * 40)),
            ("wrong_selector", lambda p, m: p["records"][0]["test_selectors"].__setitem__(1, RESET_SELECTOR)),
            ("contract_body_calls", lambda p, m: p["records"][0]["characterized_contract"]["request_body"].__setitem__("call_count", 2)),
            ("exclusion_overclaim", lambda p, m: p["records"][0]["exclusions"].__setitem__("full_seed_acceptance", False)),
            ("prior_packet_size_confusion", lambda p, m: p["prior_packet_references"][0].__setitem__("utf8_bytes", 15348)),
            ("missing_body_manifest_key", lambda p, m: m["evidence_sources"].pop(BODY_MANIFEST_KEY, None)),
            ("wrong_body_manifest_target", lambda p, m: m["evidence_sources"].__setitem__(BODY_MANIFEST_KEY, AUTHORITY_PACKET_REL_PATH)),
            ("interposed_manifest_mapping", _interpose_before_body),
            ("reordered_manifest_root", _reorder_manifest_root),
            ("extra_manifest_root_key", lambda p, m: m.__setitem__("extra_root_key", True)),
            ("nonzero_raw_delta_credit", lambda p, m: p["promotion_assessment"].__setitem__("raw_registration_delta", 1)),
            ("bool_int_confusion_closures", lambda p, m: p.__setitem__("closures_authorized", 0)),
            ("float_int_confusion_evidenced", lambda p, m: p["promotion_assessment"]["canonical_after_on_exact_acceptance"].__setitem__("evidenced", 398.0)),
            ("premature_group_reconciliation", lambda p, m: p["accounting"].__setitem__("group_reconciliation_complete", True)),
            ("premature_full_union_complete", lambda p, m: p["accounting"].__setitem__("full_group_union_complete", True)),
            ("premature_ledger_status", lambda p, m: p["accounting"].__setitem__("ledger_integration_status", "COMPLETED_PROVENANCE")),
        ]

        for label, mutate in fault_table:
            with self.subTest(fault=label):
                good_p, good_m = load_body_registration_controls()
                assert_body_registration(good_p, good_m)

                bad_p, bad_m = load_body_registration_controls()
                mutate(bad_p, bad_m)
                mutated_fp = _typed_fingerprint((bad_p, bad_m))
                self.assertNotEqual(mutated_fp, baseline_fp, label)

                with self.assertRaises(AssertionError, msg=f"{label} did not raise AssertionError"):
                    assert_body_registration(bad_p, bad_m)

                self.assertEqual(_typed_fingerprint((bad_p, bad_m)), mutated_fp, label)

                restored_p, restored_m = load_body_registration_controls()
                self.assertEqual(_typed_fingerprint((restored_p, restored_m)), baseline_fp)
                assert_body_registration(restored_p, restored_m)


if __name__ == "__main__":
    unittest.main()
