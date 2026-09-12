"""Foundational tests for pure semantic-identity primitive."""
from __future__ import annotations

import copy
import pytest
from scripts.two_site_semantic_task import (
    SemanticTaskError, build_identity, validate_identity,
)

STAGE = "stage-1"
GAP = "specs/spec.md#gap-1"
SCENARIO = "scenario-alpha"
SYMBOLS = [
    {"path": "a/b.py", "symbols": ["sym_b", "sym_a"]},
    {"path": "c/d.py", "symbols": ["func_2", "func_1"]},
]
MANIFEST = {"c/d.py": "1" * 64, "a/b.py": "0" * 64}


def test_deterministic_reordering_equivalence():
    ident1 = build_identity(STAGE, GAP, SCENARIO, SYMBOLS, MANIFEST)
    reordered_syms = [
        {"path": "c/d.py", "symbols": ["func_1", "func_2"]},
        {"path": "a/b.py", "symbols": ["sym_a", "sym_b"]},
    ]
    reordered_manifest = {"a/b.py": "0" * 64, "c/d.py": "1" * 64}
    ident2 = build_identity(STAGE, GAP, SCENARIO, reordered_syms, reordered_manifest)
    assert ident1 == ident2
    assert ident1["fingerprint"] == ident2["fingerprint"]
    assert validate_identity(ident1) == ident1


@pytest.mark.parametrize("kw,val", [
    ("stage_id", "stage-2"),
    ("gap_reference", "specs/spec.md#gap-2"),
    ("scenario_key", "scenario-beta"),
    ("symbol_groups", [{"path": "a/b.py", "symbols": ["other_sym"]}]),
    ("source_manifest", {"a/b.py": "2" * 64}),
])
def test_fingerprint_sensitivity(kw, val):
    base_args = dict(stage_id=STAGE, gap_reference=GAP, scenario_key=SCENARIO,
                     symbol_groups=SYMBOLS, source_manifest=MANIFEST)
    ident_base = build_identity(**base_args)
    ident_alt = build_identity(**dict(base_args, **{kw: val}))
    assert ident_base["fingerprint"] != ident_alt["fingerprint"]
    if kw == "source_manifest":
        assert ident_base["source_sha256"] != ident_alt["source_sha256"]


def test_null_source_stability_and_absence_of_task_id_head():
    manifest_null = {"a/b.py": None, "c/d.py": "0" * 64}
    ident1 = build_identity(STAGE, GAP, SCENARIO, SYMBOLS, manifest_null)
    ident2 = build_identity(STAGE, GAP, SCENARIO, SYMBOLS, manifest_null)
    assert ident1 == ident2 and ident1["source_manifest"]["a/b.py"] is None
    assert validate_identity(ident1) == ident1
    assert not any(k in ident1 for k in ("task_id", "git_head", "head", "HEAD"))
    assert set(ident1.keys()) == {
        "schema_version", "stage_id", "gap_reference", "scenario_key",
        "symbol_groups", "source_sha256", "source_manifest", "fingerprint",
    }


@pytest.mark.parametrize("kw,val", [
    ("stage_id", ""), ("stage_id", "_bad_start"), ("stage_id", "has space"), ("stage_id", "a" * 129),
    ("scenario_key", ".bad_start"), ("scenario_key", "bad*char"),
    ("gap_reference", ""), ("gap_reference", "no_anchor.md"), ("gap_reference", "path.md#"),
    ("gap_reference", "p.md#a#b"), ("gap_reference", "/abs.md#a"), ("gap_reference", "a/./b.md#a"),
    ("gap_reference", "a/../b.md#a"), ("gap_reference", "a//b.md#a"), ("gap_reference", "a\\b.md#a"),
    ("gap_reference", " a.md#a"),
    ("symbol_groups", tuple(SYMBOLS)),
    ("symbol_groups", [{"path": "a.py", "symbols": ("s1",)}]),
    ("symbol_groups", [{"path": "/abs.py", "symbols": ["foo"]}]),
    ("symbol_groups", [{"path": "a/./b.py", "symbols": ["foo"]}]),
    ("symbol_groups", [{"path": "a.py", "symbols": ["1_bad"]}]),
    ("symbol_groups", [{"path": "a.py", "symbols": ["bad-sym"]}]),
    ("symbol_groups", [{"path": "a.py", "symbols": []}]),
    ("symbol_groups", [{"path": "a.py", "symbols": ["dup", "dup"]}]),
    ("symbol_groups", [{"path": "a.py", "symbols": ["s1"]}, {"path": "a.py", "symbols": ["s2"]}]),
    ("source_manifest", {}), ("source_manifest", {"/abs.py": "0" * 64}),
    ("source_manifest", {123: "0" * 64}), ("source_manifest", {"a/b.py": "0" * 64, 123: "0" * 64}),
    ("source_manifest", {"a/b.py": "0" * 63}), ("source_manifest", {"a/b.py": "0" * 65}),
    ("source_manifest", {"a/b.py": "A" * 64}), ("source_manifest", {"a/b.py": 123}),
])
def test_build_identity_invalid_inputs(kw, val):
    args = dict(stage_id=STAGE, gap_reference=GAP, scenario_key=SCENARIO,
                symbol_groups=SYMBOLS, source_manifest=MANIFEST)
    args[kw] = val
    with pytest.raises(SemanticTaskError):
        build_identity(**args)


@pytest.mark.parametrize("mutator", [
    lambda d: d.pop("fingerprint"),
    lambda d: d.update(task_id="task-123"),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(fingerprint="0" * 64),
    lambda d: d.update(source_sha256="0" * 64),
    lambda d: d.update(stage_id="stage-tampered"),
    lambda d: d.update(symbol_groups=tuple(d["symbol_groups"])),
    lambda d: d.update(symbol_groups=[{"path": 123, "symbols": ["sym_a"]}]),
    lambda d: d.update(symbol_groups=[{"path": "a/b.py", "symbols": ["sym_a"]}, {"path": 123, "symbols": ["sym_b"]}]),
    lambda d: d.update(symbol_groups=list(reversed(d["symbol_groups"]))),
    lambda d: d["symbol_groups"][0].update(symbols=tuple(d["symbol_groups"][0]["symbols"])),
    lambda d: d["symbol_groups"][0].update(symbols=[123, "sym_a"]),
    lambda d: d["symbol_groups"][0].update(symbols=list(reversed(d["symbol_groups"][0]["symbols"]))),
    lambda d: d.update(source_manifest={123: "0" * 64}),
    lambda d: d.update(source_manifest={"a/b.py": "0" * 64, 123: "0" * 64}),
    lambda d: d.update(source_manifest={k: d["source_manifest"][k] for k in reversed(list(d["source_manifest"].keys()))}),
])
def test_validate_identity_tampering_and_noncanonical(mutator):
    base = build_identity(STAGE, GAP, SCENARIO, SYMBOLS, MANIFEST)
    bad = copy.deepcopy(base)
    mutator(bad)
    with pytest.raises(SemanticTaskError):
        validate_identity(bad)


def test_validate_identity_detached_copy():
    base = build_identity(STAGE, GAP, SCENARIO, SYMBOLS, MANIFEST)
    validated = validate_identity(base)
    assert validated == base
    validated["symbol_groups"][0]["symbols"].append("new_sym")
    assert validated != base
