"""Source-bound first-attempt hints for the actual normal271 failure factors."""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import json
import pytest
from scripts.two_site_task_context import preparation_notes
from scripts.two_site_pre_review_receipt import p1_00_characterization_bootstrap_findings
from tests.test_two_site_byte_factor_prevention import notes_fixture

PREFIX = (
    'from __future__ import annotations\n'
    'import os\n'
    'os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"\n'
    'from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence\n'
    'install_p1_00_network_fence()\n'
)
P1 = "tests/test_p1_00b_characterization_first.py"


@pytest.mark.parametrize("path", [P1, "tests/test_p1_00_characterization_first.py"])
def test_profiled_new_characterization_scaffold_passes_real_static_gate(path):
    task, sources, profile = notes_fixture({path: 12000})
    before = copy.deepcopy((task, sources))
    result = preparation_notes(task, sources)
    contract = result["output_contract"]
    policy = contract["new_characterization_scaffold"]
    assert policy["bootstrap_prefix"] == PREFIX
    assert set(policy) == {"bootstrap_prefix", "fixture_import_rule", "persistence_snapshot_rule"}
    assert "private aliases also collect" in policy["fixture_import_rule"]
    assert "whole-file pytest collection" in policy["fixture_import_rule"]
    assert "every mapped persisted column" in policy["persistence_snapshot_rule"]
    assert "partial shared snapshot helper is not full-row proof" in policy["persistence_snapshot_rule"]
    assert contract["proposal_utf8_budget_profile"] == profile
    assert contract["max_new_test_file_utf8_bytes"] == 12000
    assert contract["new_test_file_draft_target_utf8_bytes"] == 9000
    candidate = {"changes": [{"path": path, "original_sha256": None,
                            "content": policy["bootstrap_prefix"] + "from api.routers import users\n"}]}
    assert p1_00_characterization_bootstrap_findings(candidate) == []
    # No advisory rewrites the candidate: removing the fence still fails the real gate.
    candidate["changes"][0]["content"] = PREFIX.replace("install_p1_00_network_fence()\n", "") + "from api.routers import users\n"
    findings = p1_00_characterization_bootstrap_findings(candidate)
    assert len(findings) == 1 and "import and call install_p1_00_network_fence()" in findings[0]
    assert (task, sources) == before


@pytest.mark.parametrize("kind", ["existing", "non_characterization", "legacy"])
def test_unmatched_and_legacy_notes_keep_previous_contract(kind):
    path = P1 if kind != "non_characterization" else "tests/test_other.py"
    task, sources, _ = notes_fixture({path: 12000})
    if kind == "existing":
        sources[path] = {"sha256": "a" * 64, "content": "pass\n"}
    elif kind == "legacy":
        task["requirements"] = ["legacy"]
    before = copy.deepcopy((task, sources))
    assert "new_characterization_scaffold" not in preparation_notes(task, sources)["output_contract"]
    assert (task, sources) == before


def test_returned_policy_is_detached_and_deterministic():
    task, sources, _ = notes_fixture({P1: 12000})
    before = copy.deepcopy((task, sources))
    first = preparation_notes(task, sources)
    expected = copy.deepcopy(first)
    first["output_contract"]["new_characterization_scaffold"]["bootstrap_prefix"] = "unsafe\n"
    assert preparation_notes(task, sources) == expected
    assert (task, sources) == before


def test_complete_large_profile_and_scaffold_survive_bounded_advisory_trimming():
    paths = {f"tests/test_p1_00b_characterization_{'x' * 60}{i:02}.py": i + 1 for i in range(32)}
    task, sources, profile = notes_fixture(paths, total=160000)
    task["goal"] = " ".join(f"DR-{i}" for i in range(40))
    before = copy.deepcopy((task, sources))
    result = preparation_notes(task, sources)
    assert len(json.dumps(result).encode("utf-8")) <= 8192
    assert result["output_contract"]["proposal_utf8_budget_profile"] == profile
    assert result["output_contract"]["new_characterization_scaffold"]["bootstrap_prefix"] == PREFIX
    assert (task, sources) == before
