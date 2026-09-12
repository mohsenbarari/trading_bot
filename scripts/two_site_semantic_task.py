"""Pure semantic-identity primitive for two-site tasks."""
from __future__ import annotations

import copy, hashlib, json, re
from typing import Any

__all__ = ["SemanticTaskError", "build_identity", "validate_identity"]

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_KEYS = frozenset({
    "schema_version", "stage_id", "gap_reference", "scenario_key",
    "symbol_groups", "source_sha256", "source_manifest", "fingerprint",
})


class SemanticTaskError(ValueError):
    """Raised when semantic task identity inputs or schemas are invalid."""


def _canon_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _check_path(path: Any) -> str:
    if not isinstance(path, str) or not path or path != path.strip():
        raise SemanticTaskError(f"invalid path: {path!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in path) or "\\" in path or path.startswith("/"):
        raise SemanticTaskError(f"invalid path chars/prefix: {path!r}")
    if any(p in ("", ".", "..") for p in path.split("/")):
        raise SemanticTaskError(f"invalid path segments: {path!r}")
    return path


def _check_scalar(name: str, val: Any) -> str:
    if not isinstance(val, str) or not _ID_RE.fullmatch(val):
        raise SemanticTaskError(f"invalid {name}: {val!r}")
    return val


def _check_gap_ref(ref: Any) -> str:
    if not isinstance(ref, str) or not (1 <= len(ref) <= 500) or ref != ref.strip():
        raise SemanticTaskError(f"invalid gap_reference: {ref!r}")
    if len(ref.encode("utf-8")) > 500 or any(ord(c) < 32 or ord(c) == 127 for c in ref) or "\\" in ref:
        raise SemanticTaskError(f"invalid gap_reference chars/length: {ref!r}")
    if ref.count("#") != 1:
        raise SemanticTaskError(f"gap_reference must have single #anchor: {ref!r}")
    path_part, anchor = ref.split("#")
    if not anchor:
        raise SemanticTaskError(f"gap_reference anchor empty: {ref!r}")
    _check_path(path_part)
    return ref


def build_identity(
    stage_id: str,
    gap_reference: str,
    scenario_key: str,
    symbol_groups: list[dict[str, Any]],
    source_manifest: dict[str, str | None],
) -> dict[str, Any]:
    st_id = _check_scalar("stage_id", stage_id)
    gap_ref = _check_gap_ref(gap_reference)
    sc_key = _check_scalar("scenario_key", scenario_key)

    if not isinstance(symbol_groups, list) or len(symbol_groups) > 32:
        raise SemanticTaskError("symbol_groups must be list of <= 32 items")
    seen_paths: set[str] = set()
    canon_groups: list[dict[str, Any]] = []
    for g in symbol_groups:
        if not isinstance(g, dict) or set(g.keys()) != {"path", "symbols"}:
            raise SemanticTaskError("group must have exact keys {path, symbols}")
        p = _check_path(g["path"])
        if p in seen_paths:
            raise SemanticTaskError(f"duplicate path in symbol_groups: {p}")
        seen_paths.add(p)
        syms = g["symbols"]
        if not isinstance(syms, list) or not (1 <= len(syms) <= 32):
            raise SemanticTaskError(f"symbols in {p} must be list of 1..32 items")
        seen_syms: set[str] = set()
        for s in syms:
            if not isinstance(s, str) or not s.isidentifier() or not s.isascii():
                raise SemanticTaskError(f"invalid Python identifier: {s!r}")
            if s in seen_syms:
                raise SemanticTaskError(f"duplicate symbol: {s!r}")
            seen_syms.add(s)
        canon_groups.append({"path": p, "symbols": sorted(syms)})
    canon_groups.sort(key=lambda x: x["path"])

    if not isinstance(source_manifest, dict) or not (1 <= len(source_manifest) <= 64):
        raise SemanticTaskError("source_manifest must be dict of 1..64 entries")
    for p, sha in source_manifest.items():
        _check_path(p)
        if sha is not None and (not isinstance(sha, str) or not _HEX_RE.fullmatch(sha)):
            raise SemanticTaskError(f"invalid sha256 for {p}: {sha!r}")
    canon_manifest: dict[str, str | None] = {p: source_manifest[p] for p in sorted(source_manifest.keys())}

    src_sha = hashlib.sha256(_canon_json(canon_manifest)).hexdigest()
    fp_payload = {
        "stage_id": st_id,
        "gap_reference": gap_ref,
        "scenario_key": sc_key,
        "symbol_groups": canon_groups,
        "source_sha256": src_sha,
    }
    return {
        "schema_version": 1,
        "stage_id": st_id,
        "gap_reference": gap_ref,
        "scenario_key": sc_key,
        "symbol_groups": copy.deepcopy(canon_groups),
        "source_sha256": src_sha,
        "source_manifest": copy.deepcopy(canon_manifest),
        "fingerprint": hashlib.sha256(_canon_json(fp_payload)).hexdigest(),
    }


def validate_identity(identity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(identity, dict) or set(identity.keys()) != _KEYS:
        raise SemanticTaskError("identity must be dict with exact schema keys")
    if identity.get("schema_version") != 1 or type(identity["schema_version"]) is not int:
        raise SemanticTaskError("invalid schema_version")

    sym_groups = identity.get("symbol_groups")
    if not isinstance(sym_groups, list):
        raise SemanticTaskError("symbol_groups must be a list")
    group_paths: list[str] = []
    for g in sym_groups:
        if not isinstance(g, dict) or set(g.keys()) != {"path", "symbols"}:
            raise SemanticTaskError("each symbol group must have exact keys {path, symbols}")
        group_paths.append(_check_path(g.get("path")))
        syms = g.get("symbols")
        if not isinstance(syms, list) or any(not isinstance(s, str) for s in syms) or syms != sorted(syms):
            raise SemanticTaskError("symbols must be a sorted list of strings")
    if group_paths != sorted(group_paths):
        raise SemanticTaskError("symbol_groups must be in sorted path order")

    manifest = identity.get("source_manifest")
    if not isinstance(manifest, dict):
        raise SemanticTaskError("source_manifest must be a dict")
    for k in manifest.keys():
        _check_path(k)
    if list(manifest.keys()) != sorted(manifest.keys()):
        raise SemanticTaskError("source_manifest keys must be in sorted order")

    rebuilt = build_identity(
        stage_id=identity.get("stage_id"),  # type: ignore
        gap_reference=identity.get("gap_reference"),  # type: ignore
        scenario_key=identity.get("scenario_key"),  # type: ignore
        symbol_groups=sym_groups,
        source_manifest=manifest,
    )
    if identity.get("source_sha256") != rebuilt["source_sha256"]:
        raise SemanticTaskError("tampered source_sha256")
    if identity.get("fingerprint") != rebuilt["fingerprint"]:
        raise SemanticTaskError("tampered fingerprint")
    return rebuilt
