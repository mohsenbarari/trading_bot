"""Tests for reconciliation schema profile advisory and cap safety."""
import copy
import json
import os
from pathlib import Path
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_task_context import (
    preparation_notes,
    _is_reconciliation_schema_applicable,
    _build_reconciliation_schema_profile,
    _RECONCILIATION_SCHEMA_PROFILE_ID,
    _RECONCILIATION_SCHEMA_PROFILE_RULES,
)
from scripts.two_site_proposal_runner import (
    task_preparation_notes,
    enrich_repository_dependency_context,
)


CANONICAL_INVENTORY_COVERAGE = "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
CANONICAL_INVENTORY_RECONCILE = "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json"

EXPECTED_RECONCILIATION_SCHEMA_PROFILE_ID = "P1_RECONCILIATION_SCHEMA_V1"
EXPECTED_RECONCILIATION_SCHEMA_PROFILE_RULES = [
    "derive exact recursive key sets and built-in container/key/leaf types from explicit schema: bool is not int, float is not int, str/dict subclasses are excluded where exact types required",
    "independently read canonical manifest and verify exact accounting/stage/pending batch, never count every scenario as a new seed or close unproven origin cells",
    "pin all supplied path/commit/SHA/selector identities from independent immutable reference literals/actual retained bytes, never reuse the implementation/input pin as expected oracle",
    "independently corrupt each required nested shape, missing/extra key, wrong type and commit/SHA/selector, with one valid baseline and check-specific failure expectations (unrelated ValueError must not make negative pass)",
    "Reviewer maps every requirement/positive/negative to actual assertions and reuse candidates before VERIFIED; PASS/covered_requirements/VERIFIED alone is not final acceptance",
]


def _sample_inventory_json() -> str:
    return json.dumps({
        "schema_version": 1,
        "stage": "P1-00b",
        "inventory": {"expected_seed_count": 465},
        "coverage_status": "PARTIAL",
    })


def _make_reconciliation_task(**kwargs) -> dict:
    base = {
        "task_id": "WA-P1-00B-EVIDENCE-RECONCILE-20261001-116",
        "goal": "Execute characterization reconciliation against canonical manifest and schema validation.",
        "context_files": [
            CANONICAL_INVENTORY_COVERAGE,
        ],
        "allowed_files": [
            "tests/test_p1_00b_characterization_reconciliation_trade.py",
            CANONICAL_INVENTORY_COVERAGE,
        ],
        "requirements": [
            "Verify reconciliation schema matches canonical inventory records.",
            "Ensure provenance and accounting invariants are satisfied.",
        ],
        "test_selectors": [
            "tests/test_p1_00b_characterization_reconciliation_trade.py",
        ],
    }
    base.update(kwargs)
    return base


def _make_canonical_sources() -> dict:
    return {
        CANONICAL_INVENTORY_COVERAGE: {"content": _sample_inventory_json()},
        CANONICAL_INVENTORY_RECONCILE: {"content": _sample_inventory_json()},
    }


def test_profile_id_rules_content_and_byte_budget():
    profile = _build_reconciliation_schema_profile()
    assert profile["profile_id"] == EXPECTED_RECONCILIATION_SCHEMA_PROFILE_ID
    rules = profile["rules"]
    assert isinstance(rules, list)
    assert len(rules) == 5
    assert rules == EXPECTED_RECONCILIATION_SCHEMA_PROFILE_RULES

    assert "bool is not int" in rules[0] and "float is not int" in rules[0] and "subclasses are excluded" in rules[0]
    assert "canonical manifest" in rules[1] and "never count every scenario as a new seed" in rules[1]
    assert "commit/SHA/selector identities" in rules[2] and "never reuse the implementation/input pin" in rules[2]
    assert "independently corrupt" in rules[3] and "unrelated ValueError must not make negative pass" in rules[3]
    assert "Reviewer maps every requirement" in rules[4] and "alone is not final acceptance" in rules[4]

    serialized = json.dumps({"reconciliation_schema_profile": profile})
    assert len(serialized.encode("utf-8")) <= 1200


