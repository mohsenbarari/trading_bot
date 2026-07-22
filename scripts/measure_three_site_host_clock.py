#!/usr/bin/env python3
"""Measure one staging host's UTC synchronization without trusting an operator value."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


SCHEMA = "three-site-staging-host-clock-v1"
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
}


class ClockMeasurementError(RuntimeError):
    pass


def _run(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=SAFE_ENV,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ClockMeasurementError(f"clock command failed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _chrony() -> tuple[float, str]:
    binary = shutil.which("chronyc", path=SAFE_ENV["PATH"])
    if binary is None:
        raise ClockMeasurementError("chronyc is unavailable")
    raw = _run([binary, "-c", "tracking"])
    fields = [item.strip() for item in raw.split(",")]
    if len(fields) < 13 or not fields[2]:
        raise ClockMeasurementError("chronyc tracking output is incomplete")
    try:
        # chronyc -c tracking emits seconds without unit suffixes.  Chrony's
        # documented absolute clock-error bound is:
        # |system time| + root dispersion + 0.5 * root delay.  Retain the
        # larger of that bound and the recent/RMS offsets.
        system_offset = abs(float(fields[3]))
        last_offset = abs(float(fields[4]))
        rms_offset = abs(float(fields[5]))
        root_delay = abs(float(fields[9]))
        root_dispersion = abs(float(fields[10]))
        offset_ms = max(
            system_offset + root_dispersion + (0.5 * root_delay),
            last_offset,
            rms_offset,
        ) * 1000.0
        stratum = int(fields[1])
    except (TypeError, ValueError) as exc:
        raise ClockMeasurementError("chronyc tracking offset is invalid") from exc
    reference_id = fields[0].upper().strip()
    leap_status = fields[12].lower().strip()
    if (
        not 1 <= stratum <= 15
        or not math.isfinite(offset_ms)
        or reference_id == "7F7F0101"
        or "not synchron" in leap_status
        or leap_status == "?"
    ):
        raise ClockMeasurementError("chronyc is not synchronized to a usable source")
    return offset_ms, raw


def _ntpq() -> tuple[float, str]:
    binary = shutil.which("ntpq", path=SAFE_ENV["PATH"])
    if binary is None:
        raise ClockMeasurementError("ntpq is unavailable")
    raw = _run([
        binary, "-c", "rv 0 offset,rootdisp,rootdelay,stratum,leap,refid"
    ])
    fields = dict(
        re.findall(
            r"(?:^|[,\s])(offset|rootdisp|rootdelay|stratum|leap|refid)=([^,\s]+)",
            raw,
        )
    )
    if set(fields) != {
        "offset", "rootdisp", "rootdelay", "stratum", "leap", "refid"
    }:
        raise ClockMeasurementError("ntpq system-variable output is incomplete")
    try:
        offset_ms = (
            abs(float(fields["offset"]))
            + abs(float(fields["rootdisp"]))
            + (0.5 * abs(float(fields["rootdelay"])))
        )
        stratum = int(fields["stratum"])
    except (TypeError, ValueError) as exc:
        raise ClockMeasurementError("ntpq offset is invalid") from exc
    if (
        not math.isfinite(offset_ms)
        or not 1 <= stratum <= 15
        or fields["leap"].lower() not in {"00", "0", "none"}
        or fields["refid"].upper().strip(".") in {"LOCL", "LOCAL", "7F7F0101"}
    ):
        raise ClockMeasurementError("ntpq is not synchronized to a usable source")
    return offset_ms, raw


def measure(*, site: str, release_sha: str) -> dict[str, Any]:
    synchronized = _run([
        "/usr/bin/timedatectl", "show", "--property=NTPSynchronized", "--value"
    ]).strip().lower()
    if synchronized != "yes":
        raise ClockMeasurementError("host reports NTPSynchronized=no")
    errors = []
    for source, probe in (
        ("chronyc_tracking_csv", _chrony),
        ("ntpq_system_variables", _ntpq),
    ):
        try:
            offset_ms, raw = probe()
            break
        except ClockMeasurementError as exc:
            errors.append(str(exc))
    else:
        raise ClockMeasurementError(
            "no offset-capable NTP client is available: " + "; ".join(errors)
        )
    observed = datetime.now(timezone.utc).isoformat()
    raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return {
        "schema": SCHEMA,
        "site": site,
        "release_sha": release_sha,
        "synchronized": True,
        "offset_ms": round(float(offset_ms), 6),
        "observed_at": observed,
        "measurement_source": source,
        "measurement_raw_sha256": raw_sha256,
        "measurement_raw": raw,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=("bot_fi", "webapp_fi", "webapp_ir"), required=True)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args(argv)
    if len(args.release_sha) != 40 or any(c not in "0123456789abcdef" for c in args.release_sha):
        raise SystemExit("release SHA must be lowercase 40-hex")
    try:
        print(json.dumps(measure(site=args.site, release_sha=args.release_sha), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
