"""Protected main approval reader bridge.

Composes accepted leaf byte custody and pure wire record parsing under caller's
existing supervisor lock. Validates caller binding kwargs before any I/O or parser
calls, acquires no lock or authority, performs no direct filesystem I/O, and
preserves BaseException sentinels.
"""

from __future__ import annotations

import copy
import pathlib
import re
from typing import Any

from scripts.two_site_protected_main_approval_leaf import (
    read_protected_main_approval_leaf_locked,
)
from scripts.two_site_protected_main_approval_record import (
    MAX_PROTECTED_MAIN_APPROVAL_BYTES,
    parse_protected_main_approval_record,
)

__all__ = [
    "MAX_PROTECTED_MAIN_APPROVAL_BYTES",
    "ProtectedMainApprovalReaderError",
    "read_protected_main_approval_locked",
]

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtectedMainApprovalReaderError(ValueError):
    """Raised when protected main approval binding or file verification fails."""


def read_protected_main_approval_locked(
    root: pathlib.Path,
    *,
    prior_main_sha: str,
    approved_main_sha: str,
    prior_checkpoint_sha256: str,
    prior_head: str,
) -> dict[str, Any]:
    """Read and verify protected main approval file under caller's supervisor lock.

    Validates the four plain-string lower-hex binding kwargs strictly before any
    leaf, filesystem, or parser call. Then reads verified leaf bytes exactly once
    and parses record bytes with all four supplied kwargs exactly once. All ordinary
    leaf/parser errors are mapped to 'invalid owner approval file' from None.
    BaseException is preserved untouched. Returns entire detached parser result.
    """
    for val in (prior_main_sha, approved_main_sha, prior_head):
        if type(val) is not str or not _HEX_40_RE.fullmatch(val):
            raise ProtectedMainApprovalReaderError("invalid owner approval binding") from None

    if type(prior_checkpoint_sha256) is not str or not _HEX_64_RE.fullmatch(prior_checkpoint_sha256):
        raise ProtectedMainApprovalReaderError("invalid owner approval binding") from None

    try:
        raw = read_protected_main_approval_leaf_locked(root)
        out = parse_protected_main_approval_record(
            raw,
            prior_main_sha=prior_main_sha,
            approved_main_sha=approved_main_sha,
            prior_checkpoint_sha256=prior_checkpoint_sha256,
            prior_head=prior_head,
        )
    except Exception:
        raise ProtectedMainApprovalReaderError("invalid owner approval file") from None

    return copy.deepcopy(out)