def test_positive_matched_p1_reconciliation_schema_profile():
    task = _make_reconciliation_task()
    sources = _make_canonical_sources()

    assert _is_reconciliation_schema_applicable(task, sources) is True

    notes = preparation_notes(task, sources)
    assert "reconciliation_schema_profile" in notes
    profile = notes["reconciliation_schema_profile"]
    assert profile["profile_id"] == EXPECTED_RECONCILIATION_SCHEMA_PROFILE_ID
    assert profile["rules"] == EXPECTED_RECONCILIATION_SCHEMA_PROFILE_RULES

    for field in (
        "schema_version",
        "exact_blocker_ids",
        "warnings",
        "literal_hints",
        "omitted_literal_count",
        "source_contract_hints",
        "omitted_source_contract_hint_count",
        "dependency_context",
        "output_contract",
    ):
        assert field in notes

    assert len(json.dumps(notes).encode("utf-8")) <= 8192


def test_legacy_fields_preserved_when_matched():
    model_code = (
        "import enum\n"
        "from sqlalchemy import Column, DateTime, Enum, Integer, String\n"
        "class Status(str, enum.Enum):\n"
        "    PENDING = 'pending'\n"
        "    ACTIVE = 'active'\n"
        "class LedgerRow:\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    created_at = Column(DateTime, nullable=False)\n"
        "    status = Column(Enum(Status), nullable=False)\n"
        "    valued_status = Column(Enum(Status, values_callable=lambda cls: [x.value for x in cls]), nullable=False)\n"
    )
    helper_code = (
        "def helper_shape(x):\n"
        "    return {\n"
        "        'active': True,\n"
        "        'batch_id': x,\n"
        "        'status': 'ready',\n"
        "    }\n"
    )
    literal_code = 'msg = "معامله\\u200cای"\n'

    paired_goal = "Reconciliation schema validation for LedgerRow and helper_shape with P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
    matched_task = _make_reconciliation_task(goal=paired_goal)
    unmatched_task = _make_reconciliation_task(
        task_id="WA-P1-01-UNMATCHED-20261001-116",
        goal=paired_goal,
    )

    sources = _make_canonical_sources()
    sources["models/ledger.py"] = {"content": model_code}
    sources["scripts/service.py"] = {"content": helper_code}
    sources["scripts/app.py"] = {"content": literal_code}

    notes_matched = preparation_notes(matched_task, sources)
    notes_unmatched = preparation_notes(unmatched_task, sources)

    assert "reconciliation_schema_profile" in notes_matched
    assert "reconciliation_schema_profile" not in notes_unmatched

    assert len(notes_matched["literal_hints"]) > 0
    assert len(notes_matched["source_contract_hints"]) > 0
    assert len(notes_matched["dependency_context"]["model_contract_hints"]) > 0

    expected_model_hints = [
        {
            "path": "models/ledger.py",
            "model": "LedgerRow",
            "field": "created_at",
            "column_type": "DateTime",
            "required_without_declared_default": True,
        },
        {
            "path": "models/ledger.py",
            "model": "LedgerRow",
            "field": "status",
            "column_type": "Enum(Status)",
            "required_without_declared_default": True,
            "enum_database_representation": "member_name",
        },
        {
            "path": "models/ledger.py",
            "model": "LedgerRow",
            "field": "valued_status",
            "column_type": "Enum(Status, values_callable=lambda cls: [x.value for x in cls])",
            "required_without_declared_default": True,
            "enum_database_representation": "value",
        },
    ]
    assert notes_matched["dependency_context"]["model_contract_hints"] == expected_model_hints

    expected_source_hints = [
        {
            "path": "scripts/service.py",
            "function": "helper_shape",
            "keys": ["active", "batch_id", "status"],
        },
    ]
    assert notes_matched["source_contract_hints"] == expected_source_hints

    expected_literal_hint = {
        "path": "scripts/app.py",
        "line": 1,
        "json_literal": '"\\u0645\\u0639\\u0627\\u0645\\u0644\\u0647\\u200c\\u0627\\u06cc"',
        "is_fragment": False,
    }
    assert notes_matched["literal_hints"] == [expected_literal_hint]

    for key in (
        "schema_version",
        "exact_blocker_ids",
        "warnings",
        "literal_hints",
        "omitted_literal_count",
        "source_contract_hints",
        "omitted_source_contract_hint_count",
        "dependency_context",
        "output_contract",
    ):
        assert notes_matched[key] == notes_unmatched[key]

    assert len(json.dumps(notes_matched).encode("utf-8")) <= 8192
    assert len(json.dumps(notes_unmatched).encode("utf-8")) <= 8192


