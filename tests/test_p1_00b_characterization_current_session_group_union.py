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

import tests.test_p1_00b_characterization_current_session_body_registration as body_reg

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
UNION_PACKET_REL_PATH = "docs/refactor/two-server/section-1/inventory/current-session-five-source-union.json"
MANIFEST_REL_PATH = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
BODY_MANIFEST_KEY = "business_768_current_session_body_registration_path"
UNION_MANIFEST_KEY = "business_768_current_session_group_union_path"

PRE_UNION_MANIFEST_UTF8_BYTES = 4724
PRE_UNION_MANIFEST_SHA256 = "69bc0b5963ea5b509d2d972806c1b630453eb8c064b0d0918f42910517fecde8"
BODY_MODULE_REL_PATH = "tests/test_p1_00b_characterization_current_session_body_registration.py"
BODY_MODULE_UTF8_BYTES = 33864
BODY_MODULE_SHA256 = "b9a9550bddaa0f4e84886ff72a2c104a02cbfd53dd116eb72aede36a416b849b"

ASSIGNMENT_ID = "WA-P1-00B-COMPLETION-20261102-001"
ASSIGNMENT_SCOPE_SHA256 = "80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1"
PARENT_ASSIGNMENT_ID = "WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001"
ORIGINAL_PARENT_ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
TASK_ID = "p1-00b-current-session-group-union-20261103-001"
SCENARIO_KEY = "P1-00B-CURRENT-SESSION-GROUP-BU-V1.physical-five-source-seven-scenario-union"
SOURCE_HEAD = "7c1519c77d335156a1a175fd83c7c12d648678e7"

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
    "owner_packet_references",
    "source_index",
    "scenario_index",
    "distinct_seed_ids",
    "raw_membership_delta",
    "counts_after_union",
    "accounting",
    "strict_canonical_progress",
    "origin_matrix_unresolved_cells",
    "canonical_blockers",
    "coverage_status",
    "seed_acceptance_status",
    "closures_authorized",
    "stages",
)

OWNER_REF_KEYS = ("path", "utf8_bytes", "sha256", "accepted_commit")
PROJECTION_KEYS = (
    "path",
    "sha256",
    "utf8_bytes",
    "mode",
    "last_commit",
    "parent",
    "seed_ids",
    "test_selectors",
)
SCENARIO_ROW_KEYS = ("source_path", "selector", "seed_id")
MANIFEST_ROOT_KEYS = (
    "schema_version",
    "stage",
    "assignment_id",
    "inventory",
    "evidence_sources",
    "counts",
    "coverage_status",
    "canonical_blockers",
)

EXPECTED_OWNER_REFS: tuple[dict[str, Any], ...] = (
    {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-reset-execute-singleton.json",
        "utf8_bytes": 9510,
        "sha256": "4ef4ffe40f33dfe4328573bdca380b220e33306e1bee5f18d5baf54aef7170a2",
        "accepted_commit": "226e3b111a393bb834c4f8a91ac473f2d766f915",
    },
    {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-replace-execute-singleton.json",
        "utf8_bytes": 9738,
        "sha256": "cf251e42b72c41490e205499dbc269704d75ba422dbf730ef1027d29101d579c",
        "accepted_commit": "c9e9b30cd919472b1e253bc9b6da53da644171a8",
    },
    {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-verifier-only.json",
        "utf8_bytes": 11436,
        "sha256": "09c03b798d7c268a987874c1ebe3e834ed534edc97eade4be9b68e097d27f960",
        "accepted_commit": "bfad4a12866719f255f003d15625c87ccd8da1b5",
    },
    {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-authority-registration-security.json",
        "utf8_bytes": 12255,
        "sha256": "676426367cb4fd92ace6cd9f2bd04c90fb70bc8f24e357c3fe4293004f56c113",
        "accepted_commit": "041c5f5fdc06a7389238461dc29f3657ab19d8cd",
    },
    {
        "path": "docs/refactor/two-server/section-1/inventory/current-session-body-registration.json",
        "utf8_bytes": 10833,
        "sha256": "9ef13f20a2c85cc813e79e6e209398cddb25c6f60a66eb12e47013d63cc49049",
        "accepted_commit": "2a0ca26f7c8a8fa33dc105d668f795bbfa11797a",
    },
)

