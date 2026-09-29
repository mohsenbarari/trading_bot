#!/usr/bin/env python3
"""Read-only terminal old-epoch audit primitive under AE03 cold-start recovery."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

try:
    from scripts.two_site_codex_autopilot import (
        MAX_RESCOPE_RECEIPT_BYTES,
        load_latest_rescope_receipt,
        rescope_epoch_basename,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        MAX_RESCOPE_RECEIPT_BYTES,
        load_latest_rescope_receipt,
        rescope_epoch_basename,
    )

try:
    from scripts.two_site_proposal_runner import (
        bounded_file_bytes,
        unique_json,
    )
except ImportError:
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        bounded_file_bytes,
        unique_json,
    )


class ColdStartEpochError(Exception):
    """Raised when terminal epoch custody inspection fails or evidence is invalid."""


def inspect_terminal_rescope_epoch(epoch_dir: Path) -> dict[str, Any]:
    """Inspect a completed terminal rescope epoch directory under AE03 rules."""
    if not isinstance(epoch_dir, Path):
        raise ColdStartEpochError(f"epoch_dir must be a pathlib.Path instance, got {type(epoch_dir).__name__}")

    if not epoch_dir.is_absolute():
        raise ColdStartEpochError("epoch_dir must be an absolute path")

    epoch_dir_str = str(epoch_dir)
    if epoch_dir_str != os.path.normpath(epoch_dir_str):
        raise ColdStartEpochError("epoch_dir must be canonical without redundant segments")

    try:
        st_dir = os.lstat(epoch_dir)
    except FileNotFoundError:
        raise ColdStartEpochError("epoch_dir does not exist") from None
    except OSError:
        raise ColdStartEpochError("Failed to stat epoch_dir") from None

    if stat.S_ISLNK(st_dir.st_mode):
        raise ColdStartEpochError("epoch_dir must not be a symlink")
    if not stat.S_ISDIR(st_dir.st_mode):
        raise ColdStartEpochError("epoch_dir must be a directory")

    try:
        real_dir = epoch_dir.resolve(strict=True)
    except OSError:
        raise ColdStartEpochError("Failed to resolve epoch_dir path") from None

    if real_dir != epoch_dir:
        raise ColdStartEpochError("epoch_dir contains symlinked path components")
    if st_dir.st_uid != os.geteuid():
        raise ColdStartEpochError("epoch_dir owner UID mismatch")
    if (st_dir.st_mode & 0o777) != 0o700:
        raise ColdStartEpochError("epoch_dir mode must be 0700")

    terminal_file = epoch_dir / "rescope_receipt_3.json"
    try:
        first_bytes = bounded_file_bytes(epoch_dir, terminal_file, MAX_RESCOPE_RECEIPT_BYTES, "terminal rescope receipt")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdStartEpochError(f"Failed to read terminal receipt file: {type(exc).__name__}") from None

    try:
        text = first_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ColdStartEpochError("Terminal receipt payload is not valid UTF-8") from None

    try:
        payload = unique_json(text)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdStartEpochError(f"Terminal receipt JSON is malformed: {type(exc).__name__}") from None

    if not isinstance(payload, dict):
        raise ColdStartEpochError("Terminal receipt JSON root must be an object")

    candidate_repository = payload.get("repository")
    if not isinstance(candidate_repository, dict):
        raise ColdStartEpochError("Terminal receipt missing repository dictionary")

    try:
        derived_basename = rescope_epoch_basename(candidate_repository)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdStartEpochError(f"Failed to derive epoch basename: {type(exc).__name__}") from None

    if not isinstance(derived_basename, str) or derived_basename != epoch_dir.name:
        raise ColdStartEpochError("Derived epoch basename mismatch")

    try:
        strict_envelope = load_latest_rescope_receipt(epoch_dir, candidate_repository)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdStartEpochError(f"Contiguous receipt chain validation failed: {type(exc).__name__}") from None

    if strict_envelope is None:
        raise ColdStartEpochError("No valid contiguous rescope receipt chain found in epoch directory")

    if strict_envelope.get("attempt") != 3:
        raise ColdStartEpochError("Expected terminal attempt=3 in validated envelope")

    if strict_envelope.get("terminal") is not True:
        raise ColdStartEpochError("Expected terminal is True in validated envelope")

    canonical_repository = strict_envelope.get("repository")
    if not isinstance(canonical_repository, dict):
        raise ColdStartEpochError("Validated receipt envelope missing repository dictionary")

    try:
        second_bytes = bounded_file_bytes(epoch_dir, terminal_file, MAX_RESCOPE_RECEIPT_BYTES, "terminal rescope receipt")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ColdStartEpochError(f"Failed to re-read terminal receipt file: {type(exc).__name__}") from None

    if first_bytes != second_bytes:
        raise ColdStartEpochError("Terminal receipt bytes changed between consecutive reads")

    terminal_receipt_sha256 = hashlib.sha256(first_bytes).hexdigest()

    return {
        "epoch_basename": epoch_dir.name,
        "repository": copy.deepcopy(canonical_repository),
        "terminal_receipt_sha256": terminal_receipt_sha256,
    }