def test_near_cap_omission_without_hint_loss():
    zwnj = chr(0x200c)
    literal_lines = [f'v{i} = "معامله{zwnj}{i}"' for i in range(16)]
    func_lines = []
    for i in range(16):
        keys_dict = ", ".join(f'"k_{i:02d}_{j:02d}": {j}' for j in range(25))
        func_lines.append(f"def fn_pressure_{i}():\n    return {{{keys_dict}}}")
    helper_names = [f"fn_pressure_{i}" for i in range(16)]

    paired_goal = "Reconciliation schema validation for " + " ".join(helper_names)
    task_matched = _make_reconciliation_task(goal=paired_goal)
    task_unmatched = _make_reconciliation_task(
        task_id="WA-P1-01-UNMATCHED-20261001-116",
        goal=paired_goal,
    )

    sources = _make_canonical_sources()
    sources["scripts/app.py"] = {"content": "\n".join(literal_lines)}
    sources["scripts/helpers.py"] = {"content": "\n".join(func_lines)}

    notes_baseline = preparation_notes(task_unmatched, sources)
    base_bytes = len(json.dumps(notes_baseline).encode("utf-8"))

    profile = _build_reconciliation_schema_profile()
    candidate_with_profile = dict(notes_baseline)
    candidate_with_profile["reconciliation_schema_profile"] = profile
    complete_optional_field_increment = len(json.dumps(candidate_with_profile).encode("utf-8")) - base_bytes

    assert base_bytes <= 8192
    assert base_bytes + complete_optional_field_increment > 8192
    assert base_bytes > (8192 - complete_optional_field_increment)

    notes_matched = preparation_notes(task_matched, sources)
    assert "reconciliation_schema_profile" not in notes_matched
    assert notes_matched == notes_baseline

    assert len(notes_matched["literal_hints"]) > 0
    assert len(notes_matched["source_contract_hints"]) > 0
    assert notes_matched["literal_hints"] == notes_baseline["literal_hints"]
    assert notes_matched["source_contract_hints"] == notes_baseline["source_contract_hints"]