EXPECTED_DISTINCT_SEEDS = [
    "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562",
    "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593",
    "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539",
]

EXPECTED_COUNTS_AFTER_UNION = {
    "expected_seed_count": 465,
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}

EXPECTED_ACCOUNTING = {
    "current_group_status_on_exact_acceptance": "PHYSICAL_UNION_VERIFIED_ONLY",
    "source_union_complete": True,
    "full_group_union_complete": True,
    "physical_union_sources_verified": 5,
    "physical_union_scenarios_verified": 7,
    "remaining_union_units": 0,
    "remaining_union_scenarios": 0,
    "group_reconciliation_complete": False,
    "canonical_manifest_and_ledger_integration_complete": False,
    "ledger_integration_status": "PENDING",
    "outcome_b_l_required": True,
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


def _read_bytes(rel_path: str) -> bytes:
    return (REPO_ROOT / rel_path).read_bytes()


def _read_json(rel_path: str) -> dict[str, Any]:
    raw = _read_bytes(rel_path)
    doc = json.loads(raw.decode("utf-8"))
    if type(doc) is not dict:
        raise AssertionError(f"{rel_path} root must be dict")
    return doc


def _assert_typed_equal(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected):
        raise AssertionError(f"{label}: type {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(expected, dict):
        if list(actual.keys()) != list(expected.keys()):
            raise AssertionError(f"{label}: dict keys {list(actual.keys())!r} != {list(expected.keys())!r}")
        for k, exp_v in expected.items():
            _assert_typed_equal(actual[k], exp_v, f"{label}.{k}")
        return
    if isinstance(expected, (list, tuple)):
        if len(actual) != len(expected):
            raise AssertionError(f"{label}: len {len(actual)} != {len(expected)}")
        for idx, (act_v, exp_v) in enumerate(zip(actual, expected)):
            _assert_typed_equal(act_v, exp_v, f"{label}[{idx}]")
        return
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def _inspect_source_file(rel_path: str) -> dict[str, Any]:
    full_path = REPO_ROOT / rel_path
    if not full_path.exists() or full_path.is_symlink():
        raise AssertionError(f"missing or symlinked source file: {rel_path}")
    st = full_path.stat()
    if not stat.S_ISREG(st.st_mode):
        raise AssertionError(f"not a regular file: {rel_path}")
    if (st.st_mode & 0o111) != 0:
        raise AssertionError(f"executable bit set on source file: {rel_path}")
    mode_str = "100644"
    raw = full_path.read_bytes()
    tree = ast.parse(raw.decode("utf-8"), filename=rel_path)
    selectors: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"):
                    selectors.append(f"{rel_path}::{node.name}::{item.name}")
    return {
        "utf8_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": mode_str,
        "selectors": selectors,
    }


def _reconstruct_pre_union_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if list(manifest.keys()) != list(MANIFEST_ROOT_KEYS):
        raise AssertionError(f"manifest root keys {list(manifest.keys())!r} != {list(MANIFEST_ROOT_KEYS)!r}")
    ev = manifest.get("evidence_sources")
    if type(ev) is not dict:
        raise AssertionError("manifest.evidence_sources must be exact dict")
    items = list(ev.items())
    body_idx = -1
    for idx, (k, _) in enumerate(items):
        if k == BODY_MANIFEST_KEY:
            body_idx = idx
            break
    if body_idx < 0:
        raise AssertionError(f"missing {BODY_MANIFEST_KEY} in manifest.evidence_sources")
    if body_idx + 1 >= len(items):
        raise AssertionError(f"missing {UNION_MANIFEST_KEY} immediately after {BODY_MANIFEST_KEY}")
    union_k, union_v = items[body_idx + 1]
    if union_k != UNION_MANIFEST_KEY:
        raise AssertionError(
            f"expected {UNION_MANIFEST_KEY} immediately after {BODY_MANIFEST_KEY}, got {union_k!r}"
        )
    if type(union_v) is not str or union_v != UNION_PACKET_REL_PATH:
        raise AssertionError(f"invalid union manifest target {union_v!r}")
    seen_paths = {v for _, v in items[: body_idx + 2]}
    for after_k, after_v in items[body_idx + 2 :]:
        if type(after_k) is not str or not after_k.endswith("_path") or after_k == UNION_MANIFEST_KEY:
            raise AssertionError(f"invalid future manifest key {after_k!r}")
        if (
            type(after_v) is not str
            or not after_v.startswith("docs/refactor/two-server/section-1/inventory/")
            or not after_v.endswith(".json")
            or after_v in seen_paths
        ):
            raise AssertionError(f"invalid or duplicate future manifest path {after_v!r}")
        seen_paths.add(after_v)
    pre_manifest = copy.deepcopy(manifest)
    pre_manifest["evidence_sources"] = dict(items[: body_idx + 1])
    serialized_pre = (json.dumps(pre_manifest, indent=2) + "\n").encode("utf-8")
    if len(serialized_pre) != PRE_UNION_MANIFEST_UTF8_BYTES:
        raise AssertionError(
            f"reconstructed pre-union manifest size {len(serialized_pre)} != {PRE_UNION_MANIFEST_UTF8_BYTES}"
        )
    if hashlib.sha256(serialized_pre).hexdigest() != PRE_UNION_MANIFEST_SHA256:
        raise AssertionError("reconstructed pre-union manifest sha256 mismatch")
    return pre_manifest


def load_current_group_union_controls() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return fresh (union_packet, manifest) loaded from disk."""
    return copy.deepcopy(_read_json(UNION_PACKET_REL_PATH)), copy.deepcopy(_read_json(MANIFEST_REL_PATH))


def assert_current_group_union(packet: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Validate supplied union packet and manifest against physical files and accepted body oracle."""
    if type(packet) is not dict:
        raise AssertionError("packet must be exact dict")
    if type(manifest) is not dict:
        raise AssertionError("manifest must be exact dict")
    if list(packet.keys()) != list(PACKET_TOP_KEYS):
        raise AssertionError(f"packet top keys {list(packet.keys())!r} != {list(PACKET_TOP_KEYS)!r}")

    body_mod_bytes = _read_bytes(BODY_MODULE_REL_PATH)
    if len(body_mod_bytes) != BODY_MODULE_UTF8_BYTES:
        raise AssertionError("accepted body registration module byte length mismatch")
    if hashlib.sha256(body_mod_bytes).hexdigest() != BODY_MODULE_SHA256:
        raise AssertionError("accepted body registration module sha256 mismatch")

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

    _assert_typed_equal(packet["records"], [], "packet.records")

    owner_refs = packet["owner_packet_references"]
    if type(owner_refs) is not list or len(owner_refs) != len(EXPECTED_OWNER_REFS):
        raise AssertionError("packet.owner_packet_references must be a 5-element list")
    for idx, exp_ref in enumerate(EXPECTED_OWNER_REFS):
        ref = owner_refs[idx]
        if type(ref) is not dict or list(ref.keys()) != list(OWNER_REF_KEYS):
            raise AssertionError(f"owner_packet_references[{idx}] keys mismatch")
        _assert_typed_equal(ref, exp_ref, f"packet.owner_packet_references[{idx}]")

    source_index = packet["source_index"]
    if type(source_index) is not list or len(source_index) != len(EXPECTED_OWNER_REFS):
        raise AssertionError("packet.source_index must be a 5-element list")

    derived_projections: list[dict[str, Any]] = []
    derived_scenarios: list[dict[str, str]] = []
    seen_source_paths: set[str] = set()
    seen_selectors: set[str] = set()
    ordered_distinct_seeds: list[str] = []

    for idx, ref in enumerate(owner_refs):
        raw_owner = _read_bytes(ref["path"])
        if len(raw_owner) != ref["utf8_bytes"]:
            raise AssertionError(f"owner packet byte size mismatch for {ref['path']}")
        if hashlib.sha256(raw_owner).hexdigest() != ref["sha256"]:
            raise AssertionError(f"owner packet sha256 mismatch for {ref['path']}")
        owner_doc = json.loads(raw_owner.decode("utf-8"))
        owner_records = owner_doc.get("records")
        if type(owner_records) is not list or len(owner_records) != 1:
            raise AssertionError(f"owner packet {ref['path']} must have singleton records")
        rec0 = owner_records[0]
        proj = {k: copy.deepcopy(rec0[k]) for k in PROJECTION_KEYS}
        derived_projections.append(proj)

        supplied_proj = source_index[idx]
        if type(supplied_proj) is not dict or list(supplied_proj.keys()) != list(PROJECTION_KEYS):
            raise AssertionError(f"source_index[{idx}] must have exact 8 keys {PROJECTION_KEYS!r}")
        _assert_typed_equal(supplied_proj, proj, f"packet.source_index[{idx}]")

        src_path = supplied_proj["path"]
        if src_path in seen_source_paths:
            raise AssertionError(f"duplicate source_index path {src_path}")
        seen_source_paths.add(src_path)

        phys = _inspect_source_file(src_path)
        _assert_typed_equal(supplied_proj["utf8_bytes"], phys["utf8_bytes"], f"source_index[{idx}].utf8_bytes")
        _assert_typed_equal(supplied_proj["sha256"], phys["sha256"], f"source_index[{idx}].sha256")
        _assert_typed_equal(supplied_proj["mode"], "100644", f"source_index[{idx}].mode")
        _assert_typed_equal(phys["mode"], "100644", f"physical_mode[{idx}]")
        _assert_typed_equal(
            supplied_proj["test_selectors"],
            phys["selectors"],
            f"source_index[{idx}].test_selectors",
        )

        seeds = supplied_proj["seed_ids"]
        sels = supplied_proj["test_selectors"]
        if type(seeds) is not list or type(sels) is not list or len(seeds) != len(sels) or not seeds:
            raise AssertionError(f"invalid seed_ids/test_selectors cardinality at source_index[{idx}]")
        if len(set(seeds)) != len(seeds) or len(set(sels)) != len(sels):
            raise AssertionError(f"duplicate seed or selector within source_index[{idx}]")

        for seed_id, sel in zip(seeds, sels, strict=True):
            if sel in seen_selectors:
                raise AssertionError(f"duplicate selector across union: {sel}")
            seen_selectors.add(sel)
            if seed_id not in ordered_distinct_seeds:
                ordered_distinct_seeds.append(seed_id)
            derived_scenarios.append(
                {
                    "source_path": src_path,
                    "selector": sel,
                    "seed_id": seed_id,
                }
            )

    commit_chain_pairs = (
        (0, 2),
        (1, 0),
        (2, 4),
        (3, 1),
    )
    for child_idx, parent_idx in commit_chain_pairs:
        if source_index[child_idx]["parent"] != source_index[parent_idx]["last_commit"]:
            raise AssertionError(f"broken source commit chain between {child_idx} and {parent_idx}")
    if source_index[4]["parent"] != "e5c08c61895961bce399da9d8c69f561d7538ea1":
        raise AssertionError("source_index[4].parent must match historical checkpoint e5c08c61")

    scenario_index = packet["scenario_index"]
    if type(scenario_index) is not list or len(scenario_index) != 7:
        raise AssertionError("packet.scenario_index must be a 7-element list")
    for idx, row in enumerate(scenario_index):
        if type(row) is not dict or list(row.keys()) != list(SCENARIO_ROW_KEYS):
            raise AssertionError(f"scenario_index[{idx}] must have exact 3 keys {SCENARIO_ROW_KEYS!r}")
    _assert_typed_equal(scenario_index, derived_scenarios, "packet.scenario_index")

    _assert_typed_equal(packet["distinct_seed_ids"], EXPECTED_DISTINCT_SEEDS, "packet.distinct_seed_ids")
    _assert_typed_equal(ordered_distinct_seeds, EXPECTED_DISTINCT_SEEDS, "derived_distinct_seed_ids")
    _assert_typed_equal(packet["raw_membership_delta"], [], "packet.raw_membership_delta")
    _assert_typed_equal(packet["counts_after_union"], EXPECTED_COUNTS_AFTER_UNION, "packet.counts_after_union")
    _assert_typed_equal(packet["accounting"], EXPECTED_ACCOUNTING, "packet.accounting")
    _assert_typed_equal(
        packet["strict_canonical_progress"],
        {"proven": 288, "partial": 177, "total": 465},
        "packet.strict_canonical_progress",
    )
    _assert_typed_equal(packet["origin_matrix_unresolved_cells"], 24, "packet.origin_matrix_unresolved_cells")
    _assert_typed_equal(packet["canonical_blockers"], EXPECTED_BLOCKERS, "packet.canonical_blockers")
    _assert_typed_equal(packet["coverage_status"], "PARTIAL", "packet.coverage_status")
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

    pre_union_manifest = _reconstruct_pre_union_manifest(manifest)
    body_packet, _ = body_reg.load_body_registration_controls()
    body_raw = (json.dumps(body_packet, indent=2) + "\n").encode("utf-8")
    if len(body_raw) != EXPECTED_OWNER_REFS[4]["utf8_bytes"]:
        raise AssertionError("loaded body packet byte size mismatch against owner ref")
    if hashlib.sha256(body_raw).hexdigest() != EXPECTED_OWNER_REFS[4]["sha256"]:
        raise AssertionError("loaded body packet sha256 mismatch against owner ref")

    body_reg.assert_body_registration(body_packet, pre_union_manifest)
    body_reg.assert_body_registration(body_packet, manifest)


def _exact_fingerprint(obj: Any) -> Any:
    if isinstance(obj, dict):
        return ("dict", tuple((k, _exact_fingerprint(v)) for k, v in obj.items()))
    if isinstance(obj, list):
        return ("list", tuple(_exact_fingerprint(v) for v in obj))
    if isinstance(obj, tuple):
        return ("tuple", tuple(_exact_fingerprint(v) for v in obj))
    return (type(obj).__name__, repr(obj))


def _ordered_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _set_path(root: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    cur: Any = root
    for step in path[:-1]:
        cur = cur[step]
    cur[path[-1]] = value


def _del_path(root: dict[str, Any], path: tuple[Any, ...]) -> None:
    cur: Any = root
    for step in path[:-1]:
        cur = cur[step]
    del cur[path[-1]]


def _append_path(root: dict[str, Any], path: tuple[Any, ...], value: Any) -> None:
    cur: Any = root
    for step in path:
        cur = cur[step]
    cur.append(value)


class CurrentSessionGroupUnionTests(unittest.TestCase):
    def test_actual_current_group_union_preserves_canonical_membership(self) -> None:
        tracked_paths = [
            UNION_PACKET_REL_PATH,
            MANIFEST_REL_PATH,
            BODY_MODULE_REL_PATH,
            *(ref["path"] for ref in EXPECTED_OWNER_REFS),
        ]
        packet, manifest = load_current_group_union_controls()
        for proj in packet["source_index"]:
            tracked_paths.append(proj["path"])

        before_disk = {
            rel: (len(_read_bytes(rel)), hashlib.sha256(_read_bytes(rel)).hexdigest())
            for rel in tracked_paths
        }
        before_pkt_fp = _exact_fingerprint(packet)
        before_man_fp = _exact_fingerprint(manifest)

        assert_current_group_union(packet, manifest)

        self.assertEqual(_exact_fingerprint(packet), before_pkt_fp)
        self.assertEqual(_exact_fingerprint(manifest), before_man_fp)
        self.assertEqual(len(packet["owner_packet_references"]), 5)
        self.assertEqual(len(packet["source_index"]), 5)
        self.assertEqual(len(packet["scenario_index"]), 7)
        self.assertEqual(packet["records"], [])

        future_manifest = copy.deepcopy(manifest)
        future_manifest["evidence_sources"]["business_768_current_session_group_ledger_closeout_path"] = (
            "docs/refactor/two-server/section-1/inventory/surface-api.json"
        )
        future_man_fp = _exact_fingerprint(future_manifest)
        assert_current_group_union(packet, future_manifest)
        self.assertEqual(_exact_fingerprint(future_manifest), future_man_fp)

        after_disk = {
            rel: (len(_read_bytes(rel)), hashlib.sha256(_read_bytes(rel)).hexdigest())
            for rel in tracked_paths
        }
        self.assertEqual(after_disk, before_disk)

    def test_union_faults_reject_and_restore(self) -> None:
        fault_table: tuple[tuple[str, Callable[[dict[str, Any], dict[str, Any]], None]], ...] = (
            ("header_schema_bool", lambda p, m: _set_path(p, ("schema_version",), True)),
            ("header_stage", lambda p, m: _set_path(p, ("stage",), "P1-00")),
            ("header_frozen_a_scope", lambda p, m: _set_path(p, ("assignment_scope_sha256",), "6b6eff78bf62c60b839b42fb3cc9bd286fb096b3f8a58d41a59cf8f218d13257")),
            ("header_frozen_a_head", lambda p, m: _set_path(p, ("source_head",), "8b073836d57b35e9758ac6fce9c63e87831f4865")),
            ("header_task_id", lambda p, m: _set_path(p, ("task_id",), "p1-00b-current-session-body-registration-a-20261102-003")),
            ("header_scenario_key", lambda p, m: _set_path(p, ("scenario_key",), "WRONG_KEY")),
            ("packet_missing_key", lambda p, m: _del_path(p, ("records",))),
            ("packet_extra_key", lambda p, m: _set_path(p, ("unexpected_root",), 1)),
            ("records_non_empty", lambda p, m: _append_path(p, ("records",), {"path": "x.py"})),
            ("owner_ref_missing", lambda p, m: _del_path(p, ("owner_packet_references", 4))),
            ("owner_ref_duplicate", lambda p, m: _set_path(p, ("owner_packet_references", 4), copy.deepcopy(p["owner_packet_references"][0]))),
            ("owner_ref_wrong_sha", lambda p, m: _set_path(p, ("owner_packet_references", 2, "sha256"), "0" * 64)),
            ("owner_ref_wrong_bytes", lambda p, m: _set_path(p, ("owner_packet_references", 2, "utf8_bytes"), 11437)),
            ("owner_ref_wrong_commit", lambda p, m: _set_path(p, ("owner_packet_references", 4, "accepted_commit"), "0" * 40)),
            ("owner_ref_extra_key", lambda p, m: _set_path(p, ("owner_packet_references", 0, "manifest_key"), "x")),
            ("source_index_missing", lambda p, m: _del_path(p, ("source_index", 4))),
            ("source_index_duplicate", lambda p, m: _set_path(p, ("source_index", 4), copy.deepcopy(p["source_index"][0]))),
            ("source_index_wrong_sha", lambda p, m: _set_path(p, ("source_index", 1, "sha256"), "f" * 64)),
            ("source_index_wrong_bytes", lambda p, m: _set_path(p, ("source_index", 1, "utf8_bytes"), 99999)),
            ("source_index_wrong_mode", lambda p, m: _set_path(p, ("source_index", 0, "mode"), "100755")),
            ("source_index_wrong_parent", lambda p, m: _set_path(p, ("source_index", 4, "parent"), "0" * 40)),
            ("source_index_wrong_selector", lambda p, m: _set_path(p, ("source_index", 3, "test_selectors", 0), "tests/wrong.py::X::test_y")),
            ("source_index_extra_key", lambda p, m: _set_path(p, ("source_index", 0, "extra"), 1)),
            ("scenario_index_missing", lambda p, m: _del_path(p, ("scenario_index", 6))),
            ("scenario_index_extra", lambda p, m: _append_path(p, ("scenario_index",), copy.deepcopy(p["scenario_index"][0]))),
            ("scenario_index_wrong_seed", lambda p, m: _set_path(p, ("scenario_index", 5, "seed_id"), "API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593")),
            ("scenario_index_wrong_source", lambda p, m: _set_path(p, ("scenario_index", 6, "source_path"), "tests/test_p1_00b_characterization_internal_reset_execute_failure.py")),
            ("scenario_index_reordered", lambda p, m: _set_path(p, ("scenario_index",), p["scenario_index"][:5] + [p["scenario_index"][6], p["scenario_index"][5]])),
            ("distinct_seeds_missing", lambda p, m: _del_path(p, ("distinct_seed_ids", 2))),
            ("distinct_seeds_reordered", lambda p, m: _set_path(p, ("distinct_seed_ids",), list(reversed(p["distinct_seed_ids"])))),
            ("raw_delta_non_empty", lambda p, m: _append_path(p, ("raw_membership_delta",), "API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562")),
            ("counts_evidenced_float", lambda p, m: _set_path(p, ("counts_after_union", "evidenced_seed_count"), 398.0)),
            ("counts_remaining_credit", lambda p, m: _set_path(p, ("counts_after_union", "remaining_seed_count"), 66)),
            ("accounting_premature_completed", lambda p, m: _set_path(p, ("accounting", "current_group_status_on_exact_acceptance"), "COMPLETED_PROVENANCE")),
            ("accounting_source_union_false", lambda p, m: _set_path(p, ("accounting", "source_union_complete"), False)),
            ("accounting_full_union_int", lambda p, m: _set_path(p, ("accounting", "full_group_union_complete"), 1)),
            ("accounting_reconciliation_true", lambda p, m: _set_path(p, ("accounting", "group_reconciliation_complete"), True)),
            ("accounting_reconciliation_int_zero", lambda p, m: _set_path(p, ("accounting", "group_reconciliation_complete"), 0)),
            ("accounting_ledger_complete_true", lambda p, m: _set_path(p, ("accounting", "canonical_manifest_and_ledger_integration_complete"), True)),
            ("accounting_ledger_status_closed", lambda p, m: _set_path(p, ("accounting", "ledger_integration_status"), "COMPLETE")),
            ("accounting_b_l_required_false", lambda p, m: _set_path(p, ("accounting", "outcome_b_l_required"), False)),
            ("accounting_sixth_allowed", lambda p, m: _set_path(p, ("accounting", "no_sixth_behavior_accepted_before_complete_reconciliation"), False)),
            ("accounting_pending_five_reset", lambda p, m: _set_path(p, ("accounting", "pending_five"), 0)),
            ("accounting_added_units_credit", lambda p, m: _set_path(p, ("accounting", "added_behavior_units"), 1)),
            ("accounting_added_seeds_credit", lambda p, m: _set_path(p, ("accounting", "added_distinct_seeds"), 1)),
            ("accounting_full_seeds_credit", lambda p, m: _set_path(p, ("accounting", "accepted_full_seeds"), 1)),
            ("accounting_raw_delta_credit", lambda p, m: _set_path(p, ("accounting", "raw_registration_delta"), 1)),
            ("strict_proven_credit", lambda p, m: _set_path(p, ("strict_canonical_progress", "proven"), 289)),
            ("origin_cells_reduced", lambda p, m: _set_path(p, ("origin_matrix_unresolved_cells",), 23)),
            ("blocker_closed", lambda p, m: _del_path(p, ("canonical_blockers", 1))),
            ("coverage_status_complete", lambda p, m: _set_path(p, ("coverage_status",), "COMPLETE")),
            ("seed_acceptance_assessed", lambda p, m: _set_path(p, ("seed_acceptance_status",), "ASSESSED")),
            ("closures_authorized_true", lambda p, m: _set_path(p, ("closures_authorized",), True)),
            ("closures_authorized_int_zero", lambda p, m: _set_path(p, ("closures_authorized",), 0)),
            ("stage_p1_00b_complete", lambda p, m: _set_path(p, ("stages", "P1-00b"), "COMPLETE")),
            ("manifest_missing_union", lambda p, m: _del_path(m, ("evidence_sources", UNION_MANIFEST_KEY))),
            ("manifest_wrong_union_path", lambda p, m: _set_path(m, ("evidence_sources", UNION_MANIFEST_KEY), "docs/refactor/two-server/section-1/inventory/wrong.json")),
            (
                "manifest_interposed_before_union",
                lambda p, m: _set_path(
                    m,
                    ("evidence_sources",),
                    {
                        **{k: v for k, v in m["evidence_sources"].items() if k != UNION_MANIFEST_KEY},
                        "interposed_path": "docs/refactor/two-server/section-1/inventory/interposed.json",
                        UNION_MANIFEST_KEY: UNION_PACKET_REL_PATH,
                    },
                ),
            ),
            ("manifest_extra_root_key", lambda p, m: _set_path(m, ("extra_root",), True)),
            (
                "manifest_reordered_root_keys",
                lambda p, m: (
                    m.update({"canonical_blockers": m.pop("canonical_blockers")}),
                    m.update({"coverage_status": m.pop("coverage_status")}),
                ),
            ),
        )

        for label, mutate in fault_table:
            with self.subTest(fault=label):
                healthy_pkt, healthy_man = load_current_group_union_controls()
                assert_current_group_union(healthy_pkt, healthy_man)

                before_fp = (_exact_fingerprint(healthy_pkt), _exact_fingerprint(healthy_man))
                before_json = (_ordered_json(healthy_pkt), _ordered_json(healthy_man))

                bad_pkt = copy.deepcopy(healthy_pkt)
                bad_man = copy.deepcopy(healthy_man)
                mutate(bad_pkt, bad_man)

                bad_fp = (_exact_fingerprint(bad_pkt), _exact_fingerprint(bad_man))
                bad_json = (_ordered_json(bad_pkt), _ordered_json(bad_man))
                self.assertNotEqual(bad_fp, before_fp, f"{label} did not mutate exact fingerprint")
                self.assertNotEqual(
                    (bad_fp, bad_json),
                    (before_fp, before_json),
                    f"{label} did not mutate input state",
                )

                with self.assertRaises(AssertionError, msg=f"{label} must raise AssertionError"):
                    assert_current_group_union(bad_pkt, bad_man)

                self.assertEqual(
                    (_exact_fingerprint(bad_pkt), _exact_fingerprint(bad_man)),
                    bad_fp,
                    f"{label} mutated bad inputs during rejection",
                )
                self.assertEqual(
                    (_ordered_json(bad_pkt), _ordered_json(bad_man)),
                    bad_json,
                    f"{label} altered bad JSON ordering during rejection",
                )

                after_pkt, after_man = load_current_group_union_controls()
                assert_current_group_union(after_pkt, after_man)
                self.assertEqual(
                    (_exact_fingerprint(after_pkt), _exact_fingerprint(after_man)),
                    before_fp,
                )


if __name__ == "__main__":
    unittest.main()
