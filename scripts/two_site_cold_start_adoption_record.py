"""Dormant pure v1 terminal-epoch cold-adoption record codec under AE03.

Provides pure functional building, validation, serialization, and parsing of
version 1.0 terminal-epoch cold-adoption records bound to an audited terminal
epoch and canonical repository facts.

Boundary and authority constraints:
1. Pure dormant codec only: contains NO storage writer, startup hook, Provider,
   Broker, monitor, wake, replay, Git, network, or environment mutation.
2. Does NOT authorize startup: publisher, strict reader, and startup gate remain
   future units.
3. Fixed error strings: error text is constant and does not echo untrusted payloads.
"""

from __future__ import annotations

import copy
import datetime
import json
import posixpath
import re
from pathlib import PurePosixPath
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        _validate_persisted_repository,
        rescope_epoch_basename,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        _validate_persisted_repository,
        rescope_epoch_basename,
    )

try:
    from scripts.two_site_proposal_runner import unique_json
except ImportError:
    from two_site_proposal_runner import unique_json  # type: ignore[no-redef]

MAX_TERMINAL_ADOPTION_RECORD_BYTES: int = 16384
TERMINAL_ADOPTION_SCHEMA_VERSION: str = "1.0"
TERMINAL_ADOPTION_KIND: str = "TERMINAL_EPOCH_COLD_ADOPTION"

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_TIMESTAMP_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_GLOB_CHARS = frozenset("*?[]{}")
_MAX_PATH_LEN = 1024
_MAX_ALLOWED_PATHS_COUNT = 256

_TERMINAL_ADOPTION_KEYS = frozenset({
    "schema_version",
    "kind",
    "new_repository",
    "old_epoch_basename",
    "old_repository_head",
    "old_terminal_receipt_sha256",
    "prior_checkpoint_sha256",
    "issued_at_utc",
})


class ColdStartAdoptionRecordError(ValueError):
    """Raised when cold-adoption record building, cross-binding, or validation fails."""


__all__ = [
    "ColdStartAdoptionRecordError",
    "MAX_TERMINAL_ADOPTION_RECORD_BYTES",
    "TERMINAL_ADOPTION_KIND",
    "TERMINAL_ADOPTION_SCHEMA_VERSION",
    "build_terminal_adoption_record",
    "parse_terminal_adoption_record",
    "serialize_terminal_adoption_record",
    "validate_terminal_adoption_record",
]


def _bounded_canonical_bytes(obj: Any, max_bytes: int = MAX_TERMINAL_ADOPTION_RECORD_BYTES) -> bytes:
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        chunks: list[bytes] = []
        total_bytes = 0
        for chunk in encoder.iterencode(obj):
            chunk_bytes = chunk.encode("utf-8")
            total_bytes += len(chunk_bytes)
            if total_bytes > max_bytes:
                raise ColdStartAdoptionRecordError("Canonical JSON size exceeds limit")
            chunks.append(chunk_bytes)
        out = b"".join(chunks)
        if len(out) > max_bytes:
            raise ColdStartAdoptionRecordError("Canonical JSON size exceeds limit")
        return out
    except ColdStartAdoptionRecordError:
        raise
    except (TypeError, ValueError) as exc:
        raise ColdStartAdoptionRecordError("Record cannot be serialized to canonical JSON") from exc


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def _validate_timestamp_utc(val: Any) -> str:
    if type(val) is not str:
        raise ColdStartAdoptionRecordError("issued_at_utc must be a string")
    if not _TIMESTAMP_UTC_RE.fullmatch(val):
        raise ColdStartAdoptionRecordError(
            "issued_at_utc must match canonical whole-second UTC format YYYY-MM-DDTHH:MM:SSZ"
        )
    try:
        dt = datetime.datetime.strptime(val, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ColdStartAdoptionRecordError("issued_at_utc has invalid calendar date/time")
    if dt.strftime("%Y-%m-%dT%H:%M:%SZ") != val:
        raise ColdStartAdoptionRecordError("issued_at_utc canonical representation mismatch")
    return val


def _validate_hex_64(val: Any, name: str) -> str:
    if type(val) is not str or not _HEX_64_RE.fullmatch(val):
        if name == "terminal_receipt_sha256":
            raise ColdStartAdoptionRecordError("terminal_receipt_sha256 must be 64 lowercase hex characters")
        raise ColdStartAdoptionRecordError("prior_checkpoint_sha256 must be 64 lowercase hex characters")
    return val


def _validate_literal_posix_path(p: Any) -> str:
    if type(p) is not str:
        raise ColdStartAdoptionRecordError("allowed_paths entry must be a string")
    if not p or len(p) > _MAX_PATH_LEN:
        raise ColdStartAdoptionRecordError("allowed_paths entry length must be between 1 and 1024 characters")
    if p != p.strip():
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain leading or trailing whitespace")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain control characters or NUL")
    if "\\" in p:
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain backslashes")
    if p.startswith("/"):
        raise ColdStartAdoptionRecordError("allowed_paths entry must be a relative path")
    if any(c in _GLOB_CHARS for c in p):
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain glob characters")
    if re.match(r"^[a-zA-Z]:", p) or ":" in p:
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain Windows drive prefix or colon")
    segments = p.split("/")
    if any(seg == "" for seg in segments):
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain empty segments or trailing slash")
    if any(seg in (".", "..") for seg in segments):
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain . or .. segments")
    if any(seg == ".git" or seg.lower() == ".git" for seg in segments):
        raise ColdStartAdoptionRecordError("allowed_paths entry must not contain .git component")
    if posixpath.normpath(p) != p:
        raise ColdStartAdoptionRecordError("allowed_paths entry must be normalized without path aliases")
    if PurePosixPath(p).as_posix() != p:
        raise ColdStartAdoptionRecordError("allowed_paths entry must be canonical POSIX path")
    return p