@pytest.mark.parametrize("mutation,desc", [
    ({"task_id": "WA-P1-01-RECONCILE-001"}, "wrong P1 stage"),
    ({"task_id": "test-task"}, "words only without P1 identity"),
    ({"task_id": "PREFIX-WA-P1-00B-001"}, "unanchored task_id prefix"),
    ({"task_id": "P1-00BA-001"}, "non-boundary trailing letter"),
    ({"task_id": 12345}, "non-string task_id"),
    ({"allowed_files": ["scripts/two_site_proposal_runner.py"]}, "unrelated source allowed path"),
    ({"allowed_files": ["tests/test_two_site_task_context.py"]}, "unrelated test allowed path"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/inventory/unrelated.json"]}, "arbitrary inventory json target"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/inventory/../recent-characterization.json"]}, "traversal inventory target"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/inventory/sub/recent-characterization.json"]}, "nested child inventory target"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/inventory/recent-characterization-.json"]}, "empty hyphen component in inventory target"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/inventory/recent-characterization.json.bak"]}, "lookalike extension inventory target"),
    ({"allowed_files": ["tests/test_p1_00b_characterization_reconciliationFOO.py"]}, "suffix lookalike without underscore"),
    ({"allowed_files": ["tests/test_p1_00b_characterization_reconciliation_.py"]}, "empty underscore component in test target"),
    ({"allowed_files": ["tests/test_p1_00b_characterization_reconciliation_trade.py.bak"]}, "lookalike test extension"),
    ({"allowed_files": ["../tests/test_p1_00b_characterization_reconciliation.py"]}, "traversal test path"),
    ({"allowed_files": ["tests\\\\test_p1_00b_characterization_reconciliation.py"]}, "backslash test path"),
    ({"allowed_files": ["/tests/test_p1_00b_characterization_reconciliation.py"]}, "absolute test path"),
    ({"allowed_files": ["docs/refactor/two-server/section-1/00-stage-ledger.md"]}, "ledger only without target"),
    ({"allowed_files": ["tests/test_p1_00b_characterization_reconciliation.py", "scripts/two_site_proposal_runner.py"]}, "mixed allowed scope"),
    ({"allowed_files": []}, "empty allowed_files"),
    ({"allowed_files": "tests/test_p1_00b_characterization_reconciliation_trade.py"}, "non-list allowed_files"),
    ({"allowed_files": [123]}, "non-string allowed_files item"),
    ({"goal": 12345}, "non-string goal"),
    (
        {
            "goal": "Ordinary characterization task without keywords",
            "requirements": ["Run characterization test only."],
        },
        "missing both reconciliation and validation keywords",
    ),
    (
        {
            "goal": "Execute characterization against canonical manifest and schema validation.",
            "requirements": ["Ensure schema and provenance invariants are satisfied."],
        },
        "missing reconciliation keyword",
    ),
    (
        {
            "goal": "Execute characterization reconciliation against canonical manifest.",
            "requirements": ["Ensure invariants are satisfied without other terms."],
        },
        "missing schema or validation keyword",
    ),
    ({"requirements": "Verify reconciliation schema"}, "non-list requirements"),
    ({"requirements": [123]}, "non-string requirement item"),
    ({"goal": "Reconciliation schema with sqlite database tables and columns"}, "persistence requested in goal"),
    ({"requirements": ["Reconciliation schema with sqlite database tables and columns"]}, "persistence requested in requirements"),
    ({"goal": "Reconciliation schema validation with live db session context"}, "live session requested in goal"),
    ({"requirements": ["Reconciliation schema validation with live db session context"]}, "live session requested in requirements"),
])
def test_no_match_parameter_matrix(mutation, desc):
    task = _make_reconciliation_task(**mutation)
    sources = _make_canonical_sources()
    assert _is_reconciliation_schema_applicable(task, sources) is False
    notes = preparation_notes(task, sources)
    assert "reconciliation_schema_profile" not in notes


@pytest.mark.parametrize("sources_mutation,desc", [
    ({}, "empty sources"),
    ({"scripts/app.py": {"content": "x = 1\n"}}, "no inventory source"),
    ({"docs/refactor/two-server/section-1/inventory/unrelated.json": {"content": _sample_inventory_json()}}, "arbitrary inventory source"),
    ({"docs/refactor/two-server/section-1/inventory/../recent-characterization.json": {"content": _sample_inventory_json()}}, "traversal inventory source"),
    ({"docs/refactor/two-server/section-1/inventory/recent-characterization-.json": {"content": _sample_inventory_json()}}, "empty hyphen component source"),
    ({"docs/refactor/two-server/section-1/inventory/recent-characterization.json.bak": {"content": _sample_inventory_json()}}, "lookalike extension inventory source"),
    ({CANONICAL_INVENTORY_COVERAGE: {"content": None}}, "null content canonical source"),
    ({CANONICAL_INVENTORY_COVERAGE: {"content": "   \n"}}, "whitespace only content canonical source"),
    ({CANONICAL_INVENTORY_COVERAGE: {"content": 999}}, "non-string content canonical source"),
    ({CANONICAL_INVENTORY_COVERAGE: 123}, "non-dict and non-string item canonical source"),
])
def test_no_match_sources_matrix(sources_mutation, desc):
    task = _make_reconciliation_task()
    assert _is_reconciliation_schema_applicable(task, sources_mutation) is False
    notes = preparation_notes(task, sources_mutation)
    assert "reconciliation_schema_profile" not in notes


