#!/usr/bin/env python3
"""Run a disposable, redacted live-HTTP gate for Stage 7 external capture."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "deploy/market-data/Dockerfile"


class Stage7ExternalGateError(RuntimeError):
    pass


def command(
    arguments: Sequence[str], *, label: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise Stage7ExternalGateError(f"{label}_failed_rc_{result.returncode}")
    return result


def release_identity() -> tuple[str, int]:
    if command(["git", "status", "--porcelain=v1"], label="git_status").stdout.strip():
        raise Stage7ExternalGateError("git_worktree_must_be_clean")
    sha = command(["git", "rev-parse", "HEAD"], label="git_sha").stdout.strip()
    epoch = command(
        ["git", "show", "-s", "--format=%ct", "HEAD"], label="git_epoch"
    ).stdout.strip()
    if len(sha) != 40 or not epoch.isdigit():
        raise Stage7ExternalGateError("git_release_identity_invalid")
    return sha, int(epoch)


def run_gate() -> dict[str, Any]:
    sha, epoch = release_identity()
    suffix = secrets.token_hex(5)
    image = f"market-stage7-external:{sha[:12]}-{suffix}"
    root: Path | None = None
    cleanup = {"image_removed": False, "root_removed": False}
    try:
        command(
            [
                "docker",
                "build",
                "--no-cache",
                "--file",
                str(DOCKERFILE),
                "--tag",
                image,
                "--build-arg",
                f"SOURCE_SHA={sha}",
                "--build-arg",
                "IMAGE_VERSION=stage7-external-live-http-gate",
                "--build-arg",
                f"SOURCE_DATE_EPOCH={epoch}",
                ".",
            ],
            label="external_gate_image_build",
        )
        root = Path(tempfile.mkdtemp(prefix="market-stage7-external-"))
        state = root / "state"
        spool = root / "spool"
        for path in (root, state, spool):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o700)
        command(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--tmpfs",
                "/tmp:size=16m,mode=1777,noexec,nosuid,nodev",
                "--user",
                "10001:10001",
                "-e",
                "MARKET_PIPELINE_MODE=fixture",
                "-e",
                f"MARKET_PIPELINE_RELEASE_SHA={sha}",
                "-e",
                "MARKET_PIPELINE_STATE_ROOT=/var/lib/market-data/state",
                "-e",
                "MARKET_EXTERNAL_CAPTURE_ROOT=/var/lib/market-data/capture/external",
                "-e",
                "MARKET_EXTERNAL_CAPTURE_FIXTURE_POLL=true",
                "-e",
                "MARKET_EXTERNAL_CAPTURE_ONESHOT=true",
                "-v",
                f"{state}:/var/lib/market-data/state",
                "-v",
                f"{spool}:/var/lib/market-data/capture/external",
                image,
                "service",
                "--role",
                "market-capture-external",
            ],
            label="external_gate_container",
        )
        health = json.loads(
            (state / "market-capture-external/health.json").read_text(encoding="utf-8")
        )
        expected_sources = {"WALLEX_PUBLIC_API", "BINANCE_PAXG_PUBLIC_API"}
        if (
            health.get("schema") != "external_quote_capture/1.0"
            or set(health.get("sources") or {}) != expected_sources
            or int(health.get("outbox_depth", -1)) != 0
        ):
            raise Stage7ExternalGateError("external_health_contract_failed")
        for source in expected_sources:
            counters = health["sources"][source]
            if int(counters.get("success", 0)) != 1 or int(counters.get("failure", 0)):
                raise Stage7ExternalGateError("external_live_poll_failed")
        records: list[dict[str, Any]] = []
        for path in sorted(spool.glob("events-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                records.append(json.loads(line))
        if len(records) != 4:
            raise Stage7ExternalGateError("external_spool_record_count_failed")
        dimensions = {
            (
                str(item["source"]["source_id"]),
                str(item["quote"]["instrument"]),
                str(item["quote"]["quote_kind"]),
            )
            for item in records
        }
        if dimensions != {
            ("WALLEX_PUBLIC_API", "USDT_IRT", "BID"),
            ("WALLEX_PUBLIC_API", "USDT_IRT", "ASK"),
            ("WALLEX_PUBLIC_API", "USDT_IRT", "MID"),
            ("BINANCE_PAXG_PUBLIC_API", "PAXG_USD_PROXY", "MID"),
        }:
            raise Stage7ExternalGateError("external_spool_dimensions_failed")
        encoded = json.dumps(records, sort_keys=True).lower()
        if any(token in encoded for token in ("https://", "api-key", "authorization")):
            raise Stage7ExternalGateError("external_spool_private_transport_leak")
        return {
            "status": "pass",
            "release_sha": sha,
            "sources": {
                source: {
                    "successful_polls": int(health["sources"][source]["success"]),
                    "records": sum(
                        item["source"]["source_id"] == source for item in records
                    ),
                }
                for source in sorted(expected_sources)
            },
            "raw_response_stored": False,
            "credential_or_url_stored": False,
            "cleanup": cleanup,
        }
    finally:
        removed = command(
            ["docker", "image", "rm", "--force", image],
            label="cleanup_image",
            check=False,
        )
        cleanup["image_removed"] = removed.returncode == 0
        if root is not None and root.exists():
            shutil.rmtree(root)
        cleanup["root_removed"] = not (root is not None and root.exists())


def main() -> int:
    try:
        result = run_gate()
    except Stage7ExternalGateError as exc:
        print(json.dumps({"status": "fail", "reason_code": str(exc)}, sort_keys=True))
        return 1
    if not all(result["cleanup"].values()):
        print(
            json.dumps(
                {"status": "fail", "reason_code": "stage7_cleanup_incomplete"},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