def _extract_and_validate_allowed_paths(repo: dict[str, Any]) -> set[str]:
    scope = repo.get("assignment_scope")
    if type(scope) is not dict:
        raise ColdStartAdoptionRecordError("assignment_scope must be a dict")
    raw_paths = scope.get("allowed_paths")
    if type(raw_paths) is not list:
        raise ColdStartAdoptionRecordError("allowed_paths must be a list")
    if not raw_paths:
        raise ColdStartAdoptionRecordError("allowed_paths must be non-empty")
    if len(raw_paths) > _MAX_ALLOWED_PATHS_COUNT:
        raise ColdStartAdoptionRecordError("allowed_paths must not exceed 256 entries")
    seen: set[str] = set()
    for item in raw_paths:
        norm = _validate_literal_posix_path(item)
        if norm in seen:
            raise ColdStartAdoptionRecordError("Duplicate path in allowed_paths")
        seen.add(norm)
    return seen


def _validate_new_repository(new_repository: Any) -> dict[str, Any]:
    if type(new_repository) is not dict:
        raise ColdStartAdoptionRecordError("new_repository must be a dict")
    new_repo_copy = copy.deepcopy(new_repository)
    try:
        val_new = _validate_persisted_repository(new_repo_copy)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionRecordError("new_repository validation failed")
    return copy.deepcopy(val_new if isinstance(val_new, dict) else new_repo_copy)


def _validate_terminal_audit_structure(terminal_audit: Any) -> tuple[str, dict[str, Any], str]:
    if type(terminal_audit) is not dict:
        raise ColdStartAdoptionRecordError("terminal_audit must be a dict")
    if set(terminal_audit.keys()) != {"epoch_basename", "repository", "terminal_receipt_sha256"}:
        raise ColdStartAdoptionRecordError("terminal_audit keys mismatch")

    epoch_basename = terminal_audit["epoch_basename"]
    if type(epoch_basename) is not str or not epoch_basename:
        raise ColdStartAdoptionRecordError("terminal_audit epoch_basename must be a non-empty string")

    raw_old_repo = terminal_audit["repository"]
    if type(raw_old_repo) is not dict:
        raise ColdStartAdoptionRecordError("terminal_audit repository must be a dict")

    term_sha = _validate_hex_64(terminal_audit["terminal_receipt_sha256"], "terminal_receipt_sha256")

    old_repo_copy = copy.deepcopy(raw_old_repo)
    try:
        val_old = _validate_persisted_repository(old_repo_copy)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionRecordError("terminal_audit repository validation failed")
    norm_old_repo = copy.deepcopy(val_old if isinstance(val_old, dict) else old_repo_copy)

    try:
        derived_basename = rescope_epoch_basename(norm_old_repo)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionRecordError("Failed to derive epoch basename from terminal_audit repository")

    if derived_basename != epoch_basename:
        raise ColdStartAdoptionRecordError("terminal_audit epoch_basename does not match derived basename")

    return epoch_basename, norm_old_repo, term_sha


