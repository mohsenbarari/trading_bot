"""Candidate-test receipt module for pre-review sandbox validation."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts import two_site_agent_sandbox
    from scripts.two_site_agent_sandbox import SandboxError, _validate_overlay_path
except ImportError:
    import two_site_agent_sandbox
    from two_site_agent_sandbox import SandboxError, _validate_overlay_path

try:
    from scripts.two_site_pre_review_tests import (
        normalize_test_selectors,
        tracked_test_inventory,
    )
except ImportError:
    from two_site_pre_review_tests import (
        normalize_test_selectors,
        tracked_test_inventory,
    )

__all__ = ["run_candidate_tests"]

MAX_SPECS, MAX_CHANGES, MAX_SELECTORS, MAX_TAIL_BYTES = 32, 32, 200, 8192
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _bound_utf8_tail(text: str, max_bytes: int = MAX_TAIL_BYTES) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    tail = encoded[-max_bytes:]
    for offset in range(min(4, len(tail))):
        try:
            return tail[offset:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return tail.decode("utf-8", errors="ignore")


def run_candidate_tests(
    root: str | Path,
    task: Mapping[str, Any],
    worker_result: Mapping[str, Any],
    proposal_sha256: str,
    timeout: int = 1200,
) -> dict[str, Any]:
    """Execute candidate tests through sandbox and return bounded receipt."""
    if isinstance(root, str):
        if not root.strip():
            raise SandboxError("root must be a nonempty path")
        root = Path(root)
    elif not isinstance(root, Path):
        raise SandboxError(f"root must be a str or Path, got {type(root).__name__}")
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SandboxError("root must be an existing directory") from exc
    if not resolved_root.is_dir():
        raise SandboxError("root must be an existing directory")

    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.fullmatch(proposal_sha256):
        raise SandboxError("proposal_sha256 must be a lowercase 64-hex SHA")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not (1 <= timeout <= 3600):
        raise SandboxError("timeout must be an integer between 1 and 3600")

    if not isinstance(task, Mapping) or isinstance(task, (str, bytes)):
        raise SandboxError("task must be a mapping")
    raw_specs = task.get("test_selectors")
    if (not isinstance(raw_specs, (list, tuple)) or isinstance(raw_specs, (str, bytes))
            or not (1 <= len(raw_specs) <= MAX_SPECS)
            or any(not isinstance(s, str) for s in raw_specs)):
        raise SandboxError("task test_selectors must be 1..32 strings")

    if not isinstance(worker_result, Mapping) or isinstance(worker_result, (str, bytes)):
        raise SandboxError("worker_result must be a mapping")
    raw_changes = worker_result.get("changes")
    if (not isinstance(raw_changes, (list, tuple)) or isinstance(raw_changes, (str, bytes))
            or not (1 <= len(raw_changes) <= MAX_CHANGES)):
        raise SandboxError("Worker changes must be 1..32 entries")

    overlays: dict[str, str] = {}
    for change in raw_changes:
        if not isinstance(change, Mapping) or isinstance(change, (str, bytes)):
            raise SandboxError("Worker change must be a mapping")
        path, content = change.get("path"), change.get("content")
        if not isinstance(path, str) or not path.strip():
            raise SandboxError("Worker change path must be a nonempty string")
        _validate_overlay_path(path)
        if path in overlays:
            raise SandboxError(f"duplicate overlay path: {path}")
        if not isinstance(content, str) or not content or "\x00" in content:
            raise SandboxError("Worker change content must be nonempty NUL-free string")
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SandboxError("Worker change content must be valid UTF-8") from exc
        overlays[path] = content

    inventory = tracked_test_inventory(resolved_root, list(overlays.keys()))
    chosen_selectors: list[str] = []
    chosen_set: set[str] = set()
    unsupported_specs: list[str] = []
    seen_unsupported: set[str] = set()

    for spec in raw_specs:
        try:
            spec_selectors, _ = normalize_test_selectors([spec], inventory)
        except SandboxError:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)
            continue
        if not spec_selectors:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)
            continue

        new_selectors: list[str] = []
        for s in spec_selectors:
            if s not in chosen_set and s not in new_selectors:
                new_selectors.append(s)

        if len(chosen_selectors) + len(new_selectors) <= MAX_SELECTORS:
            for s in new_selectors:
                chosen_selectors.append(s)
                chosen_set.add(s)
        else:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)

    if not chosen_selectors:
        raise SandboxError("at least one focused selector required")

    sandbox_result = two_site_agent_sandbox.run_tests(
        resolved_root,
        chosen_selectors,
        timeout=timeout,
        overlays=overlays,
    )

    if not isinstance(sandbox_result, Mapping) or isinstance(sandbox_result, (str, bytes)):
        raise SandboxError("malformed sandbox result: expected mapping")
    rc = sandbox_result.get("returncode")
    if not isinstance(rc, int) or isinstance(rc, bool):
        raise SandboxError("malformed sandbox result: returncode must be int")
    stdout, stderr = sandbox_result.get("stdout"), sandbox_result.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise SandboxError("malformed sandbox result: stdout/stderr must be str")
    snapshot_files = sandbox_result.get("snapshot_files")
    snapshot_bytes = sandbox_result.get("snapshot_bytes")
    for name, val in (("snapshot_files", snapshot_files), ("snapshot_bytes", snapshot_bytes)):
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise SandboxError(f"malformed sandbox result: {name} must be non-negative int")
    isolation = sandbox_result.get("isolation")
    if not isinstance(isolation, str) or not isolation:
        raise SandboxError("malformed sandbox result: isolation must be non-empty str")

    return {
        "state": "PASSED" if rc == 0 else "FAILED",
        "proposal_sha256": proposal_sha256,
        "selectors": chosen_selectors,
        "unsupported_specs": unsupported_specs,
        "returncode": rc,
        "stdout_tail": _bound_utf8_tail(stdout),
        "stderr_tail": _bound_utf8_tail(stderr),
        "snapshot_files": snapshot_files,
        "snapshot_bytes": snapshot_bytes,
        "isolation": isolation,
    }
