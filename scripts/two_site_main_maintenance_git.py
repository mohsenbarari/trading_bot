"""Read-only full v1.1 Git proof; not owner/receipt custody or Start authority.

Uses the existing capped, scrubbed, no-replacement/no-lazy-fetch Git executor.
No synthetic old-v1 chunks, history truncation, repository writes or authority
from hash-shaped receipt metadata. Callers must additionally prove owner file,
accepted proposal/receipt custody and live lock/STOP/checkpoint bindings.
"""
from __future__ import annotations

from pathlib import Path
import copy
import re

from scripts.two_site_main_maintenance_record import (
    parse_main_maintenance_record, validate_main_maintenance_record,
)
from scripts.two_site_maintenance_transition_git import _run_git_query, _validate_root
from scripts.two_site_protected_main_git_facts import verify_protected_main_git_locked

ERROR = "invalid main maintenance git proof"
HEX40 = re.compile(rb"[0-9a-f]{40}\Z")
MAIN = "refs/heads/main"
ORIGIN = "refs/remotes/origin/main"
CANDIDATE = "refs/heads/candidate/wa-ir-standby-v1"
MAX_PATHS = 32


class MainMaintenanceGitProofError(ValueError):
    """Fixed ordinary-failure error without untrusted query output."""


def _query(root: Path, args: list[str]) -> bytes:
    raw = _run_git_query(root, args)
    if type(raw) is not bytes or len(raw) > 65536:
        raise ValueError
    return raw


def _tree(root: Path, head: str, paths: list[str]) -> dict[str, tuple[bytes, bytes]]:
    raw = _query(root, ["ls-tree", "--full-tree", "-z", head, "--", *paths])
    if not raw or not raw.endswith(b"\0"):
        raise ValueError
    result = {}
    for entry in raw[:-1].split(b"\0"):
        meta, name = entry.split(b"\t", 1)
        mode, kind, digest = meta.split(b" ")
        path = name.decode("utf-8")
        if (path in result or path not in paths or kind != b"blob"
                or mode not in (b"100644", b"100755") or not HEX40.fullmatch(digest)):
            raise ValueError
        result[path] = (mode, digest)
    if set(result) != set(paths):
        raise ValueError
    return result


def _controls(root: Path, repository: dict) -> None:
    expected = repository["control_blobs"]
    actual = _tree(root, repository["head"], sorted(expected))
    if any(actual[path][1] != digest.encode("ascii") for path, digest in expected.items()):
        raise ValueError


def _protected(root: Path, prior: dict, new: dict) -> dict:
    return verify_protected_main_git_locked(
        root, prior_main_sha=prior["protected_refs"][MAIN],
        approved_main_sha=new["protected_refs"][MAIN], prior_head=prior["head"],
        expected_head=new["head"], expected_origin_main_sha=new["protected_refs"][ORIGIN],
        expected_candidate_sha=new["protected_refs"][CANDIDATE],
    )


def verify_main_maintenance_git_chain(
    root: Path, record: object, *, prior_checkpoint_sha256: object,
    prior_repository: object, owner_approval: object,
) -> None:
    """Prove all declared pre/main/post Git facts, with protected checks at both ends.

    Fixed per-query limits: 10 seconds and 65536 combined output bytes. The
    canonical codec caps the whole history at 32; every commit has <=32 paths.
    Both normal maintenance single parents and main's ordered two parents are
    checked against real objects. Exact first-parent diffs forbid undeclared or
    deleted paths; resulting objects must be regular blobs. No caller widening.
    """
    try:
        bindings = dict(prior_checkpoint_sha256=prior_checkpoint_sha256,
                        prior_repository=prior_repository, owner_approval=owner_approval)
        if type(record) is bytes:
            normalized = parse_main_maintenance_record(record, **bindings)
        else:
            normalized = validate_main_maintenance_record(record, **bindings)
        prior = copy.deepcopy(prior_repository)
        root = _validate_root(root)
        new = normalized["new_repository"]
        entries = (normalized["pre_main_commits"] + [normalized["main_integration"]]
                   + normalized["post_main_commits"])
        if any(len(entry["paths"]) > MAX_PATHS for entry in entries):
            raise ValueError
        before = _protected(root, prior, new)
        _controls(root, prior)
        _controls(root, new)
        previous = normalized["prior_head"]
        for entry in entries:
            head, paths = entry["head"], entry["paths"]
            parents = entry.get("parents", [entry.get("parent")])
            expected = (" ".join([head, *parents]) + "\n").encode("ascii")
            if _query(root, ["rev-list", "--parents", "-n", "1", head]) != expected:
                raise ValueError
            if parents[0] != previous:
                raise ValueError
            diff = _query(root, ["diff-tree", "--no-commit-id", "--name-only",
                                 "--no-renames", "-r", "-z", previous, head, "--"])
            expected_paths = b"".join(path.encode("utf-8") + b"\0" for path in paths)
            if diff != expected_paths:
                raise ValueError
            _tree(root, head, paths)
            previous = head
        if previous != new["head"] or _protected(root, prior, new) != before:
            raise ValueError
        _controls(root, prior)
        _controls(root, new)
    except Exception:
        raise MainMaintenanceGitProofError(ERROR) from None