def _validate_cross_bindings(norm_new_repo: dict[str, Any], norm_old_repo: dict[str, Any]) -> None:
    new_head = norm_new_repo.get("head")
    old_head = norm_old_repo.get("head")
    if type(new_head) is not str or not _HEX_40_RE.fullmatch(new_head):
        raise ColdStartAdoptionRecordError("Current repository head must be 40 lowercase hex characters")
    if type(old_head) is not str or not _HEX_40_RE.fullmatch(old_head):
        raise ColdStartAdoptionRecordError("Old repository head must be 40 lowercase hex characters")
    if new_head == old_head:
        raise ColdStartAdoptionRecordError("Current and old repository HEADs must differ")

    new_refs = norm_new_repo.get("protected_refs")
    old_refs = norm_old_repo.get("protected_refs")
    if not _type_sensitive_equal(new_refs, old_refs):
        raise ColdStartAdoptionRecordError("Current and old protected_refs must be exactly equal")

    new_scope = norm_new_repo.get("assignment_scope")
    old_scope = norm_old_repo.get("assignment_scope")
    if type(new_scope) is not dict or type(old_scope) is not dict:
        raise ColdStartAdoptionRecordError("assignment_scope must be a dict")
    new_aid = new_scope.get("assignment_id")
    old_aid = old_scope.get("assignment_id")
    if type(new_aid) is not str or not new_aid:
        raise ColdStartAdoptionRecordError("Current assignment_id must be a non-empty string")
    if type(old_aid) is not str or not old_aid:
        raise ColdStartAdoptionRecordError("Old assignment_id must be a non-empty string")
    if new_aid == old_aid:
        raise ColdStartAdoptionRecordError("Current and old assignment IDs must differ")

    new_paths = _extract_and_validate_allowed_paths(norm_new_repo)
    old_paths = _extract_and_validate_allowed_paths(norm_old_repo)
    if not new_paths.isdisjoint(old_paths):
        raise ColdStartAdoptionRecordError("Current and old allowed_paths sets must have no overlap")


def build_terminal_adoption_record(
    new_repository: dict[str, Any],
    terminal_audit: dict[str, Any],
    prior_checkpoint_sha256: str,
    issued_at_utc: str,
) -> dict[str, Any]:
    """Build and validate a version 1.0 terminal-epoch cold-adoption record."""
    norm_new_repo = _validate_new_repository(new_repository)
    epoch_basename, norm_old_repo, term_receipt_sha = _validate_terminal_audit_structure(terminal_audit)
    val_checkpoint_sha = _validate_hex_64(prior_checkpoint_sha256, "prior_checkpoint_sha256")
    val_issued_at_utc = _validate_timestamp_utc(issued_at_utc)

    _validate_cross_bindings(norm_new_repo, norm_old_repo)

    record: dict[str, Any] = {
        "schema_version": TERMINAL_ADOPTION_SCHEMA_VERSION,
        "kind": TERMINAL_ADOPTION_KIND,
        "new_repository": norm_new_repo,
        "old_epoch_basename": epoch_basename,
        "old_repository_head": norm_old_repo["head"],
        "old_terminal_receipt_sha256": term_receipt_sha,
        "prior_checkpoint_sha256": val_checkpoint_sha,
        "issued_at_utc": val_issued_at_utc,
    }

    return validate_terminal_adoption_record(
        record=record,
        new_repository=new_repository,
        terminal_audit=terminal_audit,
        prior_checkpoint_sha256=prior_checkpoint_sha256,
    )


