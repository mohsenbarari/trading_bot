"""Owned SQLite cardinality/byte witnesses; never CLI/auth/session discovery.

Existing independent wire/role/hooks fixture is imported as a module, not an
aliased TestCase. Its intentionally unconstrained main table permits malformed
duplicate rows; no duplicate insertion into a real PRIMARY KEY is claimed.
"""
from __future__ import annotations
import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import hashlib
from pathlib import Path
import sqlite3
from unittest import mock
import pytest
from scripts import two_site_proposal_runtime_profile as profile
from scripts.two_site_antigravity_session import message, wire_fields
from tests import test_two_site_runtime_proposal_profile as fixture

MAIN_LIMIT, EXECUTOR_LIMIT = 262144, 524288
IDENTITY_ERROR = "proposal runtime database identity mismatch"
EXECUTOR_ERROR = "proposal runtime executor evidence missing"


@pytest.fixture
def lab():
    case = fixture.RuntimeProfileContracts("runTest")
    case.setUp()
    try:
        replace(case, [("main", case.oracle)], [case.executor()])
        yield case
    finally:
        case.doCleanups()


def snapshot(case):
    result = []
    for path in sorted(case.root.rglob("*")):
        state = path.lstat()
        payload = path.read_bytes() if path.is_file() else b""
        result.append((str(path.relative_to(case.root)), state.st_dev, state.st_ino,
            state.st_mode, state.st_nlink, state.st_uid, state.st_size,
            state.st_mtime_ns, state.st_ctime_ns, hashlib.sha256(payload).hexdigest()))
    return tuple(result)


def replace(case, main, executors):
    with sqlite3.connect(case.db_path) as db:
        db.execute("DELETE FROM trajectory_metadata_blob")
        db.execute("DELETE FROM executor_metadata")
        db.executemany("INSERT INTO trajectory_metadata_blob VALUES(?,?)", main)
        db.executemany("INSERT INTO executor_metadata VALUES(?)", [(row,) for row in executors])


def call(case, *, fresh=False):
    """Forward real reads after exact owned-path/readonly checks, never fake DBs."""
    before = snapshot(case)
    original_connect, original_regular = sqlite3.connect, profile._regular
    connections, reads, statements = [], [], []
    allowed = {case.source, case.hooks, case.db_path}
    def connect(database, *args, **kwargs):
        assert database == case.db_path.as_uri() + "?mode=ro"
        assert not args and kwargs == {"uri": True, "timeout": 5}
        connections.append(database)
        db = original_connect(database, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    def regular(path, limit):
        assert path in allowed and path.is_relative_to(case.root)
        reads.append(path)
        return original_regular(path, limit)
    try:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sqlite3, "connect", side_effect=connect))
            stack.enter_context(mock.patch.object(profile, "_regular", side_effect=regular))
            stack.enter_context(mock.patch.object(Path, "home", side_effect=AssertionError("no live home")))
            stack.enter_context(mock.patch.object(profile.subprocess, "Popen", side_effect=AssertionError("no CLI")))
            stack.enter_context(mock.patch.object(profile.subprocess, "run", side_effect=AssertionError("no CLI")))
            return profile.validate_profile(case.candidate, fresh=fresh, cli_home=case.home)
    finally:
        assert connections == [case.db_path.as_uri() + "?mode=ro"]
        assert set(reads) == allowed
        assert statements[:2] == ["PRAGMA query_only=ON", "BEGIN"]
        assert snapshot(case) == before


def healthy(case, *, fresh=False):
    expected = {"conversation_id": fixture.CID, "name": "two-site-proposal-only",
                "native_tools": 0, "command_policy": "off", "actual_executor_checked": not fresh}
    assert call(case, fresh=fresh) == expected
    assert case.oracle == fixture.literal_metadata(case.work, case.body)
    assert case.source.read_text() == profile.DEFINITION
    assert case.hooks.read_bytes() == fixture.HOOK_BYTES


def reject_then_restore(case, *, main=None, executors=None, fresh=False, error):
    healthy(case, fresh=fresh)
    good_main, good_executors = [("main", case.oracle)], [case.executor()]
    replace(case, good_main if main is None else main,
            good_executors if executors is None else executors)
    try:
        with pytest.raises(profile.ProposalProfileError) as caught:
            call(case, fresh=fresh)
        assert str(caught.value) == error
    finally:
        replace(case, good_main, good_executors)
    healthy(case, fresh=fresh)


