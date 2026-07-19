#!/usr/bin/env python3
"""Fail-closed secret and PII scanner for Telegram queue evidence artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from typing import Iterable
import zipfile


MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_DEPTH = 3

_SAFE_REDACTIONS = {
    "",
    "-",
    "none",
    "null",
    "omitted",
    "redacted",
    "[redacted]",
    "<redacted>",
    "***",
}

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "telegram_bot_token",
        re.compile(r"(?<![A-Za-z0-9_-])\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])"),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}\b", re.IGNORECASE),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "credential_url",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", re.IGNORECASE),
    ),
    ("iran_mobile", re.compile(r"(?<!\d)09\d{9}(?!\d)")),
    (
        "email_address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
)

_SOURCE_SECRET_KINDS = frozenset(
    {"telegram_bot_token", "private_key", "bearer_token", "jwt"}
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?:[\"']?)"
    r"(bot_token|authorization|api_key|access_token|refresh_token|password|passwd|secret|"
    r"chat_id|telegram_id|user_id|provider_message_id|source_natural_id|destination_key|"
    r"dedupe_key|idempotency_key)"
    r"(?:[\"']?)\s*[:=]\s*"
    r"(?:[\"']?)([^\s,}\]\"']{1,512})"
)


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    member: str | None = None


def _is_safe_redaction(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    if normalized in _SAFE_REDACTIONS:
        return True
    return normalized.startswith("sha256:") or normalized.startswith("hash:")


def _text_findings(text: str, *, path: str, member: str | None) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    # Hex fingerprints are deliberate one-way redactions.  Running phone-like
    # patterns across their random digits creates false PII findings and does
    # not inspect any recoverable source value.
    pii_surface = re.sub(r"sha256:[0-9a-fA-F]{64}", "sha256:[redacted]", text)
    for kind, pattern in _PATTERNS:
        if pattern.search(pii_surface) and kind not in seen:
            findings.append(Finding(kind=kind, path=path, member=member))
            seen.add(kind)
    for match in _SENSITIVE_ASSIGNMENT.finditer(text):
        value = match.group(2)
        if _is_safe_redaction(value):
            continue
        kind = f"raw_sensitive_field:{match.group(1).lower()}"
        if kind not in seen:
            findings.append(Finding(kind=kind, path=path, member=member))
            seen.add(kind)
    return findings


def _limit_finding(kind: str, *, path: str, member: str | None = None) -> list[Finding]:
    return [Finding(kind=kind, path=path, member=member)]


def _scan_zip(data: bytes, *, path: str, member: str | None, depth: int) -> list[Finding]:
    findings: list[Finding] = []
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_MEMBERS:
                return _limit_finding("archive_member_limit_exceeded", path=path, member=member)
            for entry in entries:
                if entry.is_dir():
                    continue
                entry_name = f"{member}!{entry.filename}" if member else entry.filename
                if entry.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    findings += _limit_finding(
                        "archive_member_size_limit_exceeded", path=path, member=entry_name
                    )
                    continue
                total += entry.file_size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    findings += _limit_finding(
                        "archive_total_size_limit_exceeded", path=path, member=member
                    )
                    break
                try:
                    blob = archive.read(entry)
                except Exception:
                    findings += _limit_finding(
                        "archive_member_unreadable", path=path, member=entry_name
                    )
                    continue
                findings += _scan_blob(
                    blob, path=path, member=entry_name, depth=depth + 1
                )
    except Exception:
        return _limit_finding("archive_unreadable", path=path, member=member)
    return findings


def _scan_tar(data: bytes, *, path: str, member: str | None, depth: int) -> list[Finding]:
    findings: list[Finding] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            entries = archive.getmembers()
            if len(entries) > MAX_ARCHIVE_MEMBERS:
                return _limit_finding("archive_member_limit_exceeded", path=path, member=member)
            for entry in entries:
                if not entry.isfile():
                    continue
                entry_name = f"{member}!{entry.name}" if member else entry.name
                if entry.size > MAX_ARCHIVE_MEMBER_BYTES:
                    findings += _limit_finding(
                        "archive_member_size_limit_exceeded", path=path, member=entry_name
                    )
                    continue
                total += entry.size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    findings += _limit_finding(
                        "archive_total_size_limit_exceeded", path=path, member=member
                    )
                    break
                extracted = archive.extractfile(entry)
                if extracted is None:
                    findings += _limit_finding(
                        "archive_member_unreadable", path=path, member=entry_name
                    )
                    continue
                blob = extracted.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(blob) > MAX_ARCHIVE_MEMBER_BYTES:
                    findings += _limit_finding(
                        "archive_member_size_limit_exceeded", path=path, member=entry_name
                    )
                    continue
                findings += _scan_blob(
                    blob, path=path, member=entry_name, depth=depth + 1
                )
    except Exception:
        return _limit_finding("archive_unreadable", path=path, member=member)
    return findings


def _scan_gzip(data: bytes, *, path: str, member: str | None, depth: int) -> list[Finding]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as archive:
            blob = archive.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
    except Exception:
        return _limit_finding("archive_unreadable", path=path, member=member)
    if len(blob) > MAX_ARCHIVE_MEMBER_BYTES:
        return _limit_finding("archive_member_size_limit_exceeded", path=path, member=member)
    entry_name = f"{member}!gzip" if member else "gzip"
    return _scan_blob(blob, path=path, member=entry_name, depth=depth + 1)


def _scan_blob(data: bytes, *, path: str, member: str | None, depth: int = 0) -> list[Finding]:
    if depth > MAX_ARCHIVE_DEPTH:
        return _limit_finding("archive_depth_limit_exceeded", path=path, member=member)
    if data.startswith(b"PK\x03\x04"):
        return _scan_zip(data, path=path, member=member, depth=depth)
    if data.startswith(b"\x1f\x8b"):
        return _scan_gzip(data, path=path, member=member, depth=depth)
    if len(data) >= 262 and data[257:262] == b"ustar":
        return _scan_tar(data, path=path, member=member, depth=depth)
    return _text_findings(data.decode("latin-1", errors="ignore"), path=path, member=member)


def _files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_symlink():
            continue
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and not candidate.is_symlink()
            )


def scan_paths(paths: Iterable[Path]) -> dict[str, object]:
    roots = tuple(Path(path) for path in paths)
    findings: list[Finding] = []
    scanned_files: list[str] = []
    manifest_rows: list[str] = []
    missing = [str(path) for path in roots if not path.exists()]
    for missing_path in missing:
        findings.append(Finding(kind="input_missing", path=missing_path))
    for root in roots:
        if root.is_symlink():
            findings.append(Finding(kind="symlink_not_scanned", path=str(root)))
        elif root.is_dir():
            findings.extend(
                Finding(kind="symlink_not_scanned", path=str(candidate))
                for candidate in root.rglob("*")
                if candidate.is_symlink()
            )
    for path in _files(path for path in roots if path.exists()):
        containing_roots = [
            root
            for root in roots
            if root.is_dir() and (path == root or root in path.parents)
        ]
        if containing_roots:
            label = path.relative_to(max(containing_roots, key=lambda item: len(item.parts))).as_posix()
        else:
            label = path.name
        if label in scanned_files:
            findings.append(Finding(kind="scan_path_label_collision", path=label))
            continue
        scanned_files.append(label)
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                findings += _limit_finding("file_size_limit_exceeded", path=str(path))
                manifest_rows.append(f"{label}\toversized:{size}")
                continue
            blob = path.read_bytes()
            manifest_rows.append(f"{label}\t{hashlib.sha256(blob).hexdigest()}\t{len(blob)}")
            findings += _scan_blob(blob, path=str(path), member=None)
        except Exception:
            findings += _limit_finding("file_unreadable", path=str(path))
            manifest_rows.append(f"{label}\tunreadable")
    scanned_files.sort()
    manifest_rows.sort()
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "clean" if not findings else "blocked",
        "scanned_file_count": len(scanned_files),
        "scanned_files": scanned_files,
        "scanned_file_manifest_sha256": hashlib.sha256(
            ("\n".join(manifest_rows) + "\n").encode("utf-8")
        ).hexdigest(),
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }


def scan_tracked_source(repo_root: Path) -> dict[str, object]:
    """Scan the exact tracked Git tree for high-confidence credential shapes.

    Source code necessarily contains field names such as ``bot_token`` and
    ``user_id``; applying the evidence/PII assignment rules to source would be
    noisy and misleading.  This surface therefore uses only secret-value
    signatures while evidence and staging logs retain the stricter scanner.
    """
    root = Path(repo_root).resolve()
    findings: list[Finding] = []
    scanned_files: list[str] = []
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=root,
            text=True,
        ).strip()
        raw = subprocess.check_output(
            ["git", "ls-tree", "-r", "-z", "--full-tree", "HEAD"],
            cwd=root,
        )
    except (OSError, subprocess.CalledProcessError):
        return {
            "schema_version": 2,
            "surface": "tracked_source",
            "status": "blocked",
            "scanned_file_count": 0,
            "scanned_files": [],
            "blob_manifest_sha256": hashlib.sha256(b"").hexdigest(),
            "finding_count": 1,
            "findings": [
                asdict(Finding(kind="tracked_source_unavailable", path=str(root)))
            ],
        }
    manifest_rows: list[str] = []
    for encoded_row in raw.split(b"\0"):
        if not encoded_row:
            continue
        try:
            metadata, encoded_path = encoded_row.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split()
        except (ValueError, UnicodeDecodeError):
            findings.append(Finding(kind="tracked_tree_entry_invalid", path="."))
            continue
        relative = encoded_path.decode("utf-8", errors="surrogateescape")
        if object_type != "blob":
            continue
        manifest_rows.append(f"{mode} {object_id}\t{relative}")
        scanned_files.append(relative)
        try:
            size = int(
                subprocess.check_output(
                    ["git", "cat-file", "-s", object_id],
                    cwd=root,
                    text=True,
                ).strip()
            )
            if size > MAX_FILE_BYTES:
                findings.append(
                    Finding(kind="file_size_limit_exceeded", path=relative)
                )
                continue
            blob = subprocess.check_output(
                ["git", "cat-file", "blob", object_id],
                cwd=root,
            )
            text = blob.decode("latin-1", errors="ignore")
        except Exception:
            findings.append(Finding(kind="file_unreadable", path=relative))
            continue
        for kind, pattern in _PATTERNS:
            if kind in _SOURCE_SECRET_KINDS and pattern.search(text):
                findings.append(Finding(kind=kind, path=relative))
    return {
        "schema_version": 2,
        "surface": "tracked_source",
        "git_commit": commit,
        "git_tree": tree,
        "blob_manifest_sha256": hashlib.sha256(
            ("\n".join(manifest_rows) + "\n").encode("utf-8", errors="surrogateescape")
        ).hexdigest(),
        "status": "clean" if not findings else "blocked",
        "scanned_file_count": len(scanned_files),
        "scanned_files": sorted(scanned_files),
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }


def scan_release_surfaces(
    *,
    artifact_paths: Iterable[Path],
    tracked_source_root: Path | None,
) -> dict[str, object]:
    surfaces: list[dict[str, object]] = []
    artifact_roots = tuple(artifact_paths)
    if artifact_roots:
        artifact_report = scan_paths(artifact_roots)
        artifact_report = {**artifact_report, "surface": "artifacts_and_logs"}
        surfaces.append(artifact_report)
    if tracked_source_root is not None:
        surfaces.append(scan_tracked_source(tracked_source_root))
    if not surfaces:
        surfaces.append(
            {
                "schema_version": 2,
                "surface": "inputs",
                "status": "blocked",
                "scanned_file_count": 0,
                "scanned_files": [],
                "scanned_file_manifest_sha256": hashlib.sha256(b"").hexdigest(),
                "finding_count": 1,
                "findings": [
                    asdict(Finding(kind="scan_surface_missing", path="."))
                ],
            }
        )
    findings = [
        {**finding, "surface": surface["surface"]}
        for surface in surfaces
        for finding in surface["findings"]
    ]
    clean = all(surface["status"] == "clean" for surface in surfaces)
    artifact_files = sorted(
        {
            str(name)
            for surface in surfaces
            if surface["surface"] == "artifacts_and_logs"
            for name in surface.get("scanned_files", [])
        }
    )
    surface_manifest_rows = [
        "\t".join(
            (
                str(surface["surface"]),
                str(surface.get("scanned_file_manifest_sha256") or surface.get("blob_manifest_sha256") or ""),
                str(surface.get("scanned_file_count", surface.get("scanned_files", 0))),
            )
        )
        for surface in surfaces
    ]
    artifact_manifest_sha256 = next(
        (
            str(surface.get("scanned_file_manifest_sha256") or "")
            for surface in surfaces
            if surface["surface"] == "artifacts_and_logs"
        ),
        hashlib.sha256(b"").hexdigest(),
    )
    return {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "clean" if clean else "blocked",
        "scanned_file_count": len(artifact_files),
        "total_scanned_file_count": sum(
            int(surface.get("scanned_file_count", surface.get("scanned_files", 0)))
            for surface in surfaces
        ),
        "scanned_files": artifact_files,
        "scanned_file_manifest_sha256": artifact_manifest_sha256,
        "release_surface_manifest_sha256": hashlib.sha256(
            ("\n".join(sorted(surface_manifest_rows)) + "\n").encode("utf-8")
        ).hexdigest(),
        "finding_count": len(findings),
        "findings": findings,
        "surfaces": [
            {
                "surface": surface["surface"],
                "status": surface["status"],
                "scanned_file_count": surface.get(
                    "scanned_file_count", surface.get("scanned_files", 0)
                ),
                "scanned_file_manifest_sha256": surface.get(
                    "scanned_file_manifest_sha256",
                    surface.get("blob_manifest_sha256"),
                ),
                "finding_count": surface["finding_count"],
            }
            for surface in surfaces
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Telegram queue evidence files and archives for secrets and raw PII."
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--tracked-source-root", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = scan_release_surfaces(
        artifact_paths=args.paths,
        tracked_source_root=args.tracked_source_root,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())
