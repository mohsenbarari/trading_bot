#!/usr/bin/env python3
"""Provision and attach the disposable WA-IR Full-Matrix staging volume."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provision_arvan_witness_recovery_vps import (
    api_request,
    list_data,
    read_private_text,
    response_data,
    server_public_ipv4,
)


REGION = "ir-thr-fr1"
SERVER_ID = "4a377387-c052-4aba-8bda-0c756b9b0cfb"
SERVER_IP = "95.38.164.29"
VOLUME_NAME = "three-site-staging-wa-ir-20260722"
VOLUME_SIZE_GB = 50
TOKEN_FILE = Path("tmp/secrets/arvan-infra-apikey")


class VolumeError(RuntimeError):
    pass


def _volume_id(volume: dict[str, Any]) -> str:
    value = str(volume.get("id") or "")
    if not value:
        raise VolumeError("Arvan volume has no id")
    return value


def _server(token: str) -> dict[str, Any]:
    data = response_data(
        api_request("GET", f"/regions/{REGION}/servers/{SERVER_ID}", token),
        "WA-IR server",
    )
    if (
        not isinstance(data, dict)
        or data.get("id") != SERVER_ID
        or server_public_ipv4(data) != SERVER_IP
        or str(data.get("status", "")).upper() != "ACTIVE"
    ):
        raise VolumeError("pinned WA-IR server identity is unavailable")
    return data


def _find(token: str) -> dict[str, Any] | None:
    matches = [
        volume
        for volume in list_data(token, f"/regions/{REGION}/volumes", "WA-IR volumes")
        if volume.get("name") == VOLUME_NAME
    ]
    if len(matches) > 1:
        raise VolumeError("multiple WA-IR staging volumes share the pinned name")
    return matches[0] if matches else None


def _attached_to(volume: dict[str, Any], server_id: str) -> bool:
    attachments = volume.get("attachments")
    if not isinstance(attachments, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("server_id") or item.get("serverId") or "") == server_id
        for item in attachments
    )


def _wait(token: str, volume_id: str, *, attached: bool) -> dict[str, Any]:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        volumes = list_data(token, f"/regions/{REGION}/volumes", "WA-IR volumes")
        matches = [item for item in volumes if str(item.get("id") or "") == volume_id]
        if len(matches) == 1:
            volume = matches[0]
            status = str(volume.get("status") or "").lower()
            if status == "error":
                raise VolumeError("Arvan reported a staging volume error")
            if attached and _attached_to(volume, SERVER_ID):
                return volume
            if not attached and status in {"available", "active"}:
                return volume
        time.sleep(3)
    raise VolumeError("WA-IR staging volume did not reach its expected state")


def confirmation_phrase() -> str:
    return f"create-wa-ir-staging-volume:{REGION}:{SERVER_ID}:{VOLUME_SIZE_GB}"


def execute(*, token: str, apply: bool, confirm: str | None) -> dict[str, Any]:
    _server(token)
    existing = _find(token)
    if not apply:
        return {
            "status": "planned",
            "region": REGION,
            "server_id": SERVER_ID,
            "server_ip": SERVER_IP,
            "volume_name": VOLUME_NAME,
            "volume_size_gb": VOLUME_SIZE_GB,
            "existing": existing is not None,
            "billable_resource_created": False,
            "required_confirmation": confirmation_phrase(),
        }
    if confirm != confirmation_phrase():
        raise VolumeError("WA-IR staging volume confirmation mismatch")
    if existing is None:
        created = response_data(
            api_request(
                "POST",
                f"/regions/{REGION}/volumes",
                token,
                {
                    "name": VOLUME_NAME,
                    "description": "Disposable three-site Full-Matrix staging data",
                    "size": VOLUME_SIZE_GB,
                },
            ),
            "create WA-IR staging volume",
        )
        if not isinstance(created, dict):
            raise VolumeError("Arvan volume creation returned an invalid response")
        volume_id = _volume_id(created)
        volume = _wait(token, volume_id, attached=False)
    else:
        volume = existing
        volume_id = _volume_id(volume)
        if int(volume.get("size") or -1) != VOLUME_SIZE_GB:
            raise VolumeError("existing WA-IR staging volume size drifted")
    if not _attached_to(volume, SERVER_ID):
        api_request(
            "PATCH",
            f"/regions/{REGION}/volumes/attach",
            token,
            {"server_id": SERVER_ID, "volume_id": volume_id},
        )
        volume = _wait(token, volume_id, attached=True)
    return {
        "status": "attached",
        "region": REGION,
        "server_id": SERVER_ID,
        "server_ip": SERVER_IP,
        "volume_id": volume_id,
        "volume_name": VOLUME_NAME,
        "volume_size_gb": int(volume.get("size") or VOLUME_SIZE_GB),
        "attached": True,
        "delete_after_full_matrix": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = execute(
            token=read_private_text(args.token_file),
            apply=args.apply,
            confirm=args.confirm,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