@pytest.mark.parametrize("bad_input,desc", [
    ((None, {}), "None task"),
    (("not-a-dict", {}), "string task"),
    (({}, None), "None sources"),
    (({}, "not-a-dict"), "string sources"),
])
def test_malformed_inputs_fail_closed(bad_input, desc):
    task, sources = bad_input
    assert _is_reconciliation_schema_applicable(task, sources) is False


def test_positive_variations_canonical_paths():
    inventory_variants = [
        "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json",
    ]
    test_variants = [
        "tests/test_p1_00b_characterization_reconciliation.py",
        "tests/test_p1_00b_characterization_reconciliation_trade.py",
        "tests/test_p1_00b_characterization_reconciliation_trade_export.py",
    ]
    for inv_path in inventory_variants:
        sources = {inv_path: {"content": _sample_inventory_json()}}
        for test_path in test_variants:
            task = _make_reconciliation_task(
                allowed_files=[test_path, inv_path, "docs/refactor/two-server/section-1/00-stage-ledger.md"],
            )
            assert _is_reconciliation_schema_applicable(task, sources) is True
            notes = preparation_notes(task, sources)
            assert "reconciliation_schema_profile" in notes


def test_suffix_isolation():
    canonical_excerpt = json.dumps({"goal": "reconciliation and schema validation accounting"})
    task = _make_reconciliation_task(
        goal=f"Ordinary characterization task\nCANONICAL_SOURCE_EXCERPTS_JSON:\n{canonical_excerpt}",
        requirements=["Run characterization test only."],
    )
    sources = _make_canonical_sources()
    assert _is_reconciliation_schema_applicable(task, sources) is False
    notes = preparation_notes(task, sources)
    assert "reconciliation_schema_profile" not in notes


def test_profile_ownership_and_immutability():
    task = _make_reconciliation_task()
    sources = _make_canonical_sources()

    task_copy = copy.deepcopy(task)
    sources_copy = copy.deepcopy(sources)

    n1 = preparation_notes(task, sources)
    n2 = preparation_notes(task, sources)

    assert task == task_copy
    assert sources == sources_copy
    assert n1 == n2

    n1["reconciliation_schema_profile"]["rules"].append("MUTATION")
    assert "MUTATION" not in n2["reconciliation_schema_profile"]["rules"]

    n3 = preparation_notes(task, sources)
    assert "MUTATION" not in n3["reconciliation_schema_profile"]["rules"]
    assert len(n3["reconciliation_schema_profile"]["rules"]) == 5


def test_private_sentinel_not_echoed():
    sentinel = "SECRET_PRIVATE_SENTINEL_TOKEN_9999"
    task = _make_reconciliation_task(
        _private_sentinel=sentinel,
        secret_carrier={"token": sentinel},
    )
    sources = _make_canonical_sources()
    sources["scripts/secret.py"] = {"content": f'# {sentinel}\n'}

    notes = preparation_notes(task, sources)
    assert "reconciliation_schema_profile" in notes
    profile_dump = json.dumps(notes["reconciliation_schema_profile"])
    assert sentinel not in profile_dump


def test_runner_delegation_and_pure_context_enrichment():
    task = _make_reconciliation_task()
    sources = _make_canonical_sources()

    delegated_notes = task_preparation_notes(task, sources)
    assert "reconciliation_schema_profile" in delegated_notes
    assert delegated_notes["reconciliation_schema_profile"]["profile_id"] == EXPECTED_RECONCILIATION_SCHEMA_PROFILE_ID

    root = Path(".")
    enriched = enrich_repository_dependency_context(root, task, sources, delegated_notes)

    assert "reconciliation_schema_profile" in enriched
    assert enriched["reconciliation_schema_profile"] == delegated_notes["reconciliation_schema_profile"]
    assert enriched["dependency_context"]["persistence_contract_requested"] is False
    assert "session_context" not in enriched["dependency_context"]
    assert len(json.dumps(enriched).encode("utf-8")) <= 8192
