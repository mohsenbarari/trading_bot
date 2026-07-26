#!/usr/bin/env python3
"""Provision and attach one immutable-name staging data volume per matrix host.

This tool is idempotent and deliberately exposes no detach or delete action.
It refuses pre-existing volumes whose size or attachment identity differs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provision_arvan_full_matrix_destructive_hosts import (
    ROLE_ORDER,
    STATE_FILE,
    TOKEN_FILE,
    DestructiveProvisionError,
    _atomic_state,
    _safe_existing_state,
)
from scripts.provision_arvan_witness_recovery_vps import (
    ApiPermissionError,
    ProvisionError,
    api_request,
    list_data,
    read_private_text,
    response_data,
)


VOLUME_SIZE_GB = 50
VOLUME_PREFIX = "three-site-matrix-destructive-20260726"
VOLUME_DESCRIPTION = "Disposable production-disjoint Full Matrix staging data"


class VolumeProvisionError(DestructiveProvisionError):
    """Disposable matrix volume provisioning failed closed."""


def volume_name(role: str) -> str:
    if role not in ROLE_ORDER:
        raise VolumeProvisionError("invalid destructive volume role")
    return f"{VOLUME_PREFIX}-{role.replace('_', '-')}-data"


def _state() -> dict[str, Any]:
    value = _safe_existing_state()
    if value is None or value.get("status") != "active":
        raise VolumeProvisionError("destructive host state is not active")
    hosts = value.get("hosts")
    if not isinstance(hosts, dict) or set(hosts) != set(ROLE_ORDER):
        raise VolumeProvisionError("destructive host role set is invalid")
    return value


def _volumes(token: str, region: str) -> list[dict[str, Any]]:
    last_error: ProvisionError | None = None
    for delay in (0, 2, 4, 8, 16):
        if delay:
            time.sleep(delay)
        try:
            return list_data(
                token,
                f"/regions/{region}/volumes",
                f"{region} volumes",
            )
        except ApiPermissionError:
            raise
        except ProvisionError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _find(token: str, region: str, role: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in _volumes(token, region)
        if item.get("name") == volume_name(role)
    ]
    if len(matches) > 1:
        raise VolumeProvisionError(f"duplicate destructive volume exists: {role}")
    return matches[0] if matches else None


def _attachments(volume: dict[str, Any]) -> list[dict[str, Any]]:
    value = volume.get("attachments")
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise VolumeProvisionError("volume attachment response is invalid")
    return value


def _verify(
    volume: dict[str, Any],
    *,
    role: str,
    server_id: str,
    allow_unattached: bool,
) -> None:
    identifier = volume.get("id")
    size = volume.get("size")
    attachments = _attachments(volume)
    if (
        not isinstance(identifier, str)
        or not identifier
        or volume.get("name") != volume_name(role)
        or type(size) is not int
        or size != VOLUME_SIZE_GB
        or str(volume.get("bootable", "")).lower() not in {"false", "0"}
    ):
        raise VolumeProvisionError(f"destructive volume identity differs: {role}")
    attached_ids = {
        str(item.get("server_id") or "")
        for item in attachments
        if item.get("server_id")
    }
    if attached_ids and attached_ids != {server_id}:
        raise VolumeProvisionError(
            f"destructive volume is attached to a different server: {role}"
        )
    if not allow_unattached and attached_ids != {server_id}:
        raise VolumeProvisionError(
            f"destructive volume attachment is incomplete: {role}"
        )


def _limits(token: str, region: str, *, needed_count: int) -> dict[str, int]:
    last_error: ProvisionError | None = None
    value: Any = None
    for delay in (0, 2, 4, 8, 16):
        if delay:
            time.sleep(delay)
        try:
            value = response_data(
                api_request("GET", f"/regions/{region}/volumes/limits", token),
                f"{region} volume limits",
            )
            last_error = None
            break
        except ApiPermissionError:
            raise
        except ProvisionError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    if not isinstance(value, dict):
        raise VolumeProvisionError(f"{region} volume limits are invalid")
    fields = (
        "max_total_volume_gigabytes",
        "max_total_volumes",
        "total_gigabytes_used",
        "total_volumes_used",
    )
    if any(type(value.get(field)) is not int for field in fields):
        raise VolumeProvisionError(f"{region} volume quota fields are invalid")
    if (
        value["total_gigabytes_used"] + needed_count * VOLUME_SIZE_GB
        > value["max_total_volume_gigabytes"]
        or value["total_volumes_used"] + needed_count
        > value["max_total_volumes"]
    ):
        raise VolumeProvisionError(f"{region} volume quota cannot fit campaign")
    return {field: value[field] for field in fields}


def preflight(token: str, state: dict[str, Any]) -> dict[str, Any]:
    existing: dict[str, dict[str, Any] | None] = {}
    for role in ROLE_ORDER:
        host = state["hosts"][role]
        region = str(host.get("region") or "")
        server_id = str(host.get("server_id") or "")
        if not region or not server_id:
            raise VolumeProvisionError(f"destructive host identity is incomplete: {role}")
        volume = _find(token, region, role)
        if volume is not None:
            _verify(
                volume,
                role=role,
                server_id=server_id,
                allow_unattached=True,
            )
        existing[role] = volume
    quotas = {}
    for region in sorted(
        {str(state["hosts"][role]["region"]) for role in ROLE_ORDER}
    ):
        needed = sum(
            state["hosts"][role]["region"] == region and existing[role] is None
            for role in ROLE_ORDER
        )
        quotas[region] = _limits(token, region, needed_count=needed)
    return {"existing": existing, "quotas": quotas}


def _create(token: str, region: str, role: str) -> dict[str, Any]:
    value = response_data(
        api_request(
            "POST",
            f"/regions/{region}/volumes",
            token,
            {
                "name": volume_name(role),
                "description": VOLUME_DESCRIPTION,
                "size": VOLUME_SIZE_GB,
            },
            timeout=90,
        ),
        f"create {role} volume",
    )
    if not isinstance(value, dict):
        raise VolumeProvisionError(f"create volume response is invalid: {role}")
    return value


def _wait(
    token: str,
    region: str,
    role: str,
    *,
    server_id: str,
    attached: bool,
) -> dict[str, Any]:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        volume = _find(token, region, role)
        if volume is not None:
            status = str(volume.get("status") or "").lower()
            if status == "error":
                raise VolumeProvisionError(f"provider volume error: {role}")
            try:
                _verify(
                    volume,
                    role=role,
                    server_id=server_id,
                    allow_unattached=not attached,
                )
            except VolumeProvisionError:
                if attached:
                    time.sleep(5)
                    continue
                raise
            if (not attached and status in {"available", "in-use"}) or (
                attached and status == "in-use"
            ):
                return volume
        time.sleep(5)
    target = "attachment" if attached else "creation"
    raise VolumeProvisionError(f"volume {target} did not complete: {role}")


def apply(token: str, state: dict[str, Any], checked: dict[str, Any]) -> dict[str, Any]:
    volumes: dict[str, dict[str, Any]] = {}
    for role in ROLE_ORDER:
        host = state["hosts"][role]
        region = str(host["region"])
        server_id = str(host["server_id"])
        volume = checked["existing"][role]
        if volume is None:
            try:
                _create(token, region, role)
            except ProvisionError:
                if _find(token, region, role) is None:
                    raise
            volume = _wait(
                token,
                region,
                role,
                server_id=server_id,
                attached=False,
            )
        _verify(
            volume,
            role=role,
            server_id=server_id,
            allow_unattached=True,
        )
        attachments = _attachments(volume)
        if not attachments:
            api_request(
                "PATCH",
                f"/regions/{region}/volumes/attach",
                token,
                {
                    "server_id": server_id,
                    "volume_id": str(volume["id"]),
                },
                timeout=90,
            )
        volume = _wait(
            token,
            region,
            role,
            server_id=server_id,
            attached=True,
        )
        attachment = _attachments(volume)[0]
        volumes[role] = {
            "role": role,
            "region": region,
            "name": volume_name(role),
            "volume_id": str(volume["id"]),
            "server_id": server_id,
            "size_gb": VOLUME_SIZE_GB,
            "status": str(volume.get("status") or ""),
            "device": str(attachment.get("device") or ""),
            "attached_at": str(attachment.get("attached_at") or ""),
        }
        state["volumes"] = volumes
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_state(state)
    state["volume_status"] = "attached"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_state(state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    args = parser.parse_args(argv)
    token = read_private_text(args.token_file)
    state = _state()
    checked = preflight(token, state)
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "apply": False,
                    "volume_size_gb": VOLUME_SIZE_GB,
                    "roles": {
                        role: {
                            "region": state["hosts"][role]["region"],
                            "name": volume_name(role),
                            "existing": checked["existing"][role] is not None,
                        }
                        for role in ROLE_ORDER
                    },
                    "delete_operation_available": False,
                    "detach_operation_available": False,
                },
                sort_keys=True,
            )
        )
        return 0
    result = apply(token, state, checked)
    print(
        json.dumps(
            {
                "status": result["volume_status"],
                "apply": True,
                "roles": {
                    role: {
                        key: value
                        for key, value in item.items()
                        if key
                        in {
                            "region",
                            "name",
                            "size_gb",
                            "status",
                            "device",
                        }
                    }
                    for role, item in result["volumes"].items()
                },
                "delete_operation_available": False,
                "detach_operation_available": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionError as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
