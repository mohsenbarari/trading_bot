#!/usr/bin/env python3
"""Provision four disposable, production-disjoint Full Matrix hosts.

The tool is idempotent by exact role/name/region/plan/image identity.  It has
no delete operation.  Bootstrap passwords are retained only in the owner-only
state document and are never printed.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provision_arvan_witness_recovery_vps import (  # noqa: E402
    ApiPermissionError,
    ProvisionError,
    api_request,
    default_security_group,
    list_data,
    read_private_text,
    response_data,
    server_public_ipv4,
    validate_public_key,
)


API_BASE = "https://napi.arvancloud.ir/ecc/v1"
TOKEN_FILE = Path("/root/secure-envs/trading-bot/arvan-cdn-token")
PUBLIC_KEY_FILE = Path("/root/.ssh/id_ed25519.pub")
PRIVATE_KEY_FILE = Path("/root/.ssh/id_ed25519")
STATE_FILE = Path(
    "/root/secure-envs/arvan/full-matrix-destructive-20260726.json"
)
KNOWN_HOSTS_FILE = Path(
    "/root/secure-envs/arvan/full-matrix-destructive-20260726.known_hosts"
)
CONTROL_IP = "65.109.216.187/32"
SECURITY_GROUP_NAME = "three-site-full-matrix-destructive-20260726"
CAMPAIGN_PREFIX = "three-site-matrix-destructive-20260726"
UBUNTU_NAME = "24.04"

ROLE_SPECS: dict[str, dict[str, str]] = {
    "bot_fi": {
        "region": "eu-west1-a",
        "name": f"{CAMPAIGN_PREFIX}-bot-fi",
        "plan_id": "g1-8-4-0",
        "image_id": "00aaa9d1-3e0a-468c-aaf4-334513981e42",
    },
    "webapp_fi": {
        "region": "eu-west1-a",
        "name": f"{CAMPAIGN_PREFIX}-webapp-fi",
        "plan_id": "g1-8-4-0",
        "image_id": "00aaa9d1-3e0a-468c-aaf4-334513981e42",
    },
    "webapp_ir": {
        "region": "ir-thr-fr1",
        "name": f"{CAMPAIGN_PREFIX}-webapp-ir",
        "plan_id": "g3-8-4-0",
        "image_id": "80827085-61a9-45dd-a9b1-04356e8b3987",
    },
    "witness": {
        "region": "eu-west1-a",
        "name": f"{CAMPAIGN_PREFIX}-witness",
        "plan_id": "eco-2-2-0",
        "image_id": "00aaa9d1-3e0a-468c-aaf4-334513981e42",
    },
}
ROLE_ORDER = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{2,80}\Z")
SERVER_ID = re.compile(r"[0-9a-f-]{36}\Z")


class DestructiveProvisionError(ProvisionError):
    """Disposable-host provisioning failed closed."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DestructiveProvisionError("state JSON contains duplicate fields")
        value[key] = item
    return value


def _safe_existing_state(path: Path = STATE_FILE) -> dict[str, Any] | None:
    if not path.is_absolute() or path.is_symlink():
        raise DestructiveProvisionError("destructive host state path is unsafe")
    if not path.exists():
        return None
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or not 2 <= metadata.st_size <= 1024 * 1024
    ):
        raise DestructiveProvisionError("destructive host state file is unsafe")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DestructiveProvisionError("destructive host state JSON is invalid") from exc
    if not isinstance(value, dict):
        raise DestructiveProvisionError("destructive host state is not an object")
    return value


