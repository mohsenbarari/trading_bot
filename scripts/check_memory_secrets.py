#!/usr/bin/env python3
"""Reject credential-like or personal data from staged project memory."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import PurePosixPath


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer credential", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("credential-bearing URL", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@")),
    (
        "non-placeholder credential assignment",
        re.compile(
            r"(?ix)\b(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
            r"client[_-]?secret|database_url|redis_url|dsn)\b\s*[:=]\s*"
            r"(?!<[^>]+>|\$\{[^}]+\}|(?:redacted|placeholder|example|changeme|none|null)\b)"
            r"[^\s`'\"]{6,}"
        ),
    ),
    ("email address", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("Iranian mobile number", re.compile(r"(?<!\d)(?:\+?98|0)9\d{9}(?!\d)")),
    ("machine-specific home path", re.compile(r"(?:^|[\s`'\"])/(?:root|home|Users)/[^\s`'\"]+")),
    (
        "private IPv4 address",
        re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"),
    ),
)

SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), stderr=subprocess.DEVNULL)


def staged_paths() -> list[str]:
    raw = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def is_unsafe_env_path(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith(".env") and name not in SAFE_ENV_NAMES


def scan_text(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((line_number, label))
    return findings


def scan_staged() -> int:
    findings: list[tuple[str, int | None, str]] = []
    for path in staged_paths():
        if is_unsafe_env_path(path):
            findings.append((path, None, "local/environment file must not be committed"))
        if not path.startswith("docs/memory/"):
            continue
        try:
            text = git("show", f":{path}").decode("utf-8")
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            findings.append((path, None, "memory file is not readable UTF-8 text"))
            continue
        findings.extend((path, line_number, label) for line_number, label in scan_text(text))

    if not findings:
        print("Memory secret guard: OK")
        return 0

    print("Memory secret guard blocked the commit; matched values are intentionally hidden:", file=sys.stderr)
    for path, line_number, label in findings:
        location = f"{path}:{line_number}" if line_number is not None else path
        print(f"- {location}: {label}", file=sys.stderr)
    return 1


def self_test() -> int:
    safe = "Use account_status for runtime access gating."
    unsafe = "api_key=sk-proj-abcdefghijklmnopqrstuvwxyz012345"
    if scan_text(safe) or not scan_text(unsafe):
        print("Memory secret guard self-test: FAILED", file=sys.stderr)
        return 1
    print("Memory secret guard self-test: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    return self_test() if args.self_test else scan_staged()


if __name__ == "__main__":
    raise SystemExit(main())
