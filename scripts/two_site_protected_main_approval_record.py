"""Protected main approval record wire format parser.

Pure wire validation is NOT proof of the owner's actual approval.
Its digest references the exact canonical OWNER-DECISION bytes, NOT a Git commit SHA.
A later trusted file reader must authenticate file custody and this result,
and separate live gates remain required.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

__all__ = [
    "ProtectedMainApprovalRecordError",
    "MAX_PROTECTED_MAIN_APPROVAL_BYTES",
    "parse_protected_main_approval_record",
]

MAX_PROTECTED_MAIN_APPROVAL_BYTES: int = 4096

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_DECISION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_EXPECTED_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "reference",
        "prior_main_sha",
        "approved_main_sha",
        "prior_checkpoint_sha256",
        "prior_head",
        "decision_id",
    }
)


class ProtectedMainApprovalRecordError(ValueError):
    """Raised when owner approval binding or record validation fails."""


def _is_valid_decision_id(decision_id: str) -> bool:
    if type(decision_id) is not str:
        return False
    return bool(_DECISION_ID_RE.fullmatch(decision_id))


def _reject_constant(val: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {val}")


def _unique_pairs_dict(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"Duplicate JSON key: {k}")
        res[k] = v
    return res


def parse_protected_main_approval_record(
    raw: bytes,
    *,
    prior_main_sha: str,
    approved_main_sha: str,
    prior_checkpoint_sha256: str,
    prior_head: str,
) -> dict[str, Any]:
    """Parse and validate canonical wire bytes for an owner approval record.

    Pure wire validation is NOT proof of the owner's actual approval.
    Its digest references the exact canonical OWNER-DECISION bytes, NOT a Git commit SHA.
    A later trusted file reader must authenticate file custody and this result,
    and separate live gates remain required.
    """
    for val in (prior_main_sha, approved_main_sha, prior_head):
        if type(val) is not str or not _HEX_40_RE.fullmatch(val):
            raise ProtectedMainApprovalRecordError("invalid owner approval binding") from None

    if type(prior_checkpoint_sha256) is not str or not _HEX_64_RE.fullmatch(prior_checkpoint_sha256):
        raise ProtectedMainApprovalRecordError("invalid owner approval binding") from None

    if type(raw) is not bytes:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if not (1 <= len(raw) <= MAX_PROTECTED_MAIN_APPROVAL_BYTES):
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    try:
        text = raw.decode("utf-8")
        data = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_pairs_dict)
    except Exception:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if type(data) is not dict:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if frozenset(data.keys()) != _EXPECTED_KEYS:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if any(type(val) is not str for val in data.values()):
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if data["schema_version"] != "1.0":
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None
    if data["kind"] != "OWNER_APPROVED_PROTECTED_MAIN_CHANGE":
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None
    if data["reference"] != "refs/heads/main":
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if data["prior_main_sha"] != prior_main_sha:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None
    if data["approved_main_sha"] != approved_main_sha:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None
    if data["prior_checkpoint_sha256"] != prior_checkpoint_sha256:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None
    if data["prior_head"] != prior_head:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if not _is_valid_decision_id(data["decision_id"]):
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    try:
        canonical_raw = (
            json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
    except Exception:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    if raw != canonical_raw:
        raise ProtectedMainApprovalRecordError("invalid owner approval record") from None

    raw_sha256 = hashlib.sha256(raw).hexdigest().lower()
    return {
        "decision": dict(data),
        "raw_sha256": raw_sha256,
    }
