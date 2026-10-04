"""Pure v1.1 full-history record codec; not approval custody or Start authority.

Keeps the v1 12-commit/16384-byte codec unchanged. Real Git ancestry, accepted
proposal/receipt custody, owner file custody, STOP/Broker/lock quiescence and
one-shot publication remain mandatory live reader/publisher responsibilities.
"""
from __future__ import annotations

import copy
import json
import re

from scripts.two_site_main_transition_sequence import validate_main_transition_sequence
from scripts.two_site_maintenance_transition_record import (
    ALLOWED_MAINTENANCE_CONTROL_PATHS, _validate_new_repository,
)
from scripts.two_site_protected_main_approval_record import parse_protected_main_approval_record
from scripts.two_site_proposal_runner import unique_json

MAX_MAIN_MAINTENANCE_RECORD_BYTES = 65536
SCHEMA_VERSION = "1.1"
KIND = "OWNER_APPROVED_MAIN_MAINTENANCE_TRANSITION"
ERROR = "invalid main maintenance transition record"
KEYS = frozenset({"schema_version", "kind", "prior_checkpoint_sha256", "prior_head",
                 "new_repository", "owner_approval", "pre_main_commits", "main_integration", "post_main_commits"})
OWNER_KEYS = frozenset({"schema_version", "kind", "reference", "prior_main_sha",
                       "approved_main_sha", "prior_checkpoint_sha256", "prior_head", "decision_id"})
MAIN_REF = "refs/heads/main"
HEX40, HEX64 = re.compile(r"[0-9a-f]{40}\Z"), re.compile(r"[0-9a-f]{64}\Z")


class MainMaintenanceRecordError(ValueError):
    """Constant-text rejection without untrusted data or lower exception output."""


def _same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return (left.keys() == right.keys()
                and all(type(k) is str for k in left)
                and all(type(k) is str for k in right)
                and all(_same(left[k], right[k]) for k in left))
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _canonical(value: object, limit: int) -> bytes:
    """Bound cumulative output while encoding, before joining complete bytes."""
    chunks, size = [], 1  # canonical trailing LF
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("utf-8")
        size += len(encoded)
        if size > limit:
            raise ValueError
        chunks.append(encoded)
    return b"".join(chunks) + b"\n"


def _owner(value: object, prior: dict, new: dict, checkpoint: str) -> dict:
    if type(value) is not dict or len(value) != 2 or any(type(k) is not str for k in value) or set(value) != {"decision", "raw_sha256"}:
        raise ValueError
    decision, digest = value["decision"], value["raw_sha256"]
    if type(decision) is not dict or len(decision) != 8 or any(type(k) is not str for k in decision) or set(decision) != OWNER_KEYS:
        raise ValueError
    # Before serialization: the actual owner grammar only has eight short strings.
    if any(type(v) is not str or not 1 <= len(v) <= 128 for v in decision.values()):
        raise ValueError
    if type(digest) is not str or not HEX64.fullmatch(digest):
        raise ValueError
    raw = _canonical(decision, 4096)
    parsed = parse_protected_main_approval_record(
        raw, prior_main_sha=prior["protected_refs"][MAIN_REF],
        approved_main_sha=new["protected_refs"][MAIN_REF],
        prior_checkpoint_sha256=checkpoint, prior_head=prior["head"],
    )
    if not _same(parsed, value):
        raise ValueError
    return parsed