def _atomic_state(value: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_FILE.parent.is_symlink() or STATE_FILE.is_symlink():
        raise DestructiveProvisionError("destructive state path is unsafe")
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = STATE_FILE.with_name(
        f".{STATE_FILE.name}.{os.getpid()}.{hashlib.sha256(raw).hexdigest()[:12]}.tmp"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise DestructiveProvisionError("short destructive state write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, STATE_FILE)
    os.chmod(STATE_FILE, 0o600)
    directory = os.open(
        STATE_FILE.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
    )
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _ubuntu_image(token: str, region: str, image_id: str) -> dict[str, Any]:
    groups = list_data(
        token,
        f"/regions/{region}/images?type=distributions",
        f"{region} distribution images",
    )
    matches = [
        image
        for group in groups
        if str(group.get("name", "")).lower() == "ubuntu"
        for image in group.get("images", [])
        if isinstance(image, dict)
        and image.get("id") == image_id
        and image.get("name") == UBUNTU_NAME
    ]
    if len(matches) != 1:
        raise DestructiveProvisionError(
            f"{region} exact Ubuntu {UBUNTU_NAME} image is unavailable"
        )
    return matches[0]


def _plan(token: str, region: str, plan_id: str) -> dict[str, Any]:
    plans = list_data(token, f"/regions/{region}/sizes", f"{region} plans")
    matches = [plan for plan in plans if plan.get("id") == plan_id]
    if len(matches) != 1:
        raise DestructiveProvisionError(f"{region} exact plan is unavailable: {plan_id}")
    plan = matches[0]
    if (
        type(plan.get("cpu_count")) is not int
        or type(plan.get("memory")) is not int
        or type(plan.get("disk")) is not int
        or plan["cpu_count"] < 2
        or plan["memory"] < 2
        or plan["disk"] < 25
    ):
        raise DestructiveProvisionError(f"{region} plan resources are unsafe")
    return plan


def _region_preflight(token: str, region: str, *, needed: int) -> dict[str, Any]:
    regions = list_data(token, "/regions", "regions")
    matches = [
        item
        for item in regions
        if item.get("code") == region
        and item.get("visible") is True
        and item.get("create") is True
    ]
    if len(matches) != 1:
        raise DestructiveProvisionError(f"region is unavailable: {region}")
    options = response_data(
        api_request("GET", f"/regions/{region}/servers/options", token),
        f"{region} server options",
    )
    if not isinstance(options, dict):
        raise DestructiveProvisionError(f"{region} server options are invalid")
    count = options.get("droplet_count")
    limit = options.get("droplet_limit")
    network_id = options.get("network_id")
    if (
        type(count) is not int
        or type(limit) is not int
        or count + needed > limit
        or not isinstance(network_id, str)
        or not network_id
    ):
        raise DestructiveProvisionError(f"{region} quota/network cannot fit campaign")
    return {
        "network_id": network_id,
        "instance_count": count,
        "instance_limit": limit,
    }


def _list_servers(token: str, region: str) -> list[dict[str, Any]]:
    return list_data(token, f"/regions/{region}/servers", f"{region} servers")


def _find_server(token: str, role: str) -> dict[str, Any] | None:
    spec = ROLE_SPECS[role]
    matches = [
        item
        for item in _list_servers(token, spec["region"])
        if item.get("name") == spec["name"]
    ]
    if len(matches) > 1:
        raise DestructiveProvisionError(f"duplicate destructive server exists: {role}")
    return matches[0] if matches else None


def _verify_server(role: str, server: dict[str, Any]) -> None:
    spec = ROLE_SPECS[role]
    flavor = server.get("flavor")
    image = server.get("image")
    if (
        server.get("name") != spec["name"]
        or not isinstance(flavor, dict)
        or flavor.get("id") != spec["plan_id"]
        or not isinstance(image, dict)
        or image.get("id") != spec["image_id"]
    ):
        raise DestructiveProvisionError(f"existing destructive server differs: {role}")


def _security_group(token: str, region: str, *, apply: bool) -> tuple[dict[str, Any] | None, str]:
    groups = list_data(token, f"/regions/{region}/securities", f"{region} securities")
    matches = [
        item
        for item in groups
        if item.get("real_name") == SECURITY_GROUP_NAME
    ]
    if len(matches) > 1:
        raise DestructiveProvisionError(f"{region} has duplicate campaign security groups")
    group = matches[0] if matches else None
    if group is None and apply:
        try:
            api_request(
                "POST",
                f"/regions/{region}/securities",
                token,
                {
                    "name": SECURITY_GROUP_NAME,
                    "description": "Disposable production-disjoint Full Matrix hosts",
                },
            )
            groups = list_data(
                token,
                f"/regions/{region}/securities",
                f"{region} securities",
            )
            group = next(
                (
                    item
                    for item in groups
                    if item.get("real_name") == SECURITY_GROUP_NAME
                ),
                None,
            )
        except ApiPermissionError:
            group = None
    if group is None:
        if not apply:
            return None, "pending-or-default"
        return default_security_group(token), "arDefault-plus-host-firewall"
    group_id = group.get("id")
    if not isinstance(group_id, str) or not group_id:
        raise DestructiveProvisionError(f"{region} campaign security group has no id")
    existing = {
        rule.get("description")
        for rule in group.get("rules", [])
        if isinstance(rule, dict)
    }
    required = (
        {
            "description": "matrix-control-ssh",
            "direction": "ingress",
            "protocol": "tcp",
            "port_from": "22",
            "port_to": "22",
            "ips": [CONTROL_IP],
        },
        {
            "description": "matrix-egress-tcp",
            "direction": "egress",
            "protocol": "tcp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
        {
            "description": "matrix-egress-udp",
            "direction": "egress",
            "protocol": "udp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
        {
            "description": "matrix-egress-icmp",
            "direction": "egress",
            "protocol": "icmp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
    )
    if apply:
        for rule in required:
            if rule["description"] not in existing:
                api_request(
                    "POST",
                    f"/regions/{region}/securities/security-rules/{group_id}",
                    token,
                    rule,
                )
    return group, "dedicated"


def _init_script(public_key: str, role: str) -> str:
    if role not in ROLE_SPECS:
        raise DestructiveProvisionError("invalid destructive role")
    encoded_key = base64.b64encode((public_key + "\n").encode()).decode("ascii")
    encoded_role = base64.b64encode((role + "\n").encode()).decode("ascii")
    return f"""#!/bin/bash
set -Eeuo pipefail
umask 077
install -d -m 0700 /root/.ssh /home/ubuntu/.ssh
printf '%s' '{encoded_key}' | base64 -d >/root/.ssh/authorized_keys
install -m 0600 /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-full-matrix-destructive.conf <<'EOF'
PubkeyAuthentication yes
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
EOF
sshd -t
systemctl reload ssh || systemctl reload sshd
printf '%s' '{encoded_role}' | base64 -d >/etc/full-matrix-destructive-role
chmod 0644 /etc/full-matrix-destructive-role
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || iptables -A INPUT -i lo -j ACCEPT
iptables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -C INPUT -p tcp -s {CONTROL_IP} --dport 22 -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp -s {CONTROL_IP} --dport 22 -j ACCEPT
iptables -P INPUT DROP
if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -C INPUT -i lo -j ACCEPT 2>/dev/null || ip6tables -A INPUT -i lo -j ACCEPT
  ip6tables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || ip6tables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  ip6tables -P INPUT DROP
fi
install -d -m 0700 /var/lib/trading-bot-full-matrix
touch /var/lib/trading-bot-full-matrix/bootstrap-complete
"""


def _validate_init_script(script: str) -> None:
    result = subprocess.run(
        ["/bin/bash", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise DestructiveProvisionError("destructive init script syntax is invalid")


def _create(
    token: str,
    role: str,
    *,
    public_key: str,
    region: dict[str, Any],
    security_group: dict[str, Any],
) -> tuple[str, str]:
    spec = ROLE_SPECS[role]
    group_id = security_group.get("id")
    if not isinstance(group_id, str) or not group_id:
        raise DestructiveProvisionError("cannot create without security group")
    script = _init_script(public_key, role)
    _validate_init_script(script)
    created = response_data(
        api_request(
            "POST",
            f"/regions/{spec['region']}/servers",
            token,
            {
                "name": spec["name"],
                "network_ids": [region["network_id"]],
                "flavor_id": spec["plan_id"],
                "image_id": spec["image_id"],
                "security_groups": [{"name": group_id}],
                "ssh_key": False,
                "key_name": 0,
                "count": 1,
                "create_type": "image",
                "disk_size": _plan(
                    token, spec["region"], spec["plan_id"]
                )["disk"],
                "init_script": script,
                "ha_enabled": False,
            },
            timeout=90,
        ),
        f"create {role}",
    )
    if not isinstance(created, dict) or not isinstance(created.get("id"), str):
        raise DestructiveProvisionError(f"{role} creation returned no id")
    return created["id"], str(created.get("password") or "")


def _wait(token: str, role: str, server_id: str) -> dict[str, Any]:
    spec = ROLE_SPECS[role]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        server = response_data(
            api_request(
                "GET",
                f"/regions/{spec['region']}/servers/{server_id}",
                token,
            ),
            f"read {role}",
        )
        if not isinstance(server, dict):
            raise DestructiveProvisionError(f"{role} server response is invalid")
        if str(server.get("status", "")).lower() == "error":
            raise DestructiveProvisionError(f"{role} entered provider error state")
        if (
            str(server.get("status", "")).lower() == "active"
            and server_public_ipv4(server)
        ):
            _verify_server(role, server)
            return server
        time.sleep(5)
    raise DestructiveProvisionError(f"{role} did not become active")


def _safe_public_ip(server: dict[str, Any], role: str) -> str:
    value = server_public_ipv4(server)
    if value is None:
        raise DestructiveProvisionError(f"{role} has no public IPv4")
    parsed = ipaddress.ip_address(value)
    if parsed.version != 4 or parsed.is_private or parsed.is_loopback:
        raise DestructiveProvisionError(f"{role} public IPv4 is invalid")
    return value


def preflight(token: str) -> dict[str, Any]:
    public_key = validate_public_key(PUBLIC_KEY_FILE)
    if not PRIVATE_KEY_FILE.is_file():
        raise DestructiveProvisionError("matching private SSH key is missing")
    existing = {
        role: _find_server(token, role)
        for role in ROLE_ORDER
    }
    regions = {}
    for region in sorted({spec["region"] for spec in ROLE_SPECS.values()}):
        needed = sum(
            spec["region"] == region and existing[role] is None
            for role, spec in ROLE_SPECS.items()
        )
        regions[region] = _region_preflight(token, region, needed=needed)
    plans = {
        role: _plan(token, spec["region"], spec["plan_id"])
        for role, spec in ROLE_SPECS.items()
    }
    for role, spec in ROLE_SPECS.items():
        if NAME.fullmatch(spec["name"]) is None:
            raise DestructiveProvisionError(f"{role} server name is invalid")
        _ubuntu_image(token, spec["region"], spec["image_id"])
        _validate_init_script(_init_script(public_key, role))
    return {
        "public_key": public_key,
        "regions": regions,
        "plans": plans,
        "existing": existing,
    }


def apply(token: str, checked: dict[str, Any]) -> dict[str, Any]:
    security: dict[str, tuple[dict[str, Any], str]] = {}
    for region in checked["regions"]:
        group, mode = _security_group(token, region, apply=True)
        if group is None:
            raise DestructiveProvisionError(f"{region} security group is unavailable")
        security[region] = (group, mode)
    prior = _safe_existing_state()
    prior_hosts = (
        prior.get("hosts", {})
        if isinstance(prior, dict) and isinstance(prior.get("hosts"), dict)
        else {}
    )
    hosts: dict[str, dict[str, Any]] = {}
    for role in ROLE_ORDER:
        spec = ROLE_SPECS[role]
        server = checked["existing"][role]
        password = str((prior_hosts.get(role) or {}).get("bootstrap_password") or "")
        if server is None:
            try:
                server_id, password = _create(
                    token,
                    role,
                    public_key=checked["public_key"],
                    region=checked["regions"][spec["region"]],
                    security_group=security[spec["region"]][0],
                )
            except ProvisionError:
                recovered = _find_server(token, role)
                if recovered is None:
                    raise
                server_id = str(recovered.get("id") or "")
        else:
            _verify_server(role, server)
            server_id = str(server.get("id") or "")
        if not server_id:
            raise DestructiveProvisionError(f"{role} server id is missing")
        server = _wait(token, role, server_id)
        plan = checked["plans"][role]
        hosts[role] = {
            "role": role,
            "region": spec["region"],
            "name": spec["name"],
            "server_id": server_id,
            "public_ip": _safe_public_ip(server, role),
            "plan_id": spec["plan_id"],
            "image_id": spec["image_id"],
            "cpu_count": plan["cpu_count"],
            "memory_gb": plan["memory"],
            "disk_gb": plan["disk"],
            "hourly_irr": plan.get("price_per_hour"),
            "monthly_irr": plan.get("price_per_month"),
            "security_group_mode": security[spec["region"]][1],
            "bootstrap_password": password,
            "created_at": server.get("created"),
        }
        _atomic_state(
            {
                "schema": "three-site-full-matrix-destructive-hosts-v1",
                "status": "provisioning",
                "production_overlap": False,
                "created_at": (
                    prior.get("created_at")
                    if isinstance(prior, dict)
                    else datetime.now(timezone.utc).isoformat()
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "hosts": hosts,
            }
        )
    result = {
        "schema": "three-site-full-matrix-destructive-hosts-v1",
        "status": "active",
        "production_overlap": False,
        "created_at": (
            prior.get("created_at")
            if isinstance(prior, dict)
            else datetime.now(timezone.utc).isoformat()
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hosts": hosts,
    }
    _atomic_state(result)
    return result


def public_summary(state: dict[str, Any], *, apply_mode: bool) -> dict[str, Any]:
    hosts = state.get("hosts", {})
    return {
        "status": state.get("status"),
        "apply": apply_mode,
        "billable_resource_created": apply_mode,
        "production_overlap": False,
        "delete_operation_available": False,
        "state_file": str(STATE_FILE),
        "state_file_mode": "0600" if apply_mode else None,
        "roles": {
            role: {
                key: value
                for key, value in item.items()
                if key
                in {
                    "region",
                    "name",
                    "public_ip",
                    "plan_id",
                    "cpu_count",
                    "memory_gb",
                    "disk_gb",
                    "hourly_irr",
                    "security_group_mode",
                }
            }
            for role, item in hosts.items()
            if isinstance(item, dict)
        },
    }


def inspect_existing_hosts(token: str) -> dict[str, Any]:
    """Read back only safe, auditable provider facts for the four drill hosts.

    The owner-only state document supplies immutable provider ids but they are
    never printed.  This intentionally performs no lifecycle action and is
    also useful before wiring a newly documented provider fault primitive.
    """

    state = _safe_existing_state()
    if state is None or state.get("status") != "active":
        raise DestructiveProvisionError("destructive host state is unavailable")
    hosts = state.get("hosts")
    if not isinstance(hosts, dict) or set(hosts) != set(ROLE_ORDER):
        raise DestructiveProvisionError("destructive host state roles differ")
    inspected: dict[str, dict[str, Any]] = {}
    for role in ROLE_ORDER:
        stored = hosts.get(role)
        spec = ROLE_SPECS[role]
        if not isinstance(stored, dict):
            raise DestructiveProvisionError(f"destructive host state is malformed: {role}")
        region = str(stored.get("region") or "")
        server_id = str(stored.get("server_id") or "")
        if region != spec["region"] or SERVER_ID.fullmatch(server_id) is None:
            raise DestructiveProvisionError(f"destructive host identity is invalid: {role}")
        server = response_data(
            api_request("GET", f"/regions/{region}/servers/{server_id}", token),
            f"inspect {role}",
        )
        if not isinstance(server, dict):
            raise DestructiveProvisionError(f"provider host response is invalid: {role}")
        _verify_server(role, server)
        observed_ip = _safe_public_ip(server, role)
        if observed_ip != str(stored.get("public_ip") or ""):
            raise DestructiveProvisionError(f"provider host address differs: {role}")
        available = response_data(
            api_request(
                "GET",
                f"/regions/{region}/servers/{server_id}/actions",
                token,
            ),
            f"inspect available actions {role}",
        )
        action_names: list[str] = []
        if isinstance(available, list):
            for item in available:
                if not isinstance(item, dict):
                    raise DestructiveProvisionError("provider action entry is invalid")
                action = item.get("action")
                if not isinstance(action, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:-]{1,64}", action):
                    raise DestructiveProvisionError("provider action identity is invalid")
                action_names.append(action)
        elif isinstance(available, dict):
            action = available.get("action")
            if not isinstance(action, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:-]{1,64}", action):
                raise DestructiveProvisionError("provider action identity is invalid")
            action_names.append(action)
        else:
            raise DestructiveProvisionError("provider available-actions response is invalid")
        # Only non-sensitive schema keys are emitted.  Values (including
        # console links or bootstrap material if the provider ever adds them)
        # are deliberately neither retained nor printed.
        sensitive_key_fragments = ("credential", "password", "secret", "token")
        inspected[role] = {
            "region": region,
            "status": str(server.get("status") or ""),
            "public_ip": observed_ip,
            "server_response_keys": sorted(
                str(key)
                for key in server
                if not any(fragment in str(key).lower() for fragment in sensitive_key_fragments)
            ),
            "available_actions": sorted(set(action_names)),
            "power_fields_present": sorted(
                key
                for key in ("actions", "allowed_actions", "power_state", "task_state")
                if key in server
            ),
        }
    return {
        "schema": "three-site-full-matrix-destructive-host-inspection-v1",
        "status": "passed",
        "read_only": True,
        "delete_operation_available": False,
        "roles": inspected,
    }


def inspect_api_schema(token: str) -> dict[str, Any]:
    """Discover only published ECC operation metadata through safe GETs.

    An unreviewed power endpoint must never be guessed.  The result contains
    path and HTTP-method names only, never schema examples, server ids, or
    credentials.  Missing public schemas are reported as such rather than
    turning into an action probe against a host.
    """

    candidates = ("/openapi.json", "/swagger.json")
    unavailable: list[str] = []
    for candidate in candidates:
        try:
            response = api_request("GET", candidate, token)
        except ProvisionError:
            unavailable.append(candidate)
            continue
        document = response_data(response, f"ECC schema {candidate}") if "data" in response else response
        if not isinstance(document, dict):
            raise DestructiveProvisionError("published ECC schema is not an object")
        paths = document.get("paths")
        if not isinstance(paths, dict):
            raise DestructiveProvisionError("published ECC schema has no paths object")
        server_paths: dict[str, list[str]] = {}
        for path, methods in paths.items():
            if not isinstance(path, str) or "/servers" not in path:
                continue
            if not isinstance(methods, dict):
                continue
            allowed = sorted(
                str(method).upper()
                for method in methods
                if str(method).lower() in {"delete", "get", "patch", "post", "put"}
            )
            if allowed:
                server_paths[path] = allowed
        return {
            "schema": "three-site-full-matrix-destructive-ecc-schema-inspection-v1",
            "status": "published_schema_found",
            "read_only": True,
            "document_path": candidate,
            "server_paths": server_paths,
        }
    return {
        "schema": "three-site-full-matrix-destructive-ecc-schema-inspection-v1",
        "status": "published_schema_not_available",
        "read_only": True,
        "checked_paths": unavailable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--inspect-api-schema", action="store_true")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    args = parser.parse_args(argv)
    if sum(bool(value) for value in (args.apply, args.inspect, args.inspect_api_schema)) > 1:
        raise DestructiveProvisionError("--apply and inspection modes cannot be combined")
    token = read_private_text(args.token_file)
    if args.inspect:
        print(json.dumps(inspect_existing_hosts(token), sort_keys=True))
        return 0
    if args.inspect_api_schema:
        print(json.dumps(inspect_api_schema(token), sort_keys=True))
        return 0
    checked = preflight(token)
    if not args.apply:
        plans = checked["plans"]
        dry = {
            "schema": "three-site-full-matrix-destructive-hosts-v1",
            "status": "dry_run",
            "hosts": {
                role: {
                    "region": ROLE_SPECS[role]["region"],
                    "name": ROLE_SPECS[role]["name"],
                    "plan_id": ROLE_SPECS[role]["plan_id"],
                    "cpu_count": plans[role]["cpu_count"],
                    "memory_gb": plans[role]["memory"],
                    "disk_gb": plans[role]["disk"],
                    "hourly_irr": plans[role].get("price_per_hour"),
                    "existing": checked["existing"][role] is not None,
                }
                for role in ROLE_ORDER
            },
        }
        print(json.dumps(public_summary(dry, apply_mode=False), sort_keys=True))
        return 0
    state = apply(token, checked)
    print(json.dumps(public_summary(state, apply_mode=True), sort_keys=True))
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