def padded_opaque(data, number, size):
    """Independent fixture encoding; change only an already-opaque byte field."""
    pairs = [(key, value) for key, _wire, value in wire_fields(data)]
    assert sum(key == number for key, _value in pairs) == 1
    def pack(length):
        return message(*[(key, b"X" * length if key == number else value) for key, value in pairs])
    maximum = size - len(pack(0))
    for length in range(max(0, maximum - 8), maximum + 1):
        encoded = pack(length)
        if len(encoded) == size:
            return encoded
    raise AssertionError("fixture could not reach exact physical byte boundary")


@pytest.mark.parametrize("fresh", (True, False))
def test_all_main_rows_count_before_payload_filter_in_both_phases(lab, fresh):
    # Explicit malformed-schema witness, not an impossible PRIMARY KEY insert.
    with sqlite3.connect(lab.db_path) as db:
        columns = db.execute("PRAGMA table_info(trajectory_metadata_blob)").fetchall()
    assert [(row[1], row[5]) for row in columns] == [("id", 0), ("data", 0)]
    large = padded_opaque(lab.oracle, 1, MAIN_LIMIT + 1)
    variants = [[], [("main", large)], [("main", lab.oracle), ("main", large)],
                [("main", lab.oracle), ("main", lab.oracle)], [("main", None)],
                [("main", "not a blob")], [("main", 17)], [("main", 1.5)]]
    for main in variants:
        reject_then_restore(lab, main=main, fresh=fresh, error=IDENTITY_ERROR)


@pytest.mark.parametrize("count", range(1, 9))
def test_every_executor_at_each_permitted_count_is_actually_validated(lab, count):
    healthy(lab)
    good = lab.executor()
    replace(lab, [("main", lab.oracle)], [good] * count)
    original = profile.validate_resource_inventory
    expected_inventory = message(*[(1, row) for row in fixture.inventory_rows(lab.work)])
    try:
        with mock.patch.object(profile, "validate_resource_inventory", wraps=original) as inventory:
            healthy(lab)
        assert inventory.call_args_list == [mock.call(expected_inventory, lab.candidate)] * count
    finally:
        replace(lab, [("main", lab.oracle)], [good])
    healthy(lab)


def test_oversized_hidden_sole_and_wrong_storage_class_executors_reject(lab):
    good = lab.executor()
    large = padded_opaque(good, 11, EXECUTOR_LIMIT + 1)
    variants = [[], [good] * 9, [large], [good, large], [None], [good, None],
                ["not a blob"], [good, "not a blob"], [17], [good, 17], [1.5], [good, 1.5]]
    for executors in variants:
        reject_then_restore(lab, executors=executors, error=EXECUTOR_ERROR)


def test_exact_main_and_executor_byte_limits_remain_valid(lab):
    healthy(lab)
    main = padded_opaque(lab.oracle, 1, MAIN_LIMIT)
    executor = padded_opaque(lab.executor(), 11, EXECUTOR_LIMIT)
    assert len(main) == MAIN_LIMIT and len(executor) == EXECUTOR_LIMIT
    replace(lab, [("main", main)], [executor] * 8)
    try:
        healthy(lab)
        healthy(lab, fresh=True)
    finally:
        replace(lab, [("main", lab.oracle)], [lab.executor()])
    healthy(lab)


def test_nonmain_rows_and_fresh_executor_semantics_are_not_broadened(lab):
    healthy(lab)
    replace(lab, [("main", lab.oracle), ("other", b"X" * (MAIN_LIMIT + 1))], [None] * 9)
    try:
        healthy(lab, fresh=True)  # Fresh discovery has no actual-executor requirement.
    finally:
        replace(lab, [("main", lab.oracle)], [lab.executor()])
    healthy(lab)


def test_selected_bounded_blobs_still_reach_existing_role_and_wire_gates(lab):
    reject_then_restore(lab, main=[("main", b"")],
                        error="proposal runtime profile metadata mismatch")
    reject_then_restore(lab, executors=[b""],
                        error="proposal runtime database validation failed")
    reject_then_restore(lab, executors=[lab.executor(model="wrong-model")],
                        error="proposal runtime actual executor mismatch")
