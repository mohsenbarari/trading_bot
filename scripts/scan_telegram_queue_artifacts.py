#!/usr/bin/env python3
"""Fail-closed secret and PII scanner for Telegram queue evidence artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import re
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
    for kind, pattern in _PATTERNS:
        if pattern.search(text) and kind not in seen:
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
    scanned_files = 0
    missing = [str(path) for path in roots if not path.exists()]
    for missing_path in missing:
        findings.append(Finding(kind="input_missing", path=missing_path))
    for path in _files(path for path in roots if path.exists()):
        scanned_files += 1
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                findings += _limit_finding("file_size_limit_exceeded", path=str(path))
                continue
            findings += _scan_blob(path.read_bytes(), path=str(path), member=None)
        except Exception:
            findings += _limit_finding("file_unreadable", path=str(path))
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "clean" if not findings else "blocked",
        "scanned_files": scanned_files,
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Telegram queue evidence files and archives for secrets and raw PII."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = scan_paths(args.paths)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "clean" else 2


if __name__ == "__main__":
    raise SystemExit(main())
