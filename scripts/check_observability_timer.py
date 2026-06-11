#!/usr/bin/env python3
"""Check whether an observability timer or its cron fallback is installed."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


CRON_DIRS = (Path("/etc/cron.d"), Path("/var/spool/cron"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check one observability timer and optional cron fallback.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--timer-name", required=True)
    parser.add_argument("--fallback-pattern", required=True)
    parser.add_argument("--optional", action="store_true")
    return parser.parse_args()


def run_systemctl(*args: str) -> str:
    completed = subprocess.run(
        ["systemctl", *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return (completed.stdout or completed.stderr or "").strip()


def find_cron_matches(pattern: str) -> list[str]:
    matches: list[str] = []
    for root in CRON_DIRS:
        if not root.exists():
            continue
        try:
            candidates = root.rglob("*") if root.is_dir() else [root]
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if pattern in text:
                    matches.append(str(path))
        except OSError:
            continue
    return sorted(set(matches))


def build_status(*, label: str, timer_name: str, fallback_pattern: str, optional: bool) -> dict[str, Any]:
    systemd_available = shutil.which("systemctl") is not None
    systemd: dict[str, str] = {}
    if systemd_available:
        systemd = {
            "enabled": run_systemctl("is-enabled", timer_name),
            "active": run_systemctl("is-active", timer_name),
        }
    cron_matches = find_cron_matches(fallback_pattern)
    installed = (
        bool(systemd)
        and systemd.get("enabled") == "enabled"
        and systemd.get("active") == "active"
    ) or bool(cron_matches)
    status = "ok" if installed else "not_required" if optional else "failed"
    return {
        "label": label,
        "timer_name": timer_name,
        "fallback_pattern": fallback_pattern,
        "required": not optional,
        "systemd_available": systemd_available,
        "systemd": systemd,
        "cron_matches": cron_matches,
        "status": status,
    }


def main() -> int:
    args = parse_args()
    payload = build_status(
        label=args.label,
        timer_name=args.timer_name,
        fallback_pattern=args.fallback_pattern,
        optional=args.optional,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] in {"ok", "not_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
