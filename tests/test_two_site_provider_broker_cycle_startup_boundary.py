#!/usr/bin/env python3
"""Hermetic tests for cycle startup parsing and run directory owner boundaries."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_preparation_contract import PreparationStartup
from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    _parse_cycle_preparation_startup,
)

CANONICAL_CYCLE_ID = "cycle-0001-20260925T120000Z-1"
CANONICAL_HEAD = "a" * 40
CANONICAL_ASSIGNMENT_SHA = "b" * 64


def _canonical_startup() -> PreparationStartup:
    return PreparationStartup(
        schema_version=1,
        route="one_shot",
        cycle_id=CANONICAL_CYCLE_ID,
        source_head=CANONICAL_HEAD,
        assignment_sha256=CANONICAL_ASSIGNMENT_SHA,
    )


def test_cycle_preparation_startup_positive_contract() -> None:
    startup = _canonical_startup()
    mapping = startup.to_mapping()

    parsed = _parse_cycle_preparation_startup(mapping, CANONICAL_CYCLE_ID)

    assert isinstance(parsed, PreparationStartup)
    assert type(parsed) is PreparationStartup
    assert parsed == startup
    assert parsed.cycle_id == CANONICAL_CYCLE_ID
    assert parsed.schema_version == 1
    assert parsed.route == "one_shot"
    assert parsed.source_head == CANONICAL_HEAD
    assert parsed.assignment_sha256 == CANONICAL_ASSIGNMENT_SHA
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        parsed.route = "legacy"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw_mutator", "controller_id"),
    [
        (lambda d: {**d, "schema_version": "1.0"}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "schema_version": True}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "schema_version": 2}, CANONICAL_CYCLE_ID),
        (lambda d: {k: v for k, v in d.items() if k != "schema_version"}, CANONICAL_CYCLE_ID),
        (lambda d: {k: v for k, v in d.items() if k != "route"}, CANONICAL_CYCLE_ID),
        (lambda d: {k: v for k, v in d.items() if k != "cycle_id"}, CANONICAL_CYCLE_ID),
        (lambda d: {k: v for k, v in d.items() if k != "source_head"}, CANONICAL_CYCLE_ID),
        (lambda d: {k: v for k, v in d.items() if k != "assignment_sha256"}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "extra_key": "unexpected"}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "route": "invalid_route"}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "route": "ONE_SHOT"}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "route": ""}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "source_head": "A" * 40}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "source_head": "a" * 39}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "source_head": "g" * 40}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "assignment_sha256": "B" * 64}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "assignment_sha256": "b" * 63}, CANONICAL_CYCLE_ID),
        (lambda d: {**d, "assignment_sha256": "z" * 64}, CANONICAL_CYCLE_ID),
        (lambda d: dict(d), "cycle-0002-20260925T120000Z-2"),
        (lambda d: dict(d), "maint-20260925T120000Z-0123456789abcdef"),
        (
            lambda d: {**d, "cycle_id": "maint-20260925T120000Z-0123456789abcdef"},
            "maint-20260925T120000Z-0123456789abcdef",
        ),
        (lambda d: dict(d), "malformed-id"),
        (lambda d: dict(d), "cycle-1"),
        (lambda d: dict(d), ""),
        (lambda d: {**d, "cycle_id": "malformed-id"}, "malformed-id"),
        (lambda d: {**d, "cycle_id": ""}, ""),
        (lambda _d: "not-a-dict", CANONICAL_CYCLE_ID),
        (lambda _d: None, CANONICAL_CYCLE_ID),
        (lambda _d: [1, 2, 3], CANONICAL_CYCLE_ID),
        (lambda d: {1: "bad_key", **{k: v for k, v in d.items() if k != "route"}}, CANONICAL_CYCLE_ID),
    ],
)
def test_cycle_preparation_startup_negative_matrix(
    raw_mutator: object, controller_id: str
) -> None:
    base = _canonical_startup().to_mapping()
    mutated = raw_mutator(base) if callable(raw_mutator) else raw_mutator
    with pytest.raises(BrokerError):
        _parse_cycle_preparation_startup(mutated, controller_id)


def _snapshot_tree(root: Path) -> dict[Path, tuple[int, int, int, list[str]]]:
    snapshot: dict[Path, tuple[int, int, int, list[str]]] = {}
    for path in [root, *root.glob("**/*")]:
        st = os.lstat(path)
        children = sorted(os.listdir(path)) if path.is_dir() else []
        snapshot[path] = (st.st_mode, st.st_uid, st.st_ino, children)
    return snapshot


def test_open_existing_cycle_run_directory_owner_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "workspace_root").resolve()
    root.mkdir(mode=0o700)
    root.chmod(0o700)

    cycle_id = CANONICAL_CYCLE_ID
    subdirs = [
        root / ".local",
        root / ".local" / "two-site-refactor",
        root / ".local" / "two-site-refactor" / "autopilot",
        root / ".local" / "two-site-refactor" / "autopilot" / "runs",
        root / ".local" / "two-site-refactor" / "autopilot" / "runs" / cycle_id,
    ]
    for directory in subdirs:
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)

    target_run_dir = subdirs[-1]
    initial_snapshot = _snapshot_tree(root)

    real_euid = os.geteuid()
    unowned_uid = real_euid + 10001

    # 1. Initial open rejects when current euid does not own directory
    with monkeypatch.context() as ctx:
        ctx.setattr(os, "geteuid", lambda: unowned_uid)
        with pytest.raises(BrokerError, match="not owned by current euid"):
            with _open_existing_cycle_run_directory(root, cycle_id):
                pass

    # 2. Valid open succeeds, post-open verify rejects owner drift, restore succeeds
    with _open_existing_cycle_run_directory(root, cycle_id) as (
        run_path,
        run_fd,
        verify_identity,
    ):
        assert run_path == target_run_dir
        assert isinstance(run_fd, int)
        assert run_fd >= 0

        # Valid initial verification succeeds
        verify_identity()

        # Post-open verify rejects owner drift
        with monkeypatch.context() as ctx:
            ctx.setattr(os, "geteuid", lambda: unowned_uid)
            with pytest.raises(BrokerError, match="not owned by current euid"):
                verify_identity()

        # Restore patch and prove valid verify succeeds
        verify_identity()

    # 3. Post-close verification rejects closed context
    with pytest.raises(BrokerError, match="closed"):
        verify_identity()

    # 4. Assert no directory mutation
    final_snapshot = _snapshot_tree(root)
    assert final_snapshot == initial_snapshot
