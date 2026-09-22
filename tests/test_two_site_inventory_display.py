"""Hermetic tests for pure inventory display formatter and CLI adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest.mock

import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from scripts.two_site_cycle_tools import (
    inspect_canonical_evidence,
    inventory_view,
    main,
)
from scripts.two_site_inventory_display import (
    error_report,
    format_inventory,
    valid_request,
)
from tests.test_two_site_canonical_evidence_inspect import (
    _make_canonical_repo,
    _run_git,
    canonical_repo,
)


@pytest.mark.parametrize(
    "b_id,offset,limit,expected",
    [
        (None, 0, 64, True),
        ("VALID_ID", 10, 128, True),
        (None, -1, 64, False),
        (None, 2**63, 64, False),
        (None, True, 64, False),
        (None, 0, 0, False),
        (None, 0, 129, False),
        (None, 0, True, False),
        (123, 0, 64, False),
        ("", 0, 64, False),
        ("A" * 32769, 0, 64, False),
    ],
)
def test_exact_request_and_envelope_types(canonical_repo: Path, b_id, offset, limit, expected):
    assert valid_request(b_id, offset, limit) is expected

    class SubDict(dict):
        def items(self):
            raise AssertionError("SubDict.items executed")

        def keys(self):
            raise AssertionError("SubDict.keys executed")

        def values(self):
            raise AssertionError("SubDict.values executed")

    bad_raw = SubDict()
    bad_raw["status"] = "VALID"
    res1 = format_inventory(bad_raw)
    assert res1["status"] == "INVENTORY_INCONSISTENT"

    raw_sub_cov = {
        "status": "VALID",
        "expected_seed_count": 0,
        "seed_count": 0,
        "coverage": SubDict(),
    }
    res2 = format_inventory(raw_sub_cov)
    assert res2["status"] == "INVENTORY_INCONSISTENT"

    raw_unhashable_status = {"status": ["VALID"], "expected_seed_count": 0, "seed_count": 0, "coverage": {}}
    res3 = format_inventory(raw_unhashable_status)
    assert res3["status"] == "INVENTORY_INCONSISTENT"

    raw_unhashable_cov_val = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {"BEH_1": ["PROVEN_FOR_REQUESTED_SCENARIO"]},
    }
    res4 = format_inventory(raw_unhashable_cov_val)
    assert res4["status"] == "INVENTORY_INCONSISTENT"

    raw_unknown = {"status": "UNKNOWN_CUSTOM_STATUS", "expected_seed_count": 0, "seed_count": 0, "coverage": {}}
    res5 = format_inventory(raw_unknown)
    assert res5["status"] == "INVENTORY_INCONSISTENT"

    with unittest.mock.patch("scripts.two_site_cycle_tools.inspect_canonical_evidence") as mock_cls:
        out = inventory_view(canonical_repo, offset=-1)
        assert out["status"] == "INVENTORY_INCONSISTENT"
        mock_cls.assert_not_called()


def test_full_and_targeted_cardinality():
    raw_missing = {
        "status": "VALID",
        "expected_seed_count": 2,
        "seed_count": 2,
        "coverage": {"BEH_1": "UNKNOWN"},
    }
    assert format_inventory(raw_missing)["status"] == "INVENTORY_INCONSISTENT"

    raw_extra = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {"BEH_1": "UNKNOWN", "BEH_2": "UNKNOWN"},
    }
    assert format_inventory(raw_extra)["status"] == "INVENTORY_INCONSISTENT"

    raw_wrong_target = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "requested_coverage": "PROVEN_FOR_REQUESTED_SCENARIO",
        "coverage": {"OTHER_BEH": "PROVEN_FOR_REQUESTED_SCENARIO"},
    }
    assert format_inventory(raw_wrong_target, behavior_id="TARGET_BEH")["status"] == "INVENTORY_INCONSISTENT"

    raw_mismatch_state = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "requested_coverage": "PROVEN_FOR_REQUESTED_SCENARIO",
        "coverage": {"TARGET_BEH": "UNKNOWN"},
    }
    assert format_inventory(raw_mismatch_state, behavior_id="TARGET_BEH")["status"] == "INVENTORY_INCONSISTENT"

    raw_nonempty_nomatch = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "requested_coverage": "NO_MATCHING_EVIDENCE",
        "coverage": {"TARGET_BEH": "UNKNOWN"},
    }
    assert format_inventory(raw_nonempty_nomatch, behavior_id="TARGET_BEH")["status"] == "INVENTORY_INCONSISTENT"

    raw_zero_seed = {
        "status": "VALID",
        "expected_seed_count": 0,
        "seed_count": 0,
        "requested_coverage": "PROVEN_FOR_REQUESTED_SCENARIO",
        "coverage": {"TARGET_BEH": "PROVEN_FOR_REQUESTED_SCENARIO"},
    }
    assert format_inventory(raw_zero_seed, behavior_id="TARGET_BEH")["status"] == "INVENTORY_INCONSISTENT"

    raw_valid = {
        "status": "VALID",
        "expected_seed_count": 4,
        "seed_count": 4,
        "coverage": {
            "B1": "PROVEN_FOR_REQUESTED_SCENARIO",
            "B2": "PARTIAL_OR_SEED_ONLY",
            "B3": "UNKNOWN",
            "B4": "PARTIAL_OR_SEED_ONLY",
        },
    }
    raw_copy = json.loads(json.dumps(raw_valid))
    rep = format_inventory(raw_valid)
    assert rep["status"] == "VALID"
    assert rep["coverage"] == raw_copy["coverage"]
    assert raw_valid == raw_copy


def test_budget_reduction_lossless_unicode_pages():
    cov = {}
    for i in range(25):
        key = f"ID_{i:02d}_" + "گزارش_حسابداری_\u200c_تست_" * 150
        cov[key] = "PROVEN_FOR_REQUESTED_SCENARIO"
    raw = {
        "status": "VALID",
        "expected_seed_count": 25,
        "seed_count": 25,
        "coverage": cov,
    }
    orig_cov = dict(cov)

    first_page = format_inventory(raw, offset=0, limit=20)
    assert first_page["status"] == "VALID"
    assert first_page["returned_entries"] < 20

    offset = 0
    collected: dict[str, str] = {}
    while True:
        page = format_inventory(raw, offset=offset, limit=20)
        assert page["status"] == "VALID"
        raw_bytes = (json.dumps(page) + "\n").encode("utf-8")
        assert len(raw_bytes) <= 32768
        for k, v in page["coverage"].items():
            assert k not in collected
            collected[k] = v
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]

    assert collected == orig_cov
    assert raw["coverage"] == orig_cov


def test_single_entry_exact_budget_boundary():
    dummy_key = "K"
    dummy_raw = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {dummy_key: "UNKNOWN"},
    }
    rep_dummy = format_inventory(dummy_raw, offset=0, limit=1)
    assert rep_dummy["status"] == "VALID"
    base_len = len((json.dumps(rep_dummy) + "\n").encode("utf-8")) - len(dummy_key)

    exact_k_len = 32768 - base_len
    exact_key = "A" * exact_k_len
    exact_raw = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {exact_key: "UNKNOWN"},
    }
    rep_exact = format_inventory(exact_raw, offset=0, limit=1)
    assert rep_exact["status"] == "VALID"
    assert len((json.dumps(rep_exact) + "\n").encode("utf-8")) == 32768

    over_key = "A" * (exact_k_len + 1)
    over_raw = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {over_key: "UNKNOWN"},
    }
    rep_over = format_inventory(over_raw, offset=0, limit=1)
    assert rep_over["status"] == "INVENTORY_INCONSISTENT"

    supp_key = "\U0001F600" * 3000
    assert len(supp_key) == 3000 < 32768
    supp_raw = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {supp_key: "UNKNOWN"},
    }
    rep_supp = format_inventory(supp_raw, offset=0, limit=1)
    assert rep_supp["status"] == "INVENTORY_INCONSISTENT"


def test_nonfirst_huge_key_never_serialized(monkeypatch: pytest.MonkeyPatch):
    orig_dumps = json.dumps
    huge_key = "M" * 1_000_000

    def guarded_dumps(obj, *args, **kwargs):
        def _check(o):
            if isinstance(o, str):
                assert len(o) < 1_000_000, "json.dumps called with huge string"
            elif isinstance(o, dict):
                for k, v in o.items():
                    _check(k)
                    _check(v)
            elif isinstance(o, (list, tuple)):
                for item in o:
                    _check(item)

        _check(obj)
        return orig_dumps(obj, *args, **kwargs)

    monkeypatch.setattr("scripts.two_site_inventory_display.json.dumps", guarded_dumps)

    raw_nonfirst = {
        "status": "VALID",
        "expected_seed_count": 2,
        "seed_count": 2,
        "coverage": {"NORMAL_KEY": "UNKNOWN", huge_key: "UNKNOWN"},
    }
    rep = format_inventory(raw_nonfirst, offset=0, limit=64)
    assert rep["status"] == "INVENTORY_INCONSISTENT"

    raw_first = {
        "status": "VALID",
        "expected_seed_count": 1,
        "seed_count": 1,
        "coverage": {huge_key: "UNKNOWN"},
    }
    rep2 = format_inventory(raw_first, offset=0, limit=64)
    assert rep2["status"] == "INVENTORY_INCONSISTENT"


def test_empty_endpoints_and_long_errors():
    raw_empty = {
        "status": "VALID",
        "expected_seed_count": 0,
        "seed_count": 0,
        "requested_coverage": "NO_MATCHING_EVIDENCE",
        "coverage": {},
    }
    rep_empty = format_inventory(raw_empty, behavior_id="NONE_BEH")
    assert rep_empty["status"] == "VALID"
    assert rep_empty["total_entries"] == 0
    assert rep_empty["returned_entries"] == 0
    assert rep_empty["omitted_entries"] == 0
    assert rep_empty["next_offset"] is None
    assert rep_empty["coverage"] == {}

    raw_two = {
        "status": "VALID",
        "expected_seed_count": 2,
        "seed_count": 2,
        "coverage": {"K1": "UNKNOWN", "K2": "UNKNOWN"},
    }
    rep_eq = format_inventory(raw_two, offset=2, limit=64)
    assert rep_eq["status"] == "VALID"
    assert rep_eq["returned_entries"] == 0
    assert rep_eq["omitted_entries"] == 2
    assert rep_eq["next_offset"] is None
    assert rep_eq["coverage"] == {}

    rep_gt = format_inventory(raw_two, offset=10, limit=64)
    assert rep_gt["status"] == "VALID"
    assert rep_gt["returned_entries"] == 0
    assert rep_gt["omitted_entries"] == 2
    assert rep_gt["next_offset"] is None
    assert rep_gt["coverage"] == {}

    rep_maxint = format_inventory(raw_two, offset=2**63 - 1, limit=64)
    assert rep_maxint["status"] == "VALID"
    assert rep_maxint["next_offset"] is None

    rep_maxint_plus = format_inventory(raw_two, offset=2**63, limit=64)
    assert rep_maxint_plus["status"] == "INVENTORY_INCONSISTENT"

    long_reason = "هشدار_سامانه_\u200c_طولانی_" * 50
    assert len(long_reason) > 512
    err_rep = error_report(long_reason)
    assert err_rep["status"] == "INVENTORY_INCONSISTENT"
    assert len(err_rep["reason"]) == 512
    assert err_rep["reason_truncated"] is True
    assert len((json.dumps(err_rep) + "\n").encode("utf-8")) <= 32768

    err_cls = error_report("classifier execution failed")
    assert err_cls["status"] == "INVENTORY_INCONSISTENT"
    assert len((json.dumps(err_cls) + "\n").encode("utf-8")) <= 32768


def test_real_classifier_cli_is_read_only(canonical_repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    head_before = _run_git(canonical_repo, "rev-parse", "HEAD")
    status_before = _run_git(canonical_repo, "status", "--porcelain=v1")
    diff_before = _run_git(canonical_repo, "diff")

    v_full = inventory_view(canonical_repo)
    assert v_full["status"] == "VALID"
    assert v_full["seed_count"] == 4

    v_proven = inventory_view(canonical_repo, "API-DEMO-SEED-PROVEN")
    assert v_proven["status"] == "VALID"
    assert v_proven["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"

    v_partial = inventory_view(canonical_repo, "BOT-DEMO-SEED-PARTIAL")
    assert v_partial["status"] == "VALID"
    assert v_partial["requested_coverage"] == "PARTIAL_OR_SEED_ONLY"

    v_unknown = inventory_view(canonical_repo, "WEB-DEMO-SEED-UNKNOWN")
    assert v_unknown["status"] == "VALID"
    assert v_unknown["requested_coverage"] == "UNKNOWN"

    v_nomatch = inventory_view(canonical_repo, "ABSENT-BEHAVIOR-DOES-NOT-EXIST")
    assert v_nomatch["status"] == "VALID"
    assert v_nomatch["requested_coverage"] == "NO_MATCHING_EVIDENCE"

    monkeypatch.setattr(
        sys,
        "argv",
        ["two_site_cycle_tools.py", "--root", str(canonical_repo), "inventory", "--behavior-id", "API-DEMO-SEED-PROVEN"],
    )
    rc = main()
    assert rc == 0
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["status"] == "VALID"
    assert (len(out) + 1) <= 32768

    monkeypatch.setattr(
        sys,
        "argv",
        ["two_site_cycle_tools.py", "--root", str(canonical_repo), "inventory", "--offset", "-1"],
    )
    rc_err = main()
    assert rc_err != 0
    out_err = capsys.readouterr().out.strip()
    data_err = json.loads(out_err)
    assert data_err["status"] == "INVENTORY_INCONSISTENT"

    assert _run_git(canonical_repo, "rev-parse", "HEAD") == head_before
    assert _run_git(canonical_repo, "status", "--porcelain=v1") == status_before
    assert _run_git(canonical_repo, "diff") == diff_before


def test_standalone_inventory_cli(tmp_path: Path):
    repo = _make_canonical_repo(tmp_path / "standalone_repo")
    script = Path(__file__).resolve().parents[1] / "scripts" / "two_site_cycle_tools.py"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }

    cmd = [
        sys.executable,
        str(script),
        "--root",
        str(repo),
        "inventory",
        "--behavior-id",
        "API-DEMO-SEED-PROVEN",
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert proc.returncode == 0
    line = proc.stdout.strip()
    data = json.loads(line)
    assert data["status"] == "VALID"
    assert data["schema_version"] == 1
    assert data["display_only"] is True
    assert data["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"
    assert len((line + "\n").encode("utf-8")) <= 32768

    cmd_err = [
        sys.executable,
        str(script),
        "--root",
        str(repo),
        "inventory",
        "--offset",
        "-1",
    ]
    proc_err = subprocess.run(cmd_err, env=env, capture_output=True, text=True)
    assert proc_err.returncode != 0
    data_err = json.loads(proc_err.stdout.strip())
    assert data_err["status"] == "INVENTORY_INCONSISTENT"
