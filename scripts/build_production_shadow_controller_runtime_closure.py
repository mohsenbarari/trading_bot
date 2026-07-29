#!/usr/bin/env python3
"""Build one offline, controller-only convergence runtime closure.

The builder has no network, pip, Docker, SSH, Object Storage, or service
operation.  It accepts exactly three sealed wheel archives only after the
receipt digest is read from a fixed, root-only, campaign-bound held plan.  A
receipt and digest supplied by the same caller are deliberately insufficient.

This checkpoint is synthetic only.  The public CLI refuses to build a
production runtime until a separate held-FD exact-release bootstrap proves the
release/tree/blob identity before imports.

No Writer-Witness runtime artifact, lock file, manifest, wheelhouse, or
runtime path is consulted.  The controller closure is a distinct
release-bound artifact even where an upstream wheel digest happens to be
identical.
"""

from __future__ import annotations

import argparse
import base64
import csv
import ctypes
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import verify_production_shadow_controller_runtime_closure as VERIFY  # noqa: E402


POLICY_SCHEMA = "production-shadow-controller-runtime-closure-policy-v1"
EXTERNAL_INPUT_SCHEMA = "production-shadow-controller-external-wheel-input-v1"
WHEEL_INPUT_RECEIPT_SCHEMA = "production-shadow-controller-wheel-input-receipt-v1"
WHEEL_INPUT_RECEIPT_STATUS = "trusted-by-held-plan"
POLICY_RELATIVE = VERIFY.SOURCE_POLICY_RELATIVE
REQUIREMENTS_RELATIVE = "deploy/production-shadow-controller-runtime/requirements.lock"
WHEELHOUSE_RELATIVE = VERIFY.WHEELHOUSE_MANIFEST_RELATIVE
GIT = "/usr/bin/git"
MAX_POLICY_BYTES = 1024 * 1024
MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_WHEEL_MEMBERS = 100_000
MAX_EXTRACTED_FILE_BYTES = 64 * 1024 * 1024
MAX_STAGING_NAME_ATTEMPTS = 128
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
NORMALIZED_NAME_RE = re.compile(r"[-_.]+")
REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# The production target is explicitly Linux/x86_64.  `renameat2` with
# RENAME_NOREPLACE is deliberately required instead of falling back to
# `rename`, which could overwrite a directory created after the preflight.
LINUX_X86_64_RENAMEAT2 = 316
RENAME_NOREPLACE = 1
WHEEL_OWNED_TOP_LEVELS: Mapping[str, frozenset[str]] = {
    "cffi": frozenset({"_cffi_backend.cpython-312-x86_64-linux-gnu.so", "cffi"}),
    "cryptography": frozenset({"cryptography"}),
    "pycparser": frozenset({"pycparser"}),
}
POLICY_FIELDS = frozenset(
    {"schema", "namespace", "python", "packages", "site_packages", "wheel_input"}
)
POLICY_SITE_FIELDS = frozenset({"path", "import_origins"})
POLICY_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "held_root_only_plan_required",
        "caller_supplied_digest_allowed",
        "writer_witness_assets_used",
    }
)
INPUT_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "release",
        "source_policy_sha256",
        "controller_wheelhouse_sha256",
        "wheels",
        "input_receipt_sha256",
    }
)
INPUT_RECEIPT_WHEEL_FIELDS = frozenset(
    {"wheel", "archive_sha256", "record_sha256", "members_sha256"}
)
SAFE_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
}


class RuntimeClosureBuildError(RuntimeError):
    """The independently trusted controller closure cannot be built."""


@dataclass(frozen=True)
class ValidatedWheel:
    contract: Mapping[str, str]
    raw_sha256: str
    record_sha256: str
    members_sha256: str
    members: Mapping[str, bytes]


@dataclass(frozen=True)
class PreparedRuntimeClosure:
    campaign_id: str
    release_root: Path
    release_sha: str
    release_tree_sha: str
    source_policy_sha256: str
    wheelhouse_manifest_sha256: str
    held_plan_sha256: str
    wheel_input_receipt_sha256: str
    project_sources: Mapping[str, str]
    wheels: tuple[ValidatedWheel, ...]
    required_confirmation: str


@dataclass(frozen=True)
class TrustedWheelInputReceipt:
    sha256: str
    wheel_provenance: Mapping[str, Mapping[str, str]]


def _error(exc: Exception, message: str) -> RuntimeClosureBuildError:
    return RuntimeClosureBuildError(message)


def _sha256(value: bytes | Mapping[str, Any] | Sequence[Any]) -> str:
    payload = value if isinstance(value, bytes) else VERIFY.canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RuntimeClosureBuildError(f"{label} is not a SHA-256")
    return value


