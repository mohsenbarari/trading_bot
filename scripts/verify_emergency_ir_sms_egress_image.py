#!/usr/bin/env python3
"""Fail closed on the immutable fixed-upstream Emergency SMS relay image."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any, Callable


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SMS_EGRESS_SCOPE = "ir-standalone-sms-egress"
SMS_EGRESS_CONTRACT = "fixed-api.sms.ir-v1-send-verify"
FORBIDDEN_IMAGE_ENV = frozenset(
    {
        "SMSIR_API_KEY",
        "BOT_TOKEN",
        "SYNC_API_KEY",
        "PEER_SERVER_URL",
        "IRAN_SERVER_URL",
        "GERMANY_SERVER_URL",
        "FOREIGN_SERVER_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    }
)


class EmergencySmsEgressImageError(RuntimeError):
    pass


def expected_image_tag(patch_sha: str) -> str:
    if not SHA_RE.fullmatch(patch_sha):
        raise EmergencySmsEgressImageError("Emergency patch SHA is invalid")
    return f"trading_bot_emergency_ir_sms_egress:{patch_sha}"


def verify_payload(*, payload: Any, source_release_sha: str, emergency_patch_sha: str) -> list[str]:
    failures: list[str] = []
    if not SHA_RE.fullmatch(source_release_sha):
        return ["source release SHA is invalid"]
    if not SHA_RE.fullmatch(emergency_patch_sha):
        return ["Emergency patch SHA is invalid"]
    if not isinstance(payload, dict):
        return ["Docker image inspection is malformed"]
    config = payload.get("Config")
    if not isinstance(config, dict):
        return ["Docker image Config is missing"]
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return ["Docker image labels are missing"]
    expected_labels = {
        "org.opencontainers.image.revision": emergency_patch_sha,
        "org.goldtrade.emergency.base-revision": source_release_sha,
        "org.goldtrade.emergency.scope": SMS_EGRESS_SCOPE,
        "org.goldtrade.emergency.egress": SMS_EGRESS_CONTRACT,
    }
    for key, expected in expected_labels.items():
        if labels.get(key) != expected:
            failures.append(f"image label {key} differs from the Emergency SMS relay contract")
    expected_tag = expected_image_tag(emergency_patch_sha)
    tags = payload.get("RepoTags")
    if not isinstance(tags, list) or expected_tag not in tags:
        failures.append("image does not retain the exact Emergency SMS relay tag")
    if any(not isinstance(tag, str) or "three_site" in tag.lower() or "staging" in tag.lower() for tag in tags or []):
        failures.append("image carries a forbidden staging/three-site tag")
    env = config.get("Env")
    if not isinstance(env, list) or any(not isinstance(item, str) for item in env):
        failures.append("image environment metadata is malformed")
    else:
        forbidden = sorted(
            item.partition("=")[0] for item in env if item.partition("=")[0] in FORBIDDEN_IMAGE_ENV
        )
        if forbidden:
            failures.append("image embeds forbidden credential or proxy keys: " + ",".join(forbidden))
    return failures


def inspect_and_verify(
    *,
    image: str,
    source_release_sha: str,
    emergency_patch_sha: str,
    runner: Callable[..., Any] = subprocess.run,
) -> list[str]:
    expected = expected_image_tag(emergency_patch_sha)
    if image != expected:
        return ["requested image tag does not match the Emergency patch SHA"]
    try:
        result = runner(
            ["docker", "image", "inspect", image, "--format", "{{json .}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Docker image inspection failed: {type(exc).__name__}"]
    if getattr(result, "returncode", 1) != 0:
        return ["Docker image inspection failed"]
    try:
        payload = json.loads(str(getattr(result, "stdout", "")).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return ["Docker image inspection is not JSON"]
    return verify_payload(
        payload=payload,
        source_release_sha=source_release_sha,
        emergency_patch_sha=emergency_patch_sha,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source-release-sha", required=True)
    parser.add_argument("--emergency-patch-sha", required=True)
    args = parser.parse_args()
    failures = inspect_and_verify(
        image=args.image,
        source_release_sha=args.source_release_sha,
        emergency_patch_sha=args.emergency_patch_sha,
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("Emergency IR SMS relay image provenance verification passed")
    return 0
