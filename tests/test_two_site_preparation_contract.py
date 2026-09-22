"""Hermetic unit tests for scripts/two_site_preparation_contract.py."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
import socket
import subprocess
import builtins
import pytest

from scripts.two_site_preparation_contract import (
    PreparationConfigError,
    PreparationStartup,
    assert_same_startup,
    preparation_telemetry,
)

VALID_CYCLE = "cycle-2026-20260922T140000Z-1"
VALID_HEAD = "45923cc017b14e6beae0b2c0963bc7b899153658"
VALID_ASSIGNMENT = "c7fe4c4caa5243f4cdafa1c5e0ff62038ae16e436fdb82877a23188a48b1dfae"


def _make_valid_startup(route: str = "one_shot") -> PreparationStartup:
    return PreparationStartup(
        schema_version=1,
        route=route,
        cycle_id=VALID_CYCLE,
        source_head=VALID_HEAD,
        assignment_sha256=VALID_ASSIGNMENT,
    )


class HostileStr(str):
    """String subclass instrumented to detect and prevent hostile calls."""

    def __new__(cls, val: str) -> HostileStr:
        obj = super().__new__(cls, val)
        obj.armed = False
        obj.hash_calls = 0
        obj.eq_calls = 0
        obj.repr_calls = 0
        return obj

    def __hash__(self) -> int:
        if getattr(self, "armed", False):
            self.hash_calls += 1
            raise AssertionError("Hostile __hash__ was invoked on armed str subclass")
        return super().__hash__()

    def __eq__(self, other: object) -> bool:
        if getattr(self, "armed", False):
            self.eq_calls += 1
            raise AssertionError("Hostile __eq__ was invoked on armed str subclass")
        return super().__eq__(other)

    def __repr__(self) -> str:
        if getattr(self, "armed", False):
            self.repr_calls += 1
            raise AssertionError("Hostile __repr__ was invoked on armed str subclass")
        return super().__repr__()


class HostileInt(int):
    """Integer subclass instrumented to detect hostile calls."""

    def __repr__(self) -> str:
        raise AssertionError("HostileInt __repr__ called")


class HostileMarker:
    """Object that throws on stringification, repr, or numeric conversion."""

    def __init__(self, marker: str = "LEAK_MARKER_998877") -> None:
        self.marker = marker

    def __str__(self) -> str:
        raise RuntimeError("HostileMarker __str__ called")

    def __repr__(self) -> str:
        raise RuntimeError("HostileMarker __repr__ called")

    def __eq__(self, other: object) -> bool:
        raise RuntimeError("HostileMarker __eq__ called")

    def __hash__(self) -> int:
        raise RuntimeError("HostileMarker __hash__ called")


def test_startup_exact_schema_and_types() -> None:
    s_one_shot = _make_valid_startup("one_shot")
    assert s_one_shot.schema_version == 1
    assert s_one_shot.route == "one_shot"
    assert s_one_shot.cycle_id == VALID_CYCLE
    assert s_one_shot.source_head == VALID_HEAD
    assert s_one_shot.assignment_sha256 == VALID_ASSIGNMENT

    s_legacy = _make_valid_startup("legacy")
    assert s_legacy.route == "legacy"

    # Test schema_version constraints
    for bad_ver in (True, False, 0, 2, -1, "1", 1.0, HostileInt(1)):
        with pytest.raises(PreparationConfigError):
            PreparationStartup(bad_ver, "one_shot", VALID_CYCLE, VALID_HEAD, VALID_ASSIGNMENT)
        with pytest.raises(PreparationConfigError):
            mapping = s_one_shot.to_mapping()
            mapping["schema_version"] = bad_ver
            PreparationStartup.from_mapping(mapping)

    # Test route constraints
    for bad_route in ("ONE_SHOT", "other", "", None, 1, HostileStr("one_shot")):
        with pytest.raises(PreparationConfigError):
            PreparationStartup(1, bad_route, VALID_CYCLE, VALID_HEAD, VALID_ASSIGNMENT)
        with pytest.raises(PreparationConfigError):
            mapping = s_one_shot.to_mapping()
            mapping["route"] = bad_route
            PreparationStartup.from_mapping(mapping)

    # Test cycle_id constraints
    for bad_cycle in (
        "cycle-2026-20260922T140000Z",
        "cycle-26-20260922T140000Z-1",
        "cycle-2026-2026092T140000Z-1",
        "cycle-2026-20260922T14000Z-1",
        "cycle-2026-20260922T140000Z-",
        "CYCLE-2026-20260922T140000Z-1",
        "cycle-2026-20260922T140000Z-" + ("9" * 60),
        HostileStr(VALID_CYCLE),
    ):
        with pytest.raises(PreparationConfigError):
            PreparationStartup(1, "one_shot", bad_cycle, VALID_HEAD, VALID_ASSIGNMENT)

    # Test source_head constraints (exact lowercase 40 hex)
    for bad_head in (
        "45923cc017b14e6beae0b2c0963bc7b89915365",
        "45923cc017b14e6beae0b2c0963bc7b8991536589",
        "45923CC017B14E6BEAE0B2C0963BC7B899153658",
        "g" * 40,
        HostileStr(VALID_HEAD),
    ):
        with pytest.raises(PreparationConfigError):
            PreparationStartup(1, "one_shot", VALID_CYCLE, bad_head, VALID_ASSIGNMENT)

    # Test assignment_sha256 constraints (exact lowercase 64 hex)
    for bad_assign in (
        "c7fe" * 15,
        "c7fe" * 17,
        "C7FE4C4CAA5243F4CDAFA1C5E0FF62038AE16E436FDB82877A23188A48B1DFAE",
        "x" * 64,
        HostileStr(VALID_ASSIGNMENT),
    ):
        with pytest.raises(PreparationConfigError):
            PreparationStartup(1, "one_shot", VALID_CYCLE, VALID_HEAD, bad_assign)

    # Subclasses of PreparationStartup are strictly forbidden
    class SubclassStartup(PreparationStartup):
        pass

    with pytest.raises(PreparationConfigError):
        SubclassStartup(1, "one_shot", VALID_CYCLE, VALID_HEAD, VALID_ASSIGNMENT)

    # Mapping type constraints
    for bad_map in ([], (), None, "not-a-dict", {1, 2, 3}):
        with pytest.raises(PreparationConfigError):
            PreparationStartup.from_mapping(bad_map)

    class SubclassDict(dict):
        pass

    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(SubclassDict(s_one_shot.to_mapping()))

    # Mapping key count constraints
    less_keys = s_one_shot.to_mapping()
    del less_keys["schema_version"]
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(less_keys)

    more_keys = s_one_shot.to_mapping()
    more_keys["extra"] = 123
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(more_keys)


def test_startup_frozen_copy_and_fingerprint() -> None:
    s = _make_valid_startup("one_shot")

    # Immutability
    with pytest.raises(FrozenInstanceError):
        s.route = "legacy"  # type: ignore[misc]

    # to_mapping returns a new copy
    m1 = s.to_mapping()
    assert type(m1) is dict
    assert len(m1) == 5
    m1["route"] = "legacy"
    assert s.route == "one_shot"
    assert s.to_mapping()["route"] == "one_shot"

    # from_mapping does not retain reference to input dict
    raw_input = s.to_mapping()
    s_from_raw = PreparationStartup.from_mapping(raw_input)
    raw_input["route"] = "legacy"
    assert s_from_raw.route == "one_shot"

    # Canonical fingerprint computation
    canonical_json = json.dumps(s.to_mapping(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    expected_fp = hashlib.sha256(canonical_json.encode("ascii")).hexdigest()
    assert s.fingerprint == expected_fp

    # Fingerprint sensitivity to every identity field
    s_legacy = _make_valid_startup("legacy")
    assert s_legacy.fingerprint != s.fingerprint

    s_diff_cycle = PreparationStartup(1, "one_shot", "cycle-2026-20260922T140000Z-2", VALID_HEAD, VALID_ASSIGNMENT)
    assert s_diff_cycle.fingerprint != s.fingerprint

    s_diff_head = PreparationStartup(1, "one_shot", VALID_CYCLE, "0" * 40, VALID_ASSIGNMENT)
    assert s_diff_head.fingerprint != s.fingerprint

    s_diff_assign = PreparationStartup(1, "one_shot", VALID_CYCLE, VALID_HEAD, "1" * 64)
    assert s_diff_assign.fingerprint != s.fingerprint


def test_startup_drift_rejected() -> None:
    s1 = _make_valid_startup("one_shot")
    s1_clone = PreparationStartup.from_mapping(s1.to_mapping())
    assert assert_same_startup(s1, s1) is None
    assert assert_same_startup(s1, s1_clone) is None

    # Invalid argument types
    for bad_inst in (None, s1.to_mapping(), "startup", 123):
        with pytest.raises(PreparationConfigError):
            assert_same_startup(bad_inst, s1)
        with pytest.raises(PreparationConfigError):
            assert_same_startup(s1, bad_inst)

    # Drift detection across each field independently
    s_drift_route = _make_valid_startup("legacy")
    with pytest.raises(PreparationConfigError):
        assert_same_startup(s1, s_drift_route)

    s_drift_cycle = PreparationStartup(1, "one_shot", "cycle-2026-20260922T140000Z-9", VALID_HEAD, VALID_ASSIGNMENT)
    with pytest.raises(PreparationConfigError):
        assert_same_startup(s1, s_drift_cycle)

    s_drift_head = PreparationStartup(1, "one_shot", VALID_CYCLE, "1234567890" * 4, VALID_ASSIGNMENT)
    with pytest.raises(PreparationConfigError):
        assert_same_startup(s1, s_drift_head)

    s_drift_assign = PreparationStartup(1, "one_shot", VALID_CYCLE, VALID_HEAD, "abcd" * 16)
    with pytest.raises(PreparationConfigError):
        assert_same_startup(s1, s_drift_assign)


def test_telemetry_unknown_not_zero() -> None:
    s = _make_valid_startup("one_shot")

    # Startup argument must be exact PreparationStartup
    with pytest.raises(PreparationConfigError):
        preparation_telemetry(None, outcome="TASK_READY")  # type: ignore[arg-type]
    with pytest.raises(PreparationConfigError):
        preparation_telemetry(s.to_mapping(), outcome="TASK_READY")  # type: ignore[arg-type]

    # All measurements omitted -> None and UNAVAILABLE, not zero
    t_none = preparation_telemetry(s, outcome="TASK_READY")
    assert t_none["schema_version"] == 1
    assert t_none["startup"] == s.to_mapping()
    assert t_none["startup_sha256"] == s.fingerprint
    assert t_none["outcome"] == "TASK_READY"
    assert t_none["elapsed_seconds"] is None
    assert t_none["source_file_count"] is None
    assert t_none["source_bytes"] is None
    assert t_none["task_bytes"] is None
    assert t_none["issue_count"] is None
    assert t_none["measurement_state"] == "UNAVAILABLE"
    assert t_none["efficiency"] == "UNKNOWN"

    # Partial measurements
    t_partial = preparation_telemetry(s, outcome="TASK_BLOCKED", elapsed_seconds=2.5)
    assert t_partial["elapsed_seconds"] == 2.5
    assert t_partial["source_file_count"] is None
    assert t_partial["measurement_state"] == "PARTIAL"

    # Valid 0 must remain 0 and not become None
    t_zeros = preparation_telemetry(
        s,
        outcome="ERROR",
        elapsed_seconds=0,
        source_file_count=0,
        source_bytes=0,
        task_bytes=0,
        issue_count=0,
    )
    assert t_zeros["elapsed_seconds"] == 0
    assert type(t_zeros["elapsed_seconds"]) is int
    assert t_zeros["source_file_count"] == 0
    assert t_zeros["source_bytes"] == 0
    assert t_zeros["task_bytes"] == 0
    assert t_zeros["issue_count"] == 0
    assert t_zeros["measurement_state"] == "COMPLETE"

    # Float 0.0 preserved
    t_float_zero = preparation_telemetry(s, outcome="UNKNOWN", elapsed_seconds=0.0)
    assert t_float_zero["elapsed_seconds"] == 0.0
    assert type(t_float_zero["elapsed_seconds"]) is float
    assert t_float_zero["measurement_state"] == "PARTIAL"

    # Invalid outcome maps to UNKNOWN
    for bad_outcome in ("READY", "OK", "FAILED", "", 123, None, True):
        t_bad = preparation_telemetry(s, outcome=bad_outcome)
        assert t_bad["outcome"] == "UNKNOWN"


def test_telemetry_invalid_scalar_boundaries() -> None:
    s = _make_valid_startup("one_shot")
    max_safe_int = 1 << 53
    max_count = (1 << 63) - 1
    huge_int = 10**5000

    # Elapsed seconds boundaries
    assert preparation_telemetry(s, outcome="TASK_READY", elapsed_seconds=max_safe_int)["elapsed_seconds"] == max_safe_int
    assert preparation_telemetry(s, outcome="TASK_READY", elapsed_seconds=max_safe_int + 1)["elapsed_seconds"] is None
    assert preparation_telemetry(s, outcome="TASK_READY", elapsed_seconds=float(max_safe_int))["elapsed_seconds"] == float(max_safe_int)

    for bad_elapsed in (True, False, -1, -0.01, float("nan"), float("inf"), float("-inf"), huge_int, HostileInt(5)):
        res = preparation_telemetry(s, outcome="TASK_READY", elapsed_seconds=bad_elapsed)
        assert res["elapsed_seconds"] is None

    # Counts boundaries
    for count_key in ("source_file_count", "source_bytes", "task_bytes", "issue_count"):
        valid_kwargs = {count_key: max_count}
        assert preparation_telemetry(s, outcome="TASK_READY", **valid_kwargs)[count_key] == max_count

        bound_plus_one_kwargs = {count_key: max_count + 1}
        assert preparation_telemetry(s, outcome="TASK_READY", **bound_plus_one_kwargs)[count_key] is None

        for bad_count in (True, False, -1, 1.0, float("nan"), float("inf"), huge_int, HostileInt(10)):
            bad_kwargs = {count_key: bad_count}
            assert preparation_telemetry(s, outcome="TASK_READY", **bad_kwargs)[count_key] is None


def test_source_free_bounded_without_io() -> None:
    s = _make_valid_startup("one_shot")
    secret_token = "LEAK_MARKER_FORBIDDEN_TOKEN_8899"
    hostile_target = HostileMarker(secret_token)

    # Pass hostile marker to outcome and every measurement
    t_hostile = preparation_telemetry(
        s,
        outcome=hostile_target,
        elapsed_seconds=hostile_target,
        source_file_count=hostile_target,
        source_bytes=hostile_target,
        task_bytes=hostile_target,
        issue_count=hostile_target,
    )
    assert t_hostile["outcome"] == "UNKNOWN"
    assert t_hostile["elapsed_seconds"] is None
    assert t_hostile["source_file_count"] is None
    assert t_hostile["source_bytes"] is None
    assert t_hostile["task_bytes"] is None
    assert t_hostile["issue_count"] is None
    assert t_hostile["measurement_state"] == "UNAVAILABLE"

    serialized_hostile = json.dumps(t_hostile) + "\n"
    assert len(serialized_hostile.encode("utf-8")) <= 4096
    assert secret_token not in serialized_hostile

    # Install traps for open, socket.connect, subprocess.Popen and verify env invariance
    def trap_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("I/O trap: open() invoked during execution")

    def trap_connect(*args: object, **kwargs: object) -> object:
        raise AssertionError("I/O trap: socket.connect() invoked during execution")

    def trap_popen(*args: object, **kwargs: object) -> object:
        raise AssertionError("I/O trap: subprocess.Popen() invoked during execution")

    orig_open = builtins.open
    orig_connect = socket.socket.connect
    orig_popen = subprocess.Popen

    try:
        builtins.open = trap_open  # type: ignore[assignment]
        socket.socket.connect = trap_connect  # type: ignore[assignment]
        subprocess.Popen = trap_popen  # type: ignore[assignment]

        res1 = preparation_telemetry(
            s,
            outcome="TASK_READY",
            elapsed_seconds=12.5,
            source_file_count=42,
            source_bytes=1024,
            task_bytes=2048,
            issue_count=0,
        )

        orig_env = os.environ.copy()
        try:
            os.environ["ROUTE"] = "hostile_toggle"
            os.environ["CYCLE_ID"] = "cycle-bogus"
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
            os.environ["PREPARATION_FALLBACK"] = "true"

            res2 = preparation_telemetry(
                s,
                outcome="TASK_READY",
                elapsed_seconds=12.5,
                source_file_count=42,
                source_bytes=1024,
                task_bytes=2048,
                issue_count=0,
            )
            assert res1 == res2
            serialized_valid = json.dumps(res1) + "\n"
            assert len(serialized_valid.encode("utf-8")) <= 4096
        finally:
            os.environ.clear()
            os.environ.update(orig_env)
    finally:
        builtins.open = orig_open  # type: ignore[assignment]
        socket.socket.connect = orig_connect  # type: ignore[assignment]
        subprocess.Popen = orig_popen  # type: ignore[assignment]


def test_startup_hostile_key_before_hash() -> None:
    # Case 1: str subclass key for 'route' whose methods are armed after dict build
    k_route = HostileStr("route")
    raw1 = {
        "schema_version": 1,
        k_route: "one_shot",
        "cycle_id": VALID_CYCLE,
        "source_head": VALID_HEAD,
        "assignment_sha256": VALID_ASSIGNMENT,
    }
    k_route.armed = True
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(raw1)
    assert k_route.hash_calls == 0
    assert k_route.eq_calls == 0
    assert k_route.repr_calls == 0

    # Case 2: Unexpected hostile key
    k_extra = HostileStr("unexpected_key")
    raw2 = {
        "schema_version": 1,
        "route": "one_shot",
        "cycle_id": VALID_CYCLE,
        "source_head": VALID_HEAD,
        k_extra: "val",
    }
    k_extra.armed = True
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(raw2)
    assert k_extra.hash_calls == 0
    assert k_extra.eq_calls == 0
    assert k_extra.repr_calls == 0

    # Case 3: Hostile str value subclass
    v_route = HostileStr("one_shot")
    raw3 = {
        "schema_version": 1,
        "route": v_route,
        "cycle_id": VALID_CYCLE,
        "source_head": VALID_HEAD,
        "assignment_sha256": VALID_ASSIGNMENT,
    }
    v_route.armed = True
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(raw3)
    assert v_route.hash_calls == 0
    assert v_route.eq_calls == 0
    assert v_route.repr_calls == 0

    # Direct construction with hostile value subclass
    with pytest.raises(PreparationConfigError):
        PreparationStartup(1, v_route, VALID_CYCLE, VALID_HEAD, VALID_ASSIGNMENT)
    assert v_route.hash_calls == 0
    assert v_route.eq_calls == 0
    assert v_route.repr_calls == 0

    # Case 4: Mapping with len != 5 rejects on length without iterating keys
    k_hostile = HostileStr("hostile_key")
    raw4 = {
        "schema_version": 1,
        "route": "one_shot",
        "cycle_id": VALID_CYCLE,
        "source_head": VALID_HEAD,
        "assignment_sha256": VALID_ASSIGNMENT,
        k_hostile: "extra",
    }
    k_hostile.armed = True
    with pytest.raises(PreparationConfigError):
        PreparationStartup.from_mapping(raw4)
    assert k_hostile.hash_calls == 0
    assert k_hostile.eq_calls == 0
    assert k_hostile.repr_calls == 0
