#!/usr/bin/env python3
"""Idempotently allow the pinned Stage 3 test SSH jump path in Arvan ECC."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provision_arvan_witness_recovery_vps import (  # noqa: E402
    api_request,
    list_data,
    read_private_text,
    response_data,
    server_public_ipv4,
)


TOKEN_FILE = REPO_ROOT / "tmp/secrets/arvan-infra-apikey"
TARGET = {
    "region": "ir-thr-fr1",
    "server_id": "1dca4b24-6aba-4d11-b430-c8c7dcce2b8a",
    "name": "three-site-matrix-destructive-20260726-webapp-ir",
    "public_ip": "188.213.198.115",
}
JUMPS = {
    "test_bot_fi": {
        "region": "eu-west1-a",
        "server_id": "b42750eb-1efb-4595-87b6-4f61606422f1",
        "name": "three-site-matrix-destructive-20260726-bot-fi",
        "public_ip": "130.185.121.98",
    },
    "iran_relay": {
        "region": "ir-thr-fr1",
        "server_id": "eb00fba1-437e-4852-9c29-154e5ecaef85",
        "name": "ubuntu - 1 vCPU - 1 GB RAM",
        "public_ip": "185.231.182.6",
    },
}
SECURITY_GROUP_ID = "50dd5d86-7e82-489f-b3b9-de3024d96367"
SECURITY_GROUP_NAME = "three-site-full-matrix-destructive-20260726"
PRODUCTION_IPS = frozenset(
    {"65.109.216.187", "65.109.220.59", "95.38.164.29", "37.152.191.11"}
)


class Stage3SshPathError(RuntimeError):
    pass


def rule_description(jump_name: str) -> str:
    return f"stage3-{jump_name.replace('_', '-')}-ssh-jump"


def confirmation_phrase(jump_name: str) -> str:
    jump = JUMPS[jump_name]
    return (
        "add-stage3-test-ssh-jump:"
        f"{TARGET['server_id']}:{jump['public_ip']}/32"
    )


def read_pinned_server(token: str, expected: dict[str, str]) -> dict[str, Any]:
    server = response_data(
        api_request(
            "GET",
            f"/regions/{expected['region']}/servers/{expected['server_id']}",
            token,
        ),
        f"pinned Stage 3 server {expected['name']}",
    )
    if (
        not isinstance(server, dict)
        or server.get("id") != expected["server_id"]
        or server.get("name") != expected["name"]
        or server_public_ipv4(server) != expected["public_ip"]
        or str(server.get("status", "")).upper() != "ACTIVE"
        or expected["public_ip"] in PRODUCTION_IPS
    ):
        raise Stage3SshPathError("pinned non-production server identity differs")
    return server


def read_group(token: str, target: dict[str, Any]) -> dict[str, Any]:
    references = target.get("security_groups")
    if not isinstance(references, list) or not any(
        isinstance(item, dict) and item.get("id") == SECURITY_GROUP_ID
        for item in references
    ):
        raise Stage3SshPathError("target is not attached to the pinned security group")
    matches = [
        group
        for group in list_data(
            token,
            f"/regions/{TARGET['region']}/securities",
            "Stage 3 test security groups",
        )
        if group.get("id") == SECURITY_GROUP_ID
        and group.get("real_name") == SECURITY_GROUP_NAME
    ]
    if len(matches) != 1:
        raise Stage3SshPathError("pinned Stage 3 security group differs")
    return matches[0]


def matching_rule(
    group: dict[str, Any],
    jump_name: str,
    jump: dict[str, str],
) -> dict[str, Any] | None:
    description = rule_description(jump_name)
    matches = [
        rule
        for rule in group.get("rules", [])
        if isinstance(rule, dict) and rule.get("description") == description
    ]
    if len(matches) > 1:
        raise Stage3SshPathError("duplicate Stage 3 SSH jump rules exist")
    if not matches:
        return None
    rule = matches[0]
    if (
        rule.get("direction") != "ingress"
        or rule.get("protocol") != "tcp"
        or int(rule.get("port_start") or -1) != 22
        or int(rule.get("port_end") or -1) != 22
        or rule.get("ip") != f"{jump['public_ip']}/32"
        or rule.get("ether_type") != "IPv4"
    ):
        raise Stage3SshPathError("existing Stage 3 SSH jump rule drifted")
    return rule


def execute(
    token: str,
    *,
    jump_name: str,
    apply: bool,
    confirm: str | None,
) -> dict[str, Any]:
    jump = JUMPS[jump_name]
    target = read_pinned_server(token, TARGET)
    read_pinned_server(token, jump)
    group = read_group(token, target)
    existing = matching_rule(group, jump_name, jump)
    if not apply:
        return {
            "status": "already_present" if existing else "planned",
            "apply": False,
            "target": TARGET["public_ip"],
            "jump": jump_name,
            "jump_source": f"{jump['public_ip']}/32",
            "port": 22,
            "existing": existing is not None,
            "required_confirmation": confirmation_phrase(jump_name),
            "server_or_volume_lifecycle_operation": False,
            "production_overlap": False,
        }
    if confirm != confirmation_phrase(jump_name):
        raise Stage3SshPathError("Stage 3 SSH jump confirmation mismatch")
    if existing is None:
        api_request(
            "POST",
            (
                f"/regions/{TARGET['region']}/securities/"
                f"security-rules/{SECURITY_GROUP_ID}"
            ),
            token,
            {
                "description": rule_description(jump_name),
                "direction": "ingress",
                "protocol": "tcp",
                "port_from": "22",
                "port_to": "22",
                "ips": [f"{jump['public_ip']}/32"],
            },
        )
        group = read_group(token, read_pinned_server(token, TARGET))
        existing = matching_rule(group, jump_name, jump)
    if existing is None:
        raise Stage3SshPathError("Stage 3 SSH jump rule did not persist")
    return {
        "status": "present",
        "apply": True,
        "target": TARGET["public_ip"],
        "jump": jump_name,
        "jump_source": f"{jump['public_ip']}/32",
        "port": 22,
        "server_or_volume_lifecycle_operation": False,
        "production_overlap": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--jump", choices=sorted(JUMPS), default="test_bot_fi")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = execute(
            read_private_text(args.token_file),
            jump_name=args.jump,
            apply=args.apply,
            confirm=args.confirm,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