def validate_terminal_adoption_record(
    record: object,
    new_repository: dict[str, Any],
    terminal_audit: dict[str, Any],
    prior_checkpoint_sha256: str,
) -> dict[str, Any]:
    """Validate a terminal-epoch cold-adoption candidate record and return detached copy."""
    if type(record) is not dict:
        raise ColdStartAdoptionRecordError("record must be a dict")
    if set(record.keys()) != _TERMINAL_ADOPTION_KEYS:
        raise ColdStartAdoptionRecordError("Record keys mismatch")
    if "issuer" in record or "approved" in record:
        raise ColdStartAdoptionRecordError("Record contains disallowed fields")

    norm_new_repo = _validate_new_repository(new_repository)
    epoch_basename, norm_old_repo, term_receipt_sha = _validate_terminal_audit_structure(terminal_audit)
    val_checkpoint_sha = _validate_hex_64(prior_checkpoint_sha256, "prior_checkpoint_sha256")
    _validate_cross_bindings(norm_new_repo, norm_old_repo)

    if type(record["schema_version"]) is not str or record["schema_version"] != TERMINAL_ADOPTION_SCHEMA_VERSION:
        raise ColdStartAdoptionRecordError("Record schema_version must be 1.0")
    if type(record["kind"]) is not str or record["kind"] != TERMINAL_ADOPTION_KIND:
        raise ColdStartAdoptionRecordError("Record kind must be TERMINAL_EPOCH_COLD_ADOPTION")

    if not _type_sensitive_equal(record["new_repository"], norm_new_repo):
        raise ColdStartAdoptionRecordError("Record new_repository does not match expected repository")
    if record["old_epoch_basename"] != epoch_basename:
        raise ColdStartAdoptionRecordError("Record old_epoch_basename does not match terminal audit")
    if record["old_repository_head"] != norm_old_repo["head"]:
        raise ColdStartAdoptionRecordError("Record old_repository_head does not match old repository head")
    if record["old_terminal_receipt_sha256"] != term_receipt_sha:
        raise ColdStartAdoptionRecordError("Record old_terminal_receipt_sha256 does not match terminal audit")
    if record["prior_checkpoint_sha256"] != val_checkpoint_sha:
        raise ColdStartAdoptionRecordError("Record prior_checkpoint_sha256 does not match expected checkpoint")

    _validate_timestamp_utc(record["issued_at_utc"])

    _bounded_canonical_bytes(record, MAX_TERMINAL_ADOPTION_RECORD_BYTES)

    return copy.deepcopy(record)


def serialize_terminal_adoption_record(
    record: object,
    new_repository: dict[str, Any],
    terminal_audit: dict[str, Any],
    prior_checkpoint_sha256: str,
) -> bytes:
    """Validate and serialize a terminal cold-adoption record to canonical JSON bytes."""
    validated = validate_terminal_adoption_record(
        record=record,
        new_repository=new_repository,
        terminal_audit=terminal_audit,
        prior_checkpoint_sha256=prior_checkpoint_sha256,
    )
    return _bounded_canonical_bytes(validated, MAX_TERMINAL_ADOPTION_RECORD_BYTES)


def parse_terminal_adoption_record(
    raw: bytes,
    new_repository: dict[str, Any],
    terminal_audit: dict[str, Any],
    prior_checkpoint_sha256: str,
) -> dict[str, Any]:
    """Parse, check canonical bytes, and validate a terminal cold-adoption record."""
    if type(raw) is not bytes:
        raise ColdStartAdoptionRecordError("raw must be bytes")
    if len(raw) > MAX_TERMINAL_ADOPTION_RECORD_BYTES:
        raise ColdStartAdoptionRecordError("raw bytes exceed maximum allowed length")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ColdStartAdoptionRecordError("raw bytes are not valid UTF-8")

    try:
        parsed = unique_json(text)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise ColdStartAdoptionRecordError("Failed to parse JSON or duplicate key detected")

    if type(parsed) is not dict:
        raise ColdStartAdoptionRecordError("Parsed JSON root must be an object")

    validated = validate_terminal_adoption_record(
        record=parsed,
        new_repository=new_repository,
        terminal_audit=terminal_audit,
        prior_checkpoint_sha256=prior_checkpoint_sha256,
    )

    canonical = _bounded_canonical_bytes(validated, MAX_TERMINAL_ADOPTION_RECORD_BYTES)
    if raw != canonical:
        raise ColdStartAdoptionRecordError("raw bytes do not match canonical JSON serialization")

    return validated