def _require_sha40(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise RuntimeClosureBuildError(f"{label} is not a Git SHA-1")
    return value


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        return VERIFY._strict_json(raw, label=label)  # noqa: SLF001
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc


def _read_release_file(
    release_descriptor: int,
    relative: str,
    *,
    expected_uid: int | None,
    maximum: int,
    label: str,
) -> bytes:
    try:
        descriptor, before = VERIFY._open_relative_regular(  # noqa: SLF001
            release_descriptor,
            relative,
            label=label,
            expected_uid=expected_uid,
            maximum=maximum,
        )
        try:
            return VERIFY._read_descriptor(  # noqa: SLF001
                descriptor,
                before,
                label=label,
                maximum=maximum,
            )
        finally:
            os.close(descriptor)
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc


def _read_absolute_regular(
    path: Path,
    *,
    expected_uid: int | None,
    maximum: int,
    label: str,
) -> bytes:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise RuntimeClosureBuildError(f"{label} path is invalid")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureBuildError(f"{label} parent is unavailable") from exc
    try:
        parent_descriptor = VERIFY._open_root(  # noqa: SLF001
            parent,
            label=f"{label} parent",
            expected_uid=expected_uid,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    try:
        return _read_release_file(
            parent_descriptor,
            path.name,
            expected_uid=expected_uid,
            maximum=maximum,
            label=label,
        )
    finally:
        os.close(parent_descriptor)


def _parse_policy(raw: bytes) -> dict[str, Any]:
    document = _strict_json(raw, label="controller runtime policy")
    if set(document) != POLICY_FIELDS:
        raise RuntimeClosureBuildError("controller runtime policy fields differ")
    if document.get("schema") != POLICY_SCHEMA or document.get("namespace") != VERIFY.RUNTIME_NAMESPACE:
        raise RuntimeClosureBuildError("controller runtime policy schema or namespace differs")
    python = document.get("python")
    if python != {
        "implementation": "cpython",
        "major": 3,
        "minor": 12,
        "architecture": "x86_64",
    }:
        raise RuntimeClosureBuildError("controller runtime policy Python binding differs")
    if document.get("packages") != list(VERIFY.REQUIRED_PACKAGES):
        raise RuntimeClosureBuildError("controller runtime policy package closure differs")
    site = document.get("site_packages")
    if (
        not isinstance(site, dict)
        or set(site) != POLICY_SITE_FIELDS
        or site.get("path") != VERIFY.SITE_PACKAGES_DIRECTORY
        or site.get("import_origins") != VERIFY.REQUIRED_IMPORT_ORIGINS
    ):
        raise RuntimeClosureBuildError("controller runtime policy site-packages binding differs")
    wheel_input = document.get("wheel_input")
    if (
        not isinstance(wheel_input, dict)
        or set(wheel_input) != POLICY_INPUT_FIELDS
        or wheel_input.get("schema") != EXTERNAL_INPUT_SCHEMA
        or wheel_input.get("status") != "external-independent-held-plan-required"
        or wheel_input.get("held_root_only_plan_required") is not True
        or wheel_input.get("caller_supplied_digest_allowed") is not False
        or wheel_input.get("writer_witness_assets_used") is not False
    ):
        raise RuntimeClosureBuildError("controller runtime policy wheel input boundary differs")
    return document


def _parse_requirements(raw: bytes) -> list[tuple[str, str]]:
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n") or b"\r" in raw:
        raise RuntimeClosureBuildError("controller requirements lock is not canonical")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeClosureBuildError("controller requirements lock is not ASCII") from exc
    entries: list[tuple[str, str]] = []
    for line in lines:
        if not line or line.count("==") != 1 or line.strip() != line:
            raise RuntimeClosureBuildError("controller requirements lock line is invalid")
        name, version = line.split("==", 1)
        if not name or not version or NORMALIZED_NAME_RE.sub("-", name).lower() != name:
            raise RuntimeClosureBuildError("controller requirements lock line is invalid")
        entries.append((name, version))
    expected = [(record["name"], record["version"]) for record in VERIFY.REQUIRED_PACKAGES]
    if entries != expected:
        raise RuntimeClosureBuildError("controller requirements lock closure differs")
    return entries


def _parse_wheelhouse_manifest(raw: bytes) -> dict[str, str]:
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n") or b"\r" in raw:
        raise RuntimeClosureBuildError("controller wheelhouse manifest is not canonical")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeClosureBuildError("controller wheelhouse manifest is not ASCII") from exc
    result: dict[str, str] = {}
    ordered: list[str] = []
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            raise RuntimeClosureBuildError("controller wheelhouse manifest line is invalid")
        digest, wheel = line[:64], line[66:]
        _require_sha256(digest, label="controller wheelhouse digest")
        if wheel in result or not wheel.endswith(".whl") or "/" in wheel or "\\" in wheel:
            raise RuntimeClosureBuildError("controller wheelhouse filename is invalid")
        result[wheel] = digest
        ordered.append(wheel)
    expected = {record["wheel"]: record["sha256"] for record in VERIFY.REQUIRED_PACKAGES}
    if result != expected or ordered != sorted(ordered):
        raise RuntimeClosureBuildError("controller wheelhouse closure differs")
    return result


def _git(release_root: Path, arguments: Sequence[str]) -> bytes:
    command = [
        GIT,
        "-C",
        str(release_root),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "protocol.file.allow=never",
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=SAFE_GIT_ENV,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeClosureBuildError("exact release Git verification is unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > MAX_WHEEL_BYTES or len(completed.stderr) > MAX_POLICY_BYTES:
        raise RuntimeClosureBuildError("exact release Git verification failed")
    return completed.stdout


def _git_text(release_root: Path, arguments: Sequence[str]) -> str:
    try:
        return _git(release_root, arguments).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeClosureBuildError("exact release Git output is not ASCII") from exc


def _verify_exact_release(
    release_root: Path,
    *,
    release_sha: str,
    release_tree_sha: str,
    source_bytes: Mapping[str, bytes],
) -> None:
    if not release_root.is_absolute() or release_root.resolve(strict=True) != release_root:
        raise RuntimeClosureBuildError("release root must be an absolute real directory")
    observed = {
        "root": _git_text(release_root, ["rev-parse", "--show-toplevel"]),
        "head": _git_text(release_root, ["rev-parse", "HEAD"]),
        "tree": _git_text(release_root, ["rev-parse", "HEAD^{tree}"]),
        "branch": _git_text(release_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "status": _git_text(release_root, ["status", "--porcelain=v1", "--untracked-files=all"]),
        "remote": _git_text(release_root, ["remote"]),
    }
    if (
        observed["root"] != str(release_root)
        or observed["head"] != release_sha
        or observed["tree"] != release_tree_sha
        or observed["branch"] != "HEAD"
        or observed["status"]
        or observed["remote"]
    ):
        raise RuntimeClosureBuildError("release must be exact, detached, clean, and remote-free")
    for relative, actual in sorted(source_bytes.items()):
        blob = _git(release_root, ["cat-file", "blob", f"{release_sha}:{relative}"])
        if blob != actual:
            raise RuntimeClosureBuildError("release source differs from the exact Git blob")


def _collect_plan_bound_project_sources(
    release_descriptor: int,
    *,
    held_plan: VERIFY.HeldRuntimePlan,
    expected_uid: int | None,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    expected = {
        relative: digest
        for relative, digest in held_plan.required_blobs.items()
        if relative.startswith(("core/", "scripts/"))
    }
    if not expected or not VERIFY.CONTROL_SOURCE_PATHS <= set(expected):
        raise RuntimeClosureBuildError("controller runtime held plan project source set differs")
    for relative, digest in sorted(expected.items()):
        raw = _read_release_file(
            release_descriptor,
            relative,
            expected_uid=expected_uid,
            maximum=MAX_POLICY_BYTES,
            label="controller runtime project source",
        )
        if _sha256(raw) != digest:
            raise RuntimeClosureBuildError("controller runtime held plan project source digest differs")
        result[relative] = raw
    return result


def _parse_input_receipt(
    raw: bytes,
    *,
    expected_digest: str,
    release_sha: str,
    release_tree_sha: str,
    policy_sha256: str,
    wheelhouse_sha256: str,
) -> TrustedWheelInputReceipt:
    expected = _require_sha256(expected_digest, label="external trusted wheel input receipt")
    if _sha256(raw) != expected:
        raise RuntimeClosureBuildError("external trusted wheel input receipt digest differs")
    document = _strict_json(raw, label="external trusted wheel input receipt")
    if set(document) != INPUT_RECEIPT_FIELDS or document.get("schema") != WHEEL_INPUT_RECEIPT_SCHEMA:
        raise RuntimeClosureBuildError("external trusted wheel input receipt fields differ")
    if document.get("status") != WHEEL_INPUT_RECEIPT_STATUS:
        raise RuntimeClosureBuildError("external trusted wheel input receipt status differs")
    if document.get("release") != {"commit_sha": release_sha, "tree_sha": release_tree_sha}:
        raise RuntimeClosureBuildError("external trusted wheel input receipt release differs")
    if (
        document.get("source_policy_sha256") != policy_sha256
        or document.get("controller_wheelhouse_sha256") != wheelhouse_sha256
    ):
        raise RuntimeClosureBuildError("external trusted wheel input receipt controller binding differs")
    if document.get("input_receipt_sha256") != _sha256(
        {key: document[key] for key in document if key != "input_receipt_sha256"}
    ):
        raise RuntimeClosureBuildError("external trusted wheel input receipt self digest differs")
    wheels = document.get("wheels")
    if not isinstance(wheels, list):
        raise RuntimeClosureBuildError("external trusted wheel input receipt wheels differ")
    expected_wheels = {record["wheel"]: record["sha256"] for record in VERIFY.REQUIRED_PACKAGES}
    actual: dict[str, Mapping[str, str]] = {}
    for row in wheels:
        if not isinstance(row, dict) or set(row) != INPUT_RECEIPT_WHEEL_FIELDS:
            raise RuntimeClosureBuildError("external trusted wheel input receipt wheel fields differ")
        wheel = row.get("wheel")
        if not isinstance(wheel, str) or wheel in actual:
            raise RuntimeClosureBuildError("external trusted wheel input receipt wheel differs")
        actual[wheel] = {
            "archive_sha256": _require_sha256(
                row.get("archive_sha256"), label="external trusted wheel archive"
            ),
            "record_sha256": _require_sha256(
                row.get("record_sha256"), label="external trusted wheel RECORD"
            ),
            "members_sha256": _require_sha256(
                row.get("members_sha256"), label="external trusted wheel members"
            ),
        }
    if (
        {wheel: row["archive_sha256"] for wheel, row in actual.items()} != expected_wheels
        or [row.get("wheel") for row in wheels] != sorted(expected_wheels)
    ):
        raise RuntimeClosureBuildError("external trusted wheel input receipt wheel closure differs")
    return TrustedWheelInputReceipt(sha256=expected, wheel_provenance=actual)


def _marker_applies(marker: str | None) -> bool:
    if marker is None or not marker.strip():
        return True
    value = marker.lower().replace('"', "'").strip()
    if "extra" in value:
        return False
    if "pypy" in value:
        return "!=" in value
    if "platform_python_implementation" in value or "implementation_name" in value:
        return "cpython" in value and "!=" not in value
    if "python_version" in value:
        if ">=" in value or "==" in value:
            return "3.12" in value or "3.1" in value or "3" in value
        if "<" in value:
            return False
    raise RuntimeClosureBuildError("wheel dependency marker is unsupported")


def _normalize_name(value: str) -> str:
    return NORMALIZED_NAME_RE.sub("-", value).lower()


def _wheel_member_path(value: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise RuntimeClosureBuildError("wheel member path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} or part.startswith(".") or VERIFY.SAFE_RELATIVE_RE.fullmatch(part) is None
        for part in path.parts
    ):
        raise RuntimeClosureBuildError("wheel member path is unsafe")
    if path.name.endswith((".pth", ".egg-link")) or path.name in {"sitecustomize.py", "usercustomize.py"}:
        raise RuntimeClosureBuildError("wheel member startup hook is forbidden")
    if ".data" in path.parts:
        raise RuntimeClosureBuildError("wheel data relocation is unsupported")
    return path.as_posix()


def _verify_record(members: Mapping[str, bytes], record_path: str) -> None:
    try:
        rows = list(csv.reader(io.StringIO(members[record_path].decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeClosureBuildError("wheel RECORD is invalid") from exc
    seen: set[str] = set()
    for row in rows:
        if len(row) != 3:
            raise RuntimeClosureBuildError("wheel RECORD row is invalid")
        path = _wheel_member_path(row[0])
        if path not in members or path in seen:
            raise RuntimeClosureBuildError("wheel RECORD membership differs")
        seen.add(path)
        digest, size = row[1], row[2]
        if path == record_path:
            if digest or size:
                raise RuntimeClosureBuildError("wheel RECORD self row differs")
            continue
        if not digest.startswith("sha256=") or not size.isdecimal():
            raise RuntimeClosureBuildError("wheel RECORD digest differs")
        try:
            expected = base64.urlsafe_b64decode(digest[7:] + "=" * (-len(digest[7:]) % 4))
        except (ValueError, TypeError) as exc:
            raise RuntimeClosureBuildError("wheel RECORD digest differs") from exc
        if expected != hashlib.sha256(members[path]).digest() or int(size) != len(members[path]):
            raise RuntimeClosureBuildError("wheel RECORD digest differs")
    if seen != set(members):
        raise RuntimeClosureBuildError("wheel RECORD does not cover every member")


def _wheel_dependencies(metadata: bytes, *, contract: Mapping[str, str]) -> set[str]:
    message = BytesParser(policy=email_policy).parsebytes(metadata)
    if _normalize_name(str(message.get("Name") or "")) != contract["name"] or str(message.get("Version") or "") != contract["version"]:
        raise RuntimeClosureBuildError("wheel METADATA identity differs")
    dependencies: set[str] = set()
    for raw in message.get_all("Requires-Dist", []):
        requirement, separator, marker = str(raw).partition(";")
        if separator and not _marker_applies(marker):
            continue
        match = REQUIREMENT_NAME_RE.match(requirement.strip())
        if match is None:
            raise RuntimeClosureBuildError("wheel dependency declaration is invalid")
        dependencies.add(_normalize_name(match.group(1)))
    expected = {
        "cryptography": {"cffi"},
        "cffi": {"pycparser"},
        "pycparser": set(),
    }[contract["name"]]
    if dependencies != expected:
        raise RuntimeClosureBuildError("wheel dependency closure differs")
    return dependencies


def _validate_wheel(raw: bytes, *, contract: Mapping[str, str]) -> ValidatedWheel:
    if len(raw) < 1 or len(raw) > MAX_WHEEL_BYTES or _sha256(raw) != contract["sha256"]:
        raise RuntimeClosureBuildError("controller wheel archive digest differs")
    members: dict[str, bytes] = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise RuntimeClosureBuildError("controller wheel archive is invalid") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_WHEEL_MEMBERS:
            raise RuntimeClosureBuildError("controller wheel archive member count differs")
        for info in infos:
            name = _wheel_member_path(info.filename.rstrip("/"))
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise RuntimeClosureBuildError("controller wheel archive contains a symlink")
            if info.is_dir():
                continue
            if name in members or info.file_size < 0 or info.file_size > MAX_EXTRACTED_FILE_BYTES:
                raise RuntimeClosureBuildError("controller wheel archive member differs")
            payload = archive.read(info)
            if len(payload) != info.file_size:
                raise RuntimeClosureBuildError("controller wheel archive member size differs")
            members[name] = payload
    metadata_paths = [path for path in members if path.endswith(".dist-info/METADATA")]
    record_paths = [path for path in members if path.endswith(".dist-info/RECORD")]
    expected_dist_info = f"{contract['name']}-{contract['version']}.dist-info"
    expected_metadata = f"{expected_dist_info}/METADATA"
    expected_record = f"{expected_dist_info}/RECORD"
    if metadata_paths != [expected_metadata] or record_paths != [expected_record]:
        raise RuntimeClosureBuildError("controller wheel metadata layout differs")
    _wheel_dependencies(members[expected_metadata], contract=contract)
    _verify_record(members, expected_record)
    allowed_top = set(WHEEL_OWNED_TOP_LEVELS[contract["name"]]) | {expected_dist_info}
    if any(PurePosixPath(path).parts[0] not in allowed_top for path in members):
        raise RuntimeClosureBuildError("controller wheel archive contains unsupported content")
    member_hashes = {path: _sha256(payload) for path, payload in sorted(members.items())}
    return ValidatedWheel(
        contract=contract,
        raw_sha256=_sha256(raw),
        record_sha256=_sha256(members[expected_record]),
        members_sha256=_sha256(member_hashes),
        members=members,
    )


def _read_wheels(
    wheelhouse: Path,
    *,
    expected_uid: int | None,
) -> tuple[ValidatedWheel, ...]:
    try:
        root = wheelhouse.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureBuildError("external trusted wheel input directory is unavailable") from exc
    try:
        root_descriptor = VERIFY._open_root(  # noqa: SLF001
            root,
            label="external trusted wheel input directory",
            expected_uid=expected_uid,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    try:
        try:
            names = set(os.listdir(root_descriptor))
        except OSError as exc:
            raise RuntimeClosureBuildError("external trusted wheel input cannot be listed") from exc
        expected_names = {record["wheel"] for record in VERIFY.REQUIRED_PACKAGES}
        if names != expected_names:
            raise RuntimeClosureBuildError("external trusted wheel input must contain exactly the controller wheels")
        wheels: list[ValidatedWheel] = []
        for contract in VERIFY.REQUIRED_PACKAGES:
            raw = _read_release_file(
                root_descriptor,
                contract["wheel"],
                expected_uid=expected_uid,
                maximum=MAX_WHEEL_BYTES,
                label="external trusted controller wheel",
            )
            wheels.append(_validate_wheel(raw, contract=contract))
        return tuple(wheels)
    finally:
        os.close(root_descriptor)


def _confirmation(campaign_id: str, release_sha: str, held_plan_sha256: str) -> str:
    return f"BUILD-CONTROLLER-RUNTIME-CLOSURE:{campaign_id}:{release_sha}:{held_plan_sha256}"


def prepare_runtime_closure(
    *,
    release_root: Path,
    campaign_id: str,
    wheelhouse: Path,
    wheel_input_receipt: Path,
    trusted_plan_root: Path = VERIFY.HELD_RUNTIME_PLAN_ROOT,
    expected_uid: int | None = 0,
) -> PreparedRuntimeClosure:
    try:
        held_plan = VERIFY.read_held_runtime_plan(
            campaign_id,
            expected_uid=expected_uid,
            plan_root=trusted_plan_root,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    release_sha = held_plan.release_sha
    release_tree_sha = held_plan.release_tree_sha
    try:
        canonical_release = release_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureBuildError("release root is unavailable") from exc
    if canonical_release != release_root or not canonical_release.is_absolute():
        raise RuntimeClosureBuildError("release root must be absolute and real")
    try:
        release_descriptor = VERIFY._open_root(  # noqa: SLF001
            canonical_release,
            label="controller runtime release root",
            expected_uid=expected_uid,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    try:
        policy_raw = _read_release_file(
            release_descriptor,
            POLICY_RELATIVE,
            expected_uid=expected_uid,
            maximum=MAX_POLICY_BYTES,
            label="controller runtime policy",
        )
        requirements_raw = _read_release_file(
            release_descriptor,
            REQUIREMENTS_RELATIVE,
            expected_uid=expected_uid,
            maximum=MAX_POLICY_BYTES,
            label="controller runtime requirements lock",
        )
        wheelhouse_raw = _read_release_file(
            release_descriptor,
            WHEELHOUSE_RELATIVE,
            expected_uid=expected_uid,
            maximum=MAX_POLICY_BYTES,
            label="controller runtime wheelhouse manifest",
        )
        _parse_policy(policy_raw)
        _parse_requirements(requirements_raw)
        _parse_wheelhouse_manifest(wheelhouse_raw)
        source_bytes = _collect_plan_bound_project_sources(
            release_descriptor,
            held_plan=held_plan,
            expected_uid=expected_uid,
        )
    finally:
        os.close(release_descriptor)
    policy_sha256 = _sha256(policy_raw)
    wheelhouse_sha256 = _sha256(wheelhouse_raw)
    if (
        held_plan.source_policy_sha256 != policy_sha256
        or held_plan.wheelhouse_manifest_sha256 != wheelhouse_sha256
    ):
        raise RuntimeClosureBuildError("controller runtime held plan release inputs differ")
    input_receipt_raw = _read_absolute_regular(
        wheel_input_receipt,
        expected_uid=expected_uid,
        maximum=MAX_POLICY_BYTES,
        label="external trusted wheel input receipt",
    )
    trusted_input_receipt = _parse_input_receipt(
        input_receipt_raw,
        expected_digest=held_plan.wheel_input_receipt_sha256,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        policy_sha256=policy_sha256,
        wheelhouse_sha256=wheelhouse_sha256,
    )
    all_release_sources = {
        POLICY_RELATIVE: policy_raw,
        REQUIREMENTS_RELATIVE: requirements_raw,
        WHEELHOUSE_RELATIVE: wheelhouse_raw,
        **source_bytes,
    }
    _verify_exact_release(
        canonical_release,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        source_bytes=all_release_sources,
    )
    wheels = _read_wheels(wheelhouse, expected_uid=expected_uid)
    for wheel in wheels:
        expected_wheel = trusted_input_receipt.wheel_provenance[wheel.contract["wheel"]]
        if (
            wheel.raw_sha256 != expected_wheel["archive_sha256"]
            or wheel.record_sha256 != expected_wheel["record_sha256"]
            or wheel.members_sha256 != expected_wheel["members_sha256"]
        ):
            raise RuntimeClosureBuildError("external trusted wheel input receipt provenance differs")
    return PreparedRuntimeClosure(
        campaign_id=held_plan.campaign_id,
        release_root=canonical_release,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        source_policy_sha256=policy_sha256,
        wheelhouse_manifest_sha256=wheelhouse_sha256,
        held_plan_sha256=held_plan.sha256,
        wheel_input_receipt_sha256=trusted_input_receipt.sha256,
        project_sources={path: _sha256(raw) for path, raw in sorted(source_bytes.items())},
        wheels=wheels,
        required_confirmation=_confirmation(
            held_plan.campaign_id,
            release_sha,
            held_plan.sha256,
        ),
    )


def _write_private(path: Path, payload: bytes, *, root: Path | None = None) -> None:
    if root is None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
    else:
        try:
            relative_parent = path.parent.relative_to(root)
        except ValueError as exc:
            raise RuntimeClosureBuildError("controller runtime output path escaped its root") from exc
        current = root
        current.chmod(0o700)
        for part in relative_parent.parts:
            current = current / part
            current.mkdir(mode=0o700, exist_ok=True)
            current.chmod(0o700)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise RuntimeClosureBuildError("controller runtime output cannot be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_staging_directory(parent_descriptor: int) -> tuple[str, Path, int]:
    """Create and hold a root-only staging directory under one opened parent."""

    for _ in range(MAX_STAGING_NAME_ATTEMPTS):
        name = f".controller-runtime-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise RuntimeClosureBuildError("controller runtime staging directory cannot be created") from exc
        try:
            descriptor = VERIFY._open_child_directory(  # noqa: SLF001
                parent_descriptor,
                name,
                label="controller runtime staging directory",
                expected_uid=None,
            )
        except VERIFY.RuntimeClosureError as exc:
            try:
                os.rmdir(name, dir_fd=parent_descriptor)
            except OSError:
                pass
            raise RuntimeClosureBuildError(str(exc)) from exc
        return name, Path("/proc/self/fd") / str(parent_descriptor) / name, descriptor
    raise RuntimeClosureBuildError("controller runtime staging directory name space is exhausted")


def _rename_no_replace(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    """Atomically publish a staged directory only when its destination is absent."""

    try:
        platform = os.uname()
    except AttributeError as exc:
        raise RuntimeClosureBuildError("controller runtime requires Linux renameat2 support") from exc
    if platform.sysname != "Linux" or platform.machine != "x86_64":
        raise RuntimeClosureBuildError("controller runtime requires Linux/x86_64 renameat2 support")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
    except (AttributeError, OSError) as exc:
        raise RuntimeClosureBuildError("controller runtime renameat2 support is unavailable") from exc
    result = syscall(
        ctypes.c_long(LINUX_X86_64_RENAMEAT2),
        ctypes.c_int(source_parent_descriptor),
        ctypes.c_char_p(os.fsencode(source_name)),
        ctypes.c_int(destination_parent_descriptor),
        ctypes.c_char_p(os.fsencode(destination_name)),
        ctypes.c_uint(RENAME_NOREPLACE),
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise RuntimeClosureBuildError("controller runtime destination already exists")
    if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        raise RuntimeClosureBuildError("controller runtime renameat2 no-replace is unavailable")
    raise RuntimeClosureBuildError("controller runtime destination could not be published")


def _assert_materialization_lease(
    lease: object,
    prepared: PreparedRuntimeClosure,
    held_bootstrap_capability: object | None,
) -> None:
    """Bind a pre-consumed held-FD lease to exactly one prepared closure."""

    try:
        VERIFY._assert_registered_held_bootstrap_pair(  # noqa: SLF001
            held_bootstrap_capability,
            lease,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    assert_for = getattr(lease, "assert_for", None)
    if not callable(assert_for):
        raise RuntimeClosureBuildError("controller runtime materialization requires a held-FD bootstrap lease")
    try:
        assert_for(
            operation="materialize-runtime-closure",
            campaign_id=prepared.campaign_id,
            release_sha=prepared.release_sha,
            release_tree_sha=prepared.release_tree_sha,
            held_plan_sha256=prepared.held_plan_sha256,
        )
    except Exception as exc:
        raise RuntimeClosureBuildError("controller runtime materialization held-FD lease was rejected") from exc
    if (
        getattr(lease, "source_policy_sha256", None) != prepared.source_policy_sha256
        or getattr(lease, "wheelhouse_manifest_sha256", None) != prepared.wheelhouse_manifest_sha256
        or getattr(lease, "wheel_input_receipt_sha256", None) != prepared.wheel_input_receipt_sha256
    ):
        raise RuntimeClosureBuildError("controller runtime prepared closure differs from held-FD plan")
    required_blobs = getattr(lease, "required_blobs", None)
    expected_sources = (
        {
            path: digest
            for path, digest in required_blobs.items()
            if isinstance(path, str) and path.startswith(("core/", "scripts/"))
        }
        if isinstance(required_blobs, Mapping)
        else None
    )
    if expected_sources != dict(prepared.project_sources):
        raise RuntimeClosureBuildError("controller runtime project sources differ from held-FD plan")


def _claim_materialization_lease(
    held_bootstrap_capability: object | None,
    prepared: PreparedRuntimeClosure,
) -> object:
    try:
        lease = VERIFY._claim_held_bootstrap_capability(  # noqa: SLF001
            held_bootstrap_capability,
            operation="materialize-runtime-closure",
            campaign_id=prepared.campaign_id,
            release_sha=prepared.release_sha,
            release_tree_sha=prepared.release_tree_sha,
            held_plan_sha256=prepared.held_plan_sha256,
            release_root_descriptor=None,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    _assert_materialization_lease(lease, prepared, held_bootstrap_capability)
    return lease


def _materialize(
    prepared: PreparedRuntimeClosure,
    *,
    destination: Path,
    expected_uid: int | None,
    held_bootstrap_capability: object | None = None,
    _held_bootstrap_materialization_lease: object | None = None,
) -> dict[str, Any]:
    lease = _held_bootstrap_materialization_lease
    if lease is None:
        lease = _claim_materialization_lease(held_bootstrap_capability, prepared)
    else:
        _assert_materialization_lease(lease, prepared, held_bootstrap_capability)
    if (
        not destination.is_absolute()
        or destination == Path("/")
        or VERIFY.SAFE_RELATIVE_RE.fullmatch(destination.name) is None
        or destination.name.startswith(".")
    ):
        raise RuntimeClosureBuildError("controller runtime destination must be a new absolute path")
    supplied_parent = destination.parent
    try:
        parent = supplied_parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeClosureBuildError("controller runtime destination parent is unavailable") from exc
    if parent != supplied_parent:
        raise RuntimeClosureBuildError("controller runtime destination parent must be canonical")
    try:
        parent_metadata = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise RuntimeClosureBuildError("controller runtime destination parent is unavailable") from exc
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or (expected_uid is not None and parent_metadata.st_uid != expected_uid)
        or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise RuntimeClosureBuildError("controller runtime destination parent is unsafe")
    try:
        parent_descriptor = VERIFY._open_root(  # noqa: SLF001
            parent,
            label="controller runtime destination parent",
            expected_uid=expected_uid,
        )
    except VERIFY.RuntimeClosureError as exc:
        raise RuntimeClosureBuildError(str(exc)) from exc
    temporary_name = ""
    temporary = Path("/")
    temporary_descriptor = -1
    published = False
    try:
        try:
            os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeClosureBuildError("controller runtime destination cannot be inspected") from exc
        else:
            raise RuntimeClosureBuildError("controller runtime destination already exists")
        temporary_name, temporary, temporary_descriptor = _create_staging_directory(parent_descriptor)
        site_root = temporary / VERIFY.SITE_PACKAGES_DIRECTORY
        site_root.mkdir(mode=0o700)
        installed: list[dict[str, Any]] = []
        wheel_rows: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for wheel in prepared.wheels:
            rows: list[dict[str, Any]] = []
            for member, payload in sorted(wheel.members.items()):
                if member in seen_paths:
                    raise RuntimeClosureBuildError("controller wheels overlap in an installed path")
                seen_paths.add(member)
                target = site_root / member
                _write_private(target, payload, root=site_root)
                row = {
                    "path": member,
                    "size": len(payload),
                    "sha256": _sha256(payload),
                    "source_wheel": wheel.contract["wheel"],
                    "source_member": member,
                }
                rows.append(row)
                installed.append(row)
            wheel_rows.append(
                {
                    "wheel": wheel.contract["wheel"],
                    "archive_sha256": wheel.raw_sha256,
                    "record_sha256": wheel.record_sha256,
                    "members_sha256": wheel.members_sha256,
                    "installed_files_sha256": _sha256(rows),
                }
            )
        installed.sort(key=lambda row: row["path"])
        wheel_rows.sort(key=lambda row: row["wheel"])
        receipt: dict[str, Any] = {
            "schema": VERIFY.WHEEL_RECEIPT_SCHEMA,
            "namespace": VERIFY.RUNTIME_NAMESPACE,
            "campaign_id": prepared.campaign_id,
            "release": {"commit_sha": prepared.release_sha, "tree_sha": prepared.release_tree_sha},
            "source_policy_sha256": prepared.source_policy_sha256,
            "controller_wheelhouse_sha256": prepared.wheelhouse_manifest_sha256,
            "held_plan_sha256": prepared.held_plan_sha256,
            "wheel_input_receipt_sha256": prepared.wheel_input_receipt_sha256,
            "wheels": wheel_rows,
            "installed_files": installed,
        }
        receipt["receipt_sha256"] = _sha256(receipt)
        receipt_payload = VERIFY.canonical_json_bytes(receipt)
        _write_private(temporary / VERIFY.WHEEL_RECEIPT_FILENAME, receipt_payload)
        site_files = {row["path"]: row["sha256"] for row in installed}
        manifest: dict[str, Any] = {
            "schema": VERIFY.RUNTIME_CLOSURE_SCHEMA,
            "namespace": VERIFY.RUNTIME_NAMESPACE,
            "campaign_id": prepared.campaign_id,
            "release": {"commit_sha": prepared.release_sha, "tree_sha": prepared.release_tree_sha},
            "python": {
                "implementation": "cpython",
                "major": 3,
                "minor": 12,
                "architecture": "x86_64",
            },
            "source_policy_sha256": prepared.source_policy_sha256,
            "wheelhouse_manifest_sha256": prepared.wheelhouse_manifest_sha256,
            "held_plan_sha256": prepared.held_plan_sha256,
            "wheel_input_receipt_sha256": prepared.wheel_input_receipt_sha256,
            "packages": list(VERIFY.REQUIRED_PACKAGES),
            "site_packages": {
                "path": VERIFY.SITE_PACKAGES_DIRECTORY,
                "files": site_files,
                "files_sha256": VERIFY._hash_mapping(site_files),  # noqa: SLF001
                "import_origins": dict(VERIFY.REQUIRED_IMPORT_ORIGINS),
            },
            "project_sources": dict(prepared.project_sources),
            "control_sources": {
                path: prepared.project_sources[path]
                for path in sorted(VERIFY.CONTROL_SOURCE_PATHS)
            },
            "wheel_installation_receipt_sha256": _sha256(receipt_payload),
        }
        manifest["runtime_binding_sha256"] = _sha256(manifest)
        _write_private(
            temporary / VERIFY.RUNTIME_MANIFEST_FILENAME,
            VERIFY.canonical_json_bytes(manifest),
        )
        # Re-open and rescan exactly as the future pre-import bootstrap will.
        attestation = VERIFY.attest_runtime_closure(
            temporary,
            prepared.release_root,
            expected_uid=expected_uid,
            expected_campaign_id=prepared.campaign_id,
            expected_release_sha=prepared.release_sha,
            expected_release_tree_sha=prepared.release_tree_sha,
            expected_held_plan_sha256=prepared.held_plan_sha256,
            held_bootstrap_capability=held_bootstrap_capability,
        )
        attestation.close()
        # The verifier's path API needs a real release root, not stdin.  Its
        # source re-check is performed by the caller before materialization;
        # the output re-scan below is therefore intentionally filesystem-only.
        staging_metadata = os.fstat(temporary_descriptor)
        _rename_no_replace(
            parent_descriptor,
            temporary_name,
            parent_descriptor,
            destination.name,
        )
        published = True
        try:
            published_metadata = os.stat(
                destination.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise RuntimeClosureBuildError("published controller runtime cannot be inspected") from exc
        if (
            not stat.S_ISDIR(published_metadata.st_mode)
            or (published_metadata.st_dev, published_metadata.st_ino)
            != (staging_metadata.st_dev, staging_metadata.st_ino)
        ):
            raise RuntimeClosureBuildError("published controller runtime identity differs")
        return {
            "runtime_root": str(destination),
            "manifest_sha256": _sha256(VERIFY.canonical_json_bytes(manifest)),
            "wheel_receipt_sha256": _sha256(receipt_payload),
            "site_file_count": len(installed),
        }
    except Exception:
        if temporary_name and not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        os.close(parent_descriptor)


def build_runtime_closure(
    prepared: PreparedRuntimeClosure,
    *,
    destination: Path,
    confirm: str,
    expected_uid: int | None = 0,
    held_bootstrap_capability: object | None = None,
) -> dict[str, Any]:
    if confirm != prepared.required_confirmation:
        raise RuntimeClosureBuildError("controller runtime build requires exact digest-bound confirmation")
    lease = _claim_materialization_lease(held_bootstrap_capability, prepared)
    return _materialize(
        prepared,
        destination=destination,
        expected_uid=expected_uid,
        held_bootstrap_capability=held_bootstrap_capability,
        _held_bootstrap_materialization_lease=lease,
    )


def _require_root_cli() -> None:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RuntimeClosureBuildError("controller runtime build CLI requires root:root")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--wheel-input-receipt", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _require_root_cli()
        if args.apply:
            raise RuntimeClosureBuildError(
                "controller runtime build CLI is unavailable pending held-FD exact-release bootstrap"
            )
        prepared = prepare_runtime_closure(
            release_root=args.release_root,
            campaign_id=args.campaign_id,
            wheelhouse=args.wheelhouse,
            wheel_input_receipt=args.wheel_input_receipt,
            expected_uid=0,
        )
        result: dict[str, Any] = {
            "status": "synthetic-planned-not-operational",
            "campaign_id": prepared.campaign_id,
            "release_sha": prepared.release_sha,
            "release_tree_sha": prepared.release_tree_sha,
            "held_plan_sha256": prepared.held_plan_sha256,
            "wheel_input_receipt_sha256": prepared.wheel_input_receipt_sha256,
            "required_confirmation": prepared.required_confirmation,
            "external_trust_input": "fixed root-only campaign held plan for independently trusted controller wheel receipt",
        }
        if args.destination is not None or args.confirm:
            raise RuntimeClosureBuildError("controller runtime plan does not accept destination or confirmation")
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except (RuntimeClosureBuildError, VERIFY.RuntimeClosureError, OSError, ValueError) as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
