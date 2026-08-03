#!/usr/bin/env python3
"""Open only WebApp-FI's reviewed public staging ports in Arvan ECC."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.ensure_three_site_stage4_test_data_plane import (
    GROUPS,
    SERVERS,
    Stage4DataPlaneError,
    _matching_rule,
    _read_groups,
    _read_server,
    _rule_payload,
)
from scripts.provision_arvan_witness_recovery_vps import api_request, read_private_text


TOKEN_FILE = Path("/root/secure-envs/trading-bot/arvan-cdn-token")
CAMPAIGN_ID = "fd34231d-f52e-498a-aab4-438c99d88fc5"
ROLE = "webapp_fi"
PUBLIC_RULES = (
    ("stage4-webapp-fi-public-http", "0.0.0.0/0", 80),
    ("stage4-webapp-fi-public-https", "0.0.0.0/0", 443),
)


class Stage4PublicIngressError(Stage4DataPlaneError):
    pass


def _commitment() -> str:
    return hashlib.sha256(json.dumps(PUBLIC_RULES, separators=(",", ":")).encode()).hexdigest()


def confirmation_phrase() -> str:
    return f"add-stage4-public-ingress:{CAMPAIGN_ID}:{_commitment()[:16]}"


def execute(token: str, *, apply: bool, confirm: str | None) -> dict[str, Any]:
    _read_server(token, ROLE)
    group = _read_groups(token)["eu-west1-a"]
    missing = [
        (description, source, port)
        for description, source, port in PUBLIC_RULES
        if _matching_rule(group, description=description, source=source, port=port) is None
    ]
    if not apply:
        return {
            "status": "already_present" if not missing else "planned",
            "apply": False,
            "campaign_id": CAMPAIGN_ID,
            "rule_commitment_sha256": _commitment(),
            "rule_count": len(PUBLIC_RULES),
            "missing_rule_count": len(missing),
            "required_confirmation": confirmation_phrase(),
            "server_or_volume_lifecycle_operation": False,
            "production_overlap": False,
        }
    if confirm != confirmation_phrase():
        raise Stage4PublicIngressError("Stage 4 public-ingress confirmation mismatch")
    for description, source, port in missing:
        api_request(
            "POST",
            f"/regions/eu-west1-a/securities/security-rules/{GROUPS['eu-west1-a']['id']}",
            token,
            _rule_payload(description, source, port),
        )
    group = _read_groups(token)["eu-west1-a"]
    absent = [
        description
        for description, source, port in PUBLIC_RULES
        if _matching_rule(group, description=description, source=source, port=port) is None
    ]
    if absent:
        raise Stage4PublicIngressError(f"public ingress rules did not persist: {sorted(absent)}")
    return {
        "status": "present",
        "apply": True,
        "campaign_id": CAMPAIGN_ID,
        "rule_commitment_sha256": _commitment(),
        "rule_count": len(PUBLIC_RULES),
        "added_rule_count": len(missing),
        "server_or_volume_lifecycle_operation": False,
        "production_overlap": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        result = execute(read_private_text(args.token_file), apply=args.apply, confirm=args.confirm)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
