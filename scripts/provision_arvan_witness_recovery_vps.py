#!/usr/bin/env python3
"""Provision the approved temporary Arvan Witness recovery-drill VPS safely."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
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
import urllib.error
import urllib.request


API_BASE = "https://napi.arvancloud.ir/ecc/v1"
TOKEN_FILE = Path("tmp/secrets/arvan-cdn-token")
PUBLIC_KEY_FILE = Path("/root/.ssh/id_ed25519.pub")
STATE_FILE = Path("/root/secure-envs/arvan/writer-witness-recovery-vps.env")
REGION = "ir-thr-fr1"
SERVER_NAME = "writer-witness-recovery-drill-20260715"
SECURITY_GROUP_NAME = "writer-witness-recovery-drill"
PLAN_ID = "eco-2-2-0"
IMAGE_ID = "80827085-61a9-45dd-a9b1-04356e8b3987"
DISK_SIZE_GB = 30
CONTROL_IP = "65.109.216.187/32"
WEBAPP_FI_IP = "65.109.220.59/32"
WEBAPP_IR_IP = "95.38.164.29/32"


class ProvisionError(RuntimeError):
    pass


class ApiPermissionError(ProvisionError):
    pass


def read_private_text(path: Path) -> str:
    if not path.is_file():
        raise ProvisionError(f"required private file is missing: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ProvisionError(f"private file permissions are unsafe: {path} mode={mode:o}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ProvisionError(f"required private file is empty: {path}")
    return value


def api_request(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    authorization = token if token.lower().startswith("apikey ") else f"Apikey {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "trading-bot-arvan-witness-recovery/1",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        error_type = ApiPermissionError if exc.code == 403 else ProvisionError
        raise error_type(
            f"Arvan API {method} {path} failed: http={exc.code} detail={detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProvisionError(f"Arvan API is unreachable: {exc.reason}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProvisionError("Arvan API returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise ProvisionError("Arvan API returned an unexpected response shape")
    return decoded


def response_data(response: dict[str, Any], operation: str) -> Any:
    if "data" not in response:
        raise ProvisionError(f"Arvan API response has no data for {operation}")
    return response["data"]


def list_data(token: str, path: str, operation: str) -> list[dict[str, Any]]:
    data = response_data(api_request("GET", path, token), operation)
    if not isinstance(data, list):
        raise ProvisionError(f"Arvan API returned a non-list for {operation}")
    return [item for item in data if isinstance(item, dict)]


def find_one(items: list[dict[str, Any]], key: str, value: str, operation: str) -> dict[str, Any]:
    matches = [item for item in items if item.get(key) == value]
    if len(matches) != 1:
        raise ProvisionError(f"expected exactly one {operation}; found {len(matches)}")
    return matches[0]


def validate_public_key(path: Path) -> str:
    if not path.is_file():
        raise ProvisionError(f"SSH public key is missing: {path}")
    value = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?", value):
        raise ProvisionError("SSH public key is not a single Ed25519 key")
    check = subprocess.run(
        ["ssh-keygen", "-lf", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise ProvisionError("SSH public key failed ssh-keygen validation")
    return value


def init_script(public_key: str) -> str:
    encoded = base64.b64encode((public_key + "\n").encode()).decode("ascii")
    return f"""#!/bin/bash
set -Eeuo pipefail
key_file=/tmp/writer-witness-recovery-operator.pub
printf '%s' '{encoded}' | base64 -d >"$key_file"
for target in /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys; do
    home_dir="${{target%/.ssh/authorized_keys}}"
    install -d -m 0700 "$home_dir/.ssh"
    install -m 0600 "$key_file" "$target"
done
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
rm -f "$key_file"
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-writer-witness-recovery.conf <<'EOF'
PubkeyAuthentication yes
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
EOF
sshd -t
systemctl reload ssh || systemctl reload sshd

