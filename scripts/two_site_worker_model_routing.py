"""Pure disabled-by-default worker model routing policy and selector.

Provides deterministic, task-ID-based routing for worker tasks with
fail-closed policy validation, descriptor-based safe file loading,
and disabled-by-default behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping, NamedTuple, Optional, Union

SCHEMA_VERSION: str = "1.0"
OPUS_MODEL: str = "claude-opus-4-6-thinking"
OPUS_EFFORT: str = "model-default"
REQUIRED_PERCENT: int = 22

MAX_POLICY_FILE_BYTES: int = 4096  # 4 KiB limit
ROUTING_DOMAIN_SEPARATOR: str = "worker-model-routing:"
EXPECTED_POLICY_KEYS: frozenset[str] = frozenset(
    {"schema_version", "enabled", "percent", "model", "effort"}
)
DEFAULT_POLICY_PATH: str = "docs/refactor/two-server/templates/worker-model-routing.json"


class PolicyValidationError(ValueError):
    """Raised when worker routing policy fails validation or cannot be safely loaded."""


class WorkerRoute(NamedTuple):
    """Immutable worker model route representation."""

    model: str
    effort: str


@dataclass(frozen=True)
class WorkerRoutingPolicy:
    """Validated worker routing policy configuration."""

    schema_version: str
    enabled: bool
    percent: int
    model: str
    effort: str


def _reject_duplicate_keys(ordered_pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse JSON object rejecting any duplicate keys."""
    res: dict[str, Any] = {}
    for key, value in ordered_pairs:
        if key in res:
            raise PolicyValidationError(f"Duplicate key detected in policy JSON: {key!r}")
        res[key] = value
    return res