def validate_main_maintenance_record(
    record: object, *, prior_checkpoint_sha256: object, prior_repository: object,
    owner_approval: object,
) -> dict:
    """Bind the complete record to supplied prior facts and trusted-reader result.

    Supplied facts/results must come from independent live readers at use time;
    validation here proves metadata consistency, not their real-world custody.
    """
    try:
        if type(prior_checkpoint_sha256) is not str or not HEX64.fullmatch(prior_checkpoint_sha256):
            raise ValueError
        if type(record) is not dict or len(record) != 9 or any(type(k) is not str for k in record) or set(record) != KEYS:
            raise ValueError
        if type(record["schema_version"]) is not str or record["schema_version"] != SCHEMA_VERSION:
            raise ValueError
        if type(record["kind"]) is not str or record["kind"] != KIND:
            raise ValueError
        if type(record["prior_checkpoint_sha256"]) is not str or record["prior_checkpoint_sha256"] != prior_checkpoint_sha256:
            raise ValueError
        for repository in (prior_repository, record["new_repository"]):
            if type(repository) is not dict or len(repository) != 5 or type(repository.get("status")) is not str or repository["status"] != "":
                raise ValueError
        prior = _validate_new_repository(prior_repository)
        new = _validate_new_repository(record["new_repository"])
        if type(record["prior_head"]) is not str or record["prior_head"] != prior["head"] or new["head"] == prior["head"]:
            raise ValueError
        if new["assignment_scope"]["assignment_id"] == prior["assignment_scope"]["assignment_id"]:
            raise ValueError
        if new["control_blobs"].keys() != prior["control_blobs"].keys():
            raise ValueError
        if new["protected_refs"][MAIN_REF] == prior["protected_refs"][MAIN_REF]:
            raise ValueError
        if any(new["protected_refs"][r] != prior["protected_refs"][r] for r in prior["protected_refs"] if r != MAIN_REF):
            raise ValueError
        owner = _owner(owner_approval, prior, new, prior_checkpoint_sha256)
        if not _same(record["owner_approval"], owner):
            raise ValueError
        sequence = validate_main_transition_sequence(
            record["pre_main_commits"], record["main_integration"], record["post_main_commits"],
            prior_head=prior["head"], new_head=new["head"],
            approved_main_sha=new["protected_refs"][MAIN_REF],
            control_blob_paths=frozenset(new["control_blobs"]),
        )
        declared = {p for c in sequence["pre_main_commits"] + sequence["post_main_commits"] for p in c["paths"]}
        changed_controls = {p for p in prior["control_blobs"] if prior["control_blobs"][p] != new["control_blobs"][p]}
        if not changed_controls <= ALLOWED_MAINTENANCE_CONTROL_PATHS or not changed_controls <= declared:
            raise ValueError
        result = {"schema_version": SCHEMA_VERSION, "kind": KIND,
                  "prior_checkpoint_sha256": prior_checkpoint_sha256, "prior_head": prior["head"],
                  "new_repository": new, "owner_approval": owner, **sequence}
        _canonical(result, MAX_MAIN_MAINTENANCE_RECORD_BYTES)
        return copy.deepcopy(result)
    except Exception:
        raise MainMaintenanceRecordError(ERROR) from None


def build_main_maintenance_record(
    *, prior_checkpoint_sha256: object, prior_repository: object, new_repository: object,
    owner_approval: object, pre_main_commits: object, main_integration: object, post_main_commits: object,
) -> dict:
    """Build the same full-history record; acquire no approval or I/O authority."""
    prior_head = prior_repository.get("head") if type(prior_repository) is dict else None
    record = {"schema_version": SCHEMA_VERSION, "kind": KIND,
              "prior_checkpoint_sha256": prior_checkpoint_sha256, "prior_head": prior_head,
              "new_repository": new_repository, "owner_approval": owner_approval,
              "pre_main_commits": pre_main_commits, "main_integration": main_integration,
              "post_main_commits": post_main_commits}
    return validate_main_maintenance_record(record, prior_checkpoint_sha256=prior_checkpoint_sha256,
                                            prior_repository=prior_repository, owner_approval=owner_approval)


def serialize_main_maintenance_record(record: object, **bindings) -> bytes:
    """Validate and serialize canonical LF-terminated bytes, limited to 64 KiB."""
    try:
        return _canonical(validate_main_maintenance_record(record, **bindings), MAX_MAIN_MAINTENANCE_RECORD_BYTES)
    except Exception:
        raise MainMaintenanceRecordError(ERROR) from None


def parse_main_maintenance_record(raw: object, **bindings) -> dict:
    """Check the raw byte limit before decode/JSON; canonical spelling mandatory."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MAIN_MAINTENANCE_RECORD_BYTES:
            raise ValueError
        parsed = unique_json(raw.decode("utf-8"))
        result = validate_main_maintenance_record(parsed, **bindings)
        if raw != _canonical(result, MAX_MAIN_MAINTENANCE_RECORD_BYTES):
            raise ValueError
        return result
    except Exception:
        raise MainMaintenanceRecordError(ERROR) from None