# Apply an immediate host-level deny policy before enabling the persistent UFW
# policy. Key delivery and sshd validation deliberately happen first so a
# missing optional firewall module cannot strand an otherwise healthy host.
iptables -C INPUT -i lo -j ACCEPT 2>/dev/null || iptables -A INPUT -i lo -j ACCEPT
iptables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -C INPUT -p tcp -s {CONTROL_IP} --dport 22 -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp -s {CONTROL_IP} --dport 22 -j ACCEPT
iptables -C INPUT -p tcp -s {WEBAPP_FI_IP} --dport 443 -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp -s {WEBAPP_FI_IP} --dport 443 -j ACCEPT
iptables -C INPUT -p tcp -s {WEBAPP_IR_IP} --dport 443 -j ACCEPT 2>/dev/null || iptables -A INPUT -p tcp -s {WEBAPP_IR_IP} --dport 443 -j ACCEPT
iptables -P INPUT DROP
if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -C INPUT -i lo -j ACCEPT 2>/dev/null || ip6tables -A INPUT -i lo -j ACCEPT
    ip6tables -C INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || ip6tables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    ip6tables -P INPUT DROP
fi
if command -v ufw >/dev/null 2>&1; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow from {CONTROL_IP} to any port 22 proto tcp comment 'recovery-control-ssh'
    ufw allow from {WEBAPP_FI_IP} to any port 443 proto tcp comment 'recovery-webapp-fi'
    ufw allow from {WEBAPP_IR_IP} to any port 443 proto tcp comment 'recovery-webapp-ir'
    ufw --force enable