def validate_worker_routing_policy(
    data: Union[WorkerRoutingPolicy, Mapping[str, Any]],
) -> WorkerRoutingPolicy:
    """Validate policy mapping or WorkerRoutingPolicy against strict schema and value constraints.

    Enforces:
    - schema_version == '1.0'
    - enabled is boolean
    - percent == 22 (strictly fail-closed on 0, 21, 23, 100, etc.)
    - model == 'claude-opus-4-6-thinking'
    - effort == 'model-default'
    - exact expected keys with no missing or unexpected keys for mappings
    """
    if isinstance(data, WorkerRoutingPolicy):
        schema_version = data.schema_version
        enabled = data.enabled
        percent = data.percent
        model = data.model
        effort = data.effort
    elif isinstance(data, (dict, Mapping)):
        actual_keys = set(data.keys())
        if actual_keys != EXPECTED_POLICY_KEYS:
            missing = sorted(EXPECTED_POLICY_KEYS - actual_keys)
            unexpected = sorted(actual_keys - EXPECTED_POLICY_KEYS)
            err_parts = []
            if missing:
                err_parts.append(f"missing keys: {missing}")
            if unexpected:
                err_parts.append(f"unexpected keys: {unexpected}")
            raise PolicyValidationError(f"Policy schema mismatch: {'; '.join(err_parts)}")

        schema_version = data["schema_version"]
        enabled = data["enabled"]
        percent = data["percent"]
        model = data["model"]
        effort = data["effort"]
    else:
        raise PolicyValidationError(
            f"Policy must be a mapping or WorkerRoutingPolicy, got {type(data).__name__}"
        )

    if not isinstance(schema_version, str) or schema_version != SCHEMA_VERSION:
        raise PolicyValidationError(
            f"Invalid schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )

    if not isinstance(enabled, bool):
        raise PolicyValidationError(
            f"Field 'enabled' must be boolean, got {type(enabled).__name__}"
        )

    if isinstance(percent, bool) or not isinstance(percent, int):
        raise PolicyValidationError(
            f"Field 'percent' must be integer, got {type(percent).__name__}"
        )
    if percent != REQUIRED_PERCENT:
        raise PolicyValidationError(
            f"Field 'percent' must be exactly {REQUIRED_PERCENT}, got {percent}"
        )

    if not isinstance(model, str) or model != OPUS_MODEL:
        raise PolicyValidationError(f"Invalid model {model!r}; expected {OPUS_MODEL!r}")

    if not isinstance(effort, str) or effort != OPUS_EFFORT:
        raise PolicyValidationError(f"Invalid effort {effort!r}; expected {OPUS_EFFORT!r}")

    return WorkerRoutingPolicy(
        schema_version=schema_version,
        enabled=enabled,
        percent=percent,
        model=model,
        effort=effort,
    )


def load_worker_routing_policy(
    policy_path: Union[str, Path],
    *,
    base_dir: Optional[Union[str, Path]] = None,
) -> WorkerRoutingPolicy:
    """Strictly load and validate a worker routing policy JSON file using safe descriptor access.

    Enforces:
    - Path escape rejection if base_dir is supplied.
    - Safe open via descriptor with O_NOFOLLOW (rejects symlinks without TOCTOU window).
    - Regular non-symlink file check via fstat.
    - st_nlink == 1 check (rejects hardlinks).
    - File size <= 4096 bytes (4 KiB) and bounded descriptor read.
    - Valid UTF-8 encoding.
    - Duplicate key rejection in JSON.
    - Strict schema and value validation.
    """
    if not policy_path:
        raise PolicyValidationError("policy_path must not be empty")

    path_obj = Path(policy_path)

    if base_dir is not None:
        resolved_base = Path(base_dir).resolve()
        try:
            path_obj.resolve().relative_to(resolved_base)
        except ValueError as exc:
            raise PolicyValidationError(
                f"Path escape detected: {policy_path} resolves outside {base_dir}"
            ) from exc

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(str(path_obj), flags)
    except FileNotFoundError as exc:
        raise PolicyValidationError(f"Policy file not found: {policy_path}") from exc
    except OSError as exc:
        raise PolicyValidationError(
            f"Cannot safely open policy file {policy_path}: {exc}"
        ) from exc

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PolicyValidationError(f"Policy file is not a regular file: {policy_path}")

        if st.st_nlink != 1:
            raise PolicyValidationError(
                f"Policy file must have exactly 1 link (no hardlinks), got {st.st_nlink}: {policy_path}"
            )

        if st.st_size > MAX_POLICY_FILE_BYTES:
            raise PolicyValidationError(
                f"Policy file size {st.st_size} exceeds {MAX_POLICY_FILE_BYTES} bytes limit"
            )

        if base_dir is not None:
            resolved_base = Path(base_dir).resolve()
            proc_fd = Path(f"/proc/self/fd/{fd}")
            try:
                if proc_fd.exists():
                    proc_fd.resolve().relative_to(resolved_base)
            except ValueError as exc:
                raise PolicyValidationError(
                    f"Path escape detected via descriptor: {policy_path} resolves outside {base_dir}"
                ) from exc
            except OSError:
                pass

        chunks: list[bytes] = []
        total_read = 0
        while total_read <= MAX_POLICY_FILE_BYTES:
            chunk = os.read(fd, MAX_POLICY_FILE_BYTES + 1 - total_read)
            if not chunk:
                break
            chunks.append(chunk)
            total_read += len(chunk)
        raw_bytes = b"".join(chunks)
    finally:
        os.close(fd)

    if len(raw_bytes) > MAX_POLICY_FILE_BYTES:
        raise PolicyValidationError(
            f"Policy file content exceeds {MAX_POLICY_FILE_BYTES} bytes limit"
        )

    if not raw_bytes.strip():
        raise PolicyValidationError(f"Policy file is empty: {policy_path}")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyValidationError(f"Policy file is not valid UTF-8: {exc}") from exc

    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise PolicyValidationError(f"Malformed JSON in policy file: {exc}") from exc

    return validate_worker_routing_policy(data)


def compute_task_bucket(task_id: str) -> int:
    """Compute deterministic bucket in range [0, 99] using SHA-256 with fixed domain separation."""
    if not isinstance(task_id, str):
        raise TypeError(f"task_id must be a string, got {type(task_id).__name__}")
    if not task_id.strip():
        raise ValueError("task_id must not be empty or blank")

    payload = f"{ROUTING_DOMAIN_SEPARATOR}{task_id}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest, 16) % 100


def select_worker_model_route(
    task_id: str,
    policy: Optional[Union[WorkerRoutingPolicy, Mapping[str, Any]]] = None,
    *,
    default_model: str,
    default_effort: str,
    policy_path: Optional[Union[str, Path]] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> WorkerRoute:
    """Select worker model route.

    - Validates task_id and caller-supplied default_model/default_effort (fails closed on invalid input).
    - Always revalidates any supplied policy object/mapping.
    - If policy is disabled: returns caller default pair (default_model, default_effort).
    - If policy is enabled: uses SHA256 domain-separated immutable task_id modulo 100.
      If bucket < policy.percent (bucket < 22), returns Opus (model, effort).
      Otherwise returns caller default pair.
    """
    if not isinstance(task_id, str):
        raise TypeError(f"task_id must be a string, got {type(task_id).__name__}")
    if not task_id.strip():
        raise ValueError("task_id must not be empty or blank")

    if not isinstance(default_model, str) or not default_model.strip():
        raise ValueError("default_model must be a non-empty string")
    if not isinstance(default_effort, str) or not default_effort.strip():
        raise ValueError("default_effort must be a non-empty string")

    active_policy: WorkerRoutingPolicy
    if policy is not None:
        active_policy = validate_worker_routing_policy(policy)
    else:
        target_path = policy_path if policy_path is not None else DEFAULT_POLICY_PATH
        active_policy = load_worker_routing_policy(target_path, base_dir=base_dir)

    default_route = WorkerRoute(model=default_model, effort=default_effort)

    if not active_policy.enabled:
        return default_route

    bucket = compute_task_bucket(task_id)
    if bucket < active_policy.percent:
        return WorkerRoute(model=active_policy.model, effort=active_policy.effort)

    return default_route