fi
touch /var/lib/writer-witness-recovery-bootstrap-complete
"""


def validate_init_script(public_key: str) -> None:
    check = subprocess.run(
        ["bash", "-n"],
        input=init_script(public_key),
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise ProvisionError("generated init script failed bash syntax validation")


def expected_rules() -> list[dict[str, Any]]:
    return [
        {
            "description": "recovery-ssh-control",
            "direction": "ingress",
            "protocol": "tcp",
            "port_from": "22",
            "port_to": "22",
            "ips": [CONTROL_IP],
        },
        {
            "description": "recovery-witness-https-peers",
            "direction": "ingress",
            "protocol": "tcp",
            "port_from": "443",
            "port_to": "443",
            "ips": [WEBAPP_FI_IP, WEBAPP_IR_IP],
        },
        {
            "description": "recovery-egress-tcp",
            "direction": "egress",
            "protocol": "tcp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
        {
            "description": "recovery-egress-udp",
            "direction": "egress",
            "protocol": "udp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
        {
            "description": "recovery-egress-icmp",
            "direction": "egress",
            "protocol": "icmp",
            "port_from": "",
            "port_to": "",
            "ips": ["any"],
        },
    ]


def find_security_group(token: str) -> dict[str, Any] | None:
    groups = list_data(
        token,
        f"/regions/{REGION}/securities",
        "security groups",
    )
    matches = [group for group in groups if group.get("real_name") == SECURITY_GROUP_NAME]
    if len(matches) > 1:
        raise ProvisionError("multiple recovery security groups exist")
    return matches[0] if matches else None


def ensure_security_group(token: str, *, apply: bool) -> dict[str, Any] | None:
    group = find_security_group(token)
    if group is None:
        if not apply:
            return None
        created = response_data(
            api_request(
                "POST",
                f"/regions/{REGION}/securities",
                token,
                {
                    "name": SECURITY_GROUP_NAME,
                    "description": "Temporary isolated Writer Witness recovery drill",
                },
            ),
            "create security group",
        )
        if not isinstance(created, dict) or not created.get("id"):
            raise ProvisionError("security group creation returned no id")
        group = find_security_group(token)
    assert group is not None
    group_id = group.get("id")
    if not isinstance(group_id, str) or not group_id:
        raise ProvisionError("recovery security group has no id")
    existing_descriptions = {
        rule.get("description")
        for rule in group.get("rules", [])
        if isinstance(rule, dict)
    }
    for rule in expected_rules():
        if rule["description"] in existing_descriptions:
            continue
        if not apply:
            continue
        api_request(
            "POST",
            f"/regions/{REGION}/securities/security-rules/{group_id}",
            token,
            rule,
        )
    if apply:
        group = find_security_group(token)
        assert group is not None
        actual_descriptions = {
            rule.get("description")
            for rule in group.get("rules", [])
            if isinstance(rule, dict)
        }
        missing = {
            rule["description"] for rule in expected_rules()
        } - actual_descriptions
        if missing:
            raise ProvisionError(f"security group rules did not persist: {sorted(missing)}")
    return group


def default_security_group(token: str) -> dict[str, Any]:
    groups = list_data(
        token,
        f"/regions/{REGION}/securities",
        "security groups",
    )
    return find_one(groups, "real_name", "arDefault", "default security group")


def server_public_ipv4(server: dict[str, Any]) -> str | None:
    addresses = server.get("addresses")
    if not isinstance(addresses, dict):
        return None
    candidates: list[str] = []
    for values in addresses.values():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            raw = item.get("addr")
            if not isinstance(raw, str):
                continue
            try:
                parsed = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if parsed.version == 4 and not parsed.is_private:
                candidates.append(raw)
    return sorted(set(candidates))[0] if candidates else None


def validate_preflight(token: str) -> dict[str, Any]:
    regions = list_data(token, "/regions", "regions")
    region = find_one(regions, "code", REGION, "approved region")
    if region.get("create") is not True or region.get("visible") is not True:
        raise ProvisionError("approved region is not open for server creation")
    plans = list_data(token, f"/regions/{REGION}/sizes", "plans")
    plan = find_one(plans, "id", PLAN_ID, "approved plan")
    if (
        plan.get("cpu_count") != 2
        or plan.get("memory") != 2
        or plan.get("disk") != DISK_SIZE_GB
    ):
        raise ProvisionError("approved plan resources changed")
    image_groups = list_data(
        token,
        f"/regions/{REGION}/images?type=distributions",
        "distribution images",
    )
    images = [
        image
        for group in image_groups
        if str(group.get("name", "")).lower() == "ubuntu"
        for image in group.get("images", [])
        if isinstance(image, dict)
    ]
    image = find_one(images, "id", IMAGE_ID, "Ubuntu 24.04 image")
    if image.get("name") != "24.04":
        raise ProvisionError("approved Ubuntu image changed")
    options = response_data(
        api_request("GET", f"/regions/{REGION}/servers/options", token),
        "server options",
    )
    if not isinstance(options, dict):
        raise ProvisionError("server options response is invalid")
    count = options.get("droplet_count")
    limit = options.get("droplet_limit")
    if not isinstance(count, int) or not isinstance(limit, int) or count >= limit:
        raise ProvisionError("Arvan instance quota is exhausted or invalid")
    network_id = options.get("network_id")
    if not isinstance(network_id, str) or not network_id:
        raise ProvisionError("Arvan default network is missing")
    return {
        "plan": plan,
        "image": image,
        "network_id": network_id,
        "instance_count": count,
        "instance_limit": limit,
        "currency": options.get("currency"),
    }


def find_server(token: str) -> dict[str, Any] | None:
    servers = list_data(token, f"/regions/{REGION}/servers", "servers")
    matches = [server for server in servers if server.get("name") == SERVER_NAME]
    if len(matches) > 1:
        raise ProvisionError("multiple recovery servers exist")
    return matches[0] if matches else None


def verify_server_contract(server: dict[str, Any]) -> None:
    if server.get("name") != SERVER_NAME:
        raise ProvisionError("server name does not match the approved recovery drill")
    flavor = server.get("flavor")
    image = server.get("image")
    if not isinstance(flavor, dict) or flavor.get("id") != PLAN_ID:
        raise ProvisionError("server flavor does not match the approved plan")
    if not isinstance(image, dict) or image.get("id") != IMAGE_ID:
        raise ProvisionError("server image does not match Ubuntu 24.04")


def create_server(
    token: str,
    public_key: str,
    preflight: dict[str, Any],
    security_group: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    group_id = security_group.get("id")
    if not isinstance(group_id, str) or not group_id:
        raise ProvisionError("cannot create server without the isolated security group")
    created = response_data(
        api_request(
            "POST",
            f"/regions/{REGION}/servers",
            token,
            {
                "name": SERVER_NAME,
                "network_ids": [preflight["network_id"]],
                "flavor_id": PLAN_ID,
                "image_id": IMAGE_ID,
                "security_groups": [{"name": group_id}],
                "ssh_key": False,
                "key_name": 0,
                "count": 1,
                "create_type": "image",
                "disk_size": DISK_SIZE_GB,
                "init_script": init_script(public_key),
                "ha_enabled": False,
            },
            timeout=90,
        ),
        "create server",
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise ProvisionError("server creation returned no server id")
    return created, str(created.get("password") or "")


def wait_for_server(token: str, server_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 600
    last_status = "unknown"
    while time.monotonic() < deadline:
        data = response_data(
            api_request("GET", f"/regions/{REGION}/servers/{server_id}", token),
            "read created server",
        )
        if not isinstance(data, dict):
            raise ProvisionError("server read returned invalid data")
        last_status = str(data.get("status", "unknown")).lower()
        if last_status == "active" and server_public_ipv4(data):
            return data
        if last_status == "error":
            raise ProvisionError("Arvan reported server creation error")
        time.sleep(5)
    raise ProvisionError(f"server did not become active in time; last_status={last_status}")


def write_state(
    server: dict[str, Any],
    password: str,
    plan: dict[str, Any],
    security_group_mode: str,
) -> None:
    server_id = server.get("id")
    public_ip = server_public_ipv4(server)
    if not isinstance(server_id, str) or not server_id or not public_ip:
        raise ProvisionError("cannot persist incomplete server state")
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (
        f"ARVAN_RECOVERY_SERVER_ID={server_id}\n"
        f"ARVAN_RECOVERY_SERVER_NAME={SERVER_NAME}\n"
        f"ARVAN_RECOVERY_REGION={REGION}\n"
        f"ARVAN_RECOVERY_PUBLIC_IP={public_ip}\n"
        f"ARVAN_RECOVERY_PLAN={PLAN_ID}\n"
        f"ARVAN_RECOVERY_IMAGE_ID={IMAGE_ID}\n"
        f"ARVAN_RECOVERY_SECURITY_GROUP_MODE={security_group_mode}\n"
        f"ARVAN_RECOVERY_MONTHLY_IRR={plan.get('price_per_month', '')}\n"
        f"ARVAN_RECOVERY_HOURLY_IRR={plan.get('price_per_hour', '')}\n"
        f"ARVAN_RECOVERY_BOOTSTRAP_PASSWORD={password}\n"
        f"ARVAN_RECOVERY_CREATED_AT={server.get('created', '')}\n"
    )
    temporary = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    os.replace(temporary, STATE_FILE)
    os.chmod(STATE_FILE, 0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = read_private_text(args.token_file)
    public_key = validate_public_key(PUBLIC_KEY_FILE)
    validate_init_script(public_key)
    preflight = validate_preflight(token)
    existing = find_server(token)
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "apply": False,
                    "region": REGION,
                    "name": SERVER_NAME,
                    "plan": PLAN_ID,
                    "image": "ubuntu-24.04",
                    "disk_gb": DISK_SIZE_GB,
                    "monthly_irr": preflight["plan"].get("price_per_month"),
                    "hourly_irr": preflight["plan"].get("price_per_hour"),
                    "existing_server": existing is not None,
                    "security_group_exists": find_security_group(token) is not None,
                    "security_group_fallback": "arDefault-plus-host-firewall",
                    "ssh_key_delivery": "init-script-ed25519",
                    "billable_resource_created": False,
                },
                sort_keys=True,
            )
        )
        return 0

    security_group_mode = "dedicated"
    try:
        security_group = ensure_security_group(token, apply=True)
        assert security_group is not None
    except ApiPermissionError:
        security_group = default_security_group(token)
        security_group_mode = "arDefault-plus-host-firewall"
    password = ""
    server = existing
    if server is None:
        try:
            created, password = create_server(token, public_key, preflight, security_group)
            server_id = str(created["id"])
        except ProvisionError:
            recovered = find_server(token)
            if recovered is None:
                raise
            server_id = str(recovered.get("id") or "")
            if not server_id:
                raise ProvisionError("created server recovery has no id")
        server = wait_for_server(token, server_id)
    else:
        verify_server_contract(server)
        server_id = str(server.get("id") or "")
        if not server_id:
            raise ProvisionError("existing server has no id")
        server = wait_for_server(token, server_id)
    verify_server_contract(server)
    write_state(server, password, preflight["plan"], security_group_mode)
    print(
        json.dumps(
            {
                "status": "active",
                "region": REGION,
                "name": SERVER_NAME,
                "server_id": server.get("id"),
                "public_ip": server_public_ipv4(server),
                "plan": PLAN_ID,
                "image": "ubuntu-24.04",
                "disk_gb": DISK_SIZE_GB,
                "monthly_irr": preflight["plan"].get("price_per_month"),
                "hourly_irr": preflight["plan"].get("price_per_hour"),
                "security_group": (
                    SECURITY_GROUP_NAME
                    if security_group_mode == "dedicated"
                    else "arDefault"
                ),
                "security_group_mode": security_group_mode,
                "password_printed": False,
                "state_file_mode": "0600",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
