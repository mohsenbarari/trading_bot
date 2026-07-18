#!/usr/bin/env python3
"""Build and execute the read-only preflight for the real-host Witness matrix.

This tool deliberately stops before fault injection.  It proves that the dark
Witness target, its rollback path, both WebApp hosts, and the source checkout
are safe enough to start the separately approved failure campaign.  It never
issues a Witness transition, changes a firewall, stops a service, changes a
clock, fills a disk, mutates Arvan, or copies credentials into a WebApp runtime.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BRANCH = "feature/arvan-controlled-origin-failover"
WEBAPP_FI = "65.109.220.59"
WEBAPP_IR = "87.236.212.194"
WEBAPP_IR_SSH_PORT = 37067
MATRIX_WITNESS = "185.206.95.94"
ROLLBACK_WITNESS = "185.231.182.6"
CONTROL_SSH_SOURCE = "65.109.216.187"
SCHEMA_VERSION = "writer_witness_real_host_matrix_preflight_v1"
# The rollback Witness deliberately remains outside this feature deployment.
# Bind the one helper that preflight executes there to the reviewed bytes that
# are already installed on that immutable reference host.  Any redeployment of
# that host must deliberately update this pin before another Matrix campaign.
ROLLBACK_STATE_MANIFEST_SHA256 = (
    "0be506962c48d6e19b9f13b8e8f4f5961fade0d4a852de0dcea9d2819a7d61a7"
)


SOURCE_GATE = ROOT / "scripts/run_writer_witness_preflight_source_gate.sh"
PINNED_SOURCE_PATHS = (
    "scripts/build_writer_witness_release.sh",
    "scripts/build_writer_witness_wheelhouse.sh",
    "scripts/plan_writer_witness_real_host_matrix.py",
    "scripts/provision_writer_witness_host.sh",
    "scripts/provision_writer_witness_matrix_controller.py",
    "scripts/render_writer_witness_credentials.py",
    "scripts/run_writer_witness_preflight_source_gate.sh",
    "scripts/run_writer_witness_failure_drill.sh",
    "scripts/run_writer_witness_clock_jump_probe.py",
    "scripts/run_writer_witness_postgres_gate.py",
    "scripts/run_writer_witness_real_host_matrix.py",
    "scripts/verify_writer_witness_release.py",
    "scripts/verify_writer_witness_runtime.py",
    "scripts/verify_writer_witness_host_toolchain.py",
    "scripts/verify_writer_witness_runtime_provenance.py",
    "scripts/verify_writer_witness_process_maps.py",
    "scripts/verify_writer_witness_wheelhouse.py",
    "scripts/verify_writer_witness_nftables.py",
    "scripts/writer_witness_matrix_client.py",
    "writer_witness_app.py",
    "deploy/writer-witness-drill/docker-compose.yml",
    "deploy/writer-witness/writer-witness-live-restore.sh",
    "deploy/writer-witness/writer-witness-activation.py",
    "deploy/writer-witness/writer-witness-activation-recovery.service",
    "deploy/writer-witness/writer-witness-activation-watchdog.sh",
    "deploy/writer-witness/writer-witness-activation-watchdog.service",
    "deploy/writer-witness/writer-witness-activation-watchdog.timer",
    "deploy/writer-witness/writer-witness-matrix-campaign.py",
    "deploy/writer-witness/writer-witness-matrix-host-faults.sh",
    "deploy/writer-witness/writer-witness-matrix-host-fault-state.py",
    "deploy/writer-witness/writer-witness-rotate-hmac.py",
    "deploy/writer-witness/writer-witness.service",
    "deploy/writer-witness/writer-witness-state-manifest.sh",
    "deploy/writer-witness/python-runtime.json",
    "deploy/writer-witness/nftables-policy.json",
    "deploy/writer-witness/requirements.lock",
    "deploy/writer-witness/wheelhouse.sha256",
    "deploy/writer-witness/nginx.conf.template",
)


REMOTE_SECURE_FILE_ATTESTATION = r"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def read_secure(path: Path, *, expected_mode: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or stat.S_IMODE(before.st_mode) != expected_mode
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > 16 * 1024 * 1024
        ):
            raise SystemExit(f"unsafe installed artifact metadata: {path}")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise SystemExit(f"short installed artifact read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_uid,
            value.st_gid,
            stat.S_IMODE(value.st_mode),
            value.st_nlink,
        )
        if identity(before) != identity(after):
            raise SystemExit(f"installed artifact changed during attestation: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


mode = sys.argv[1]
if mode == "expected":
    path = Path(sys.argv[2])
    expected_sha256 = sys.argv[3]
    expected_mode = int(sys.argv[4], 8)
    if hashlib.sha256(read_secure(path, expected_mode=expected_mode)).hexdigest() != expected_sha256:
        raise SystemExit(f"installed artifact digest mismatch: {path}")
    print(json.dumps({"status": "attested", "path": str(path)}, sort_keys=True))
elif mode == "release-map":
    release = Path(sys.argv[2])
    mapping = json.loads(sys.argv[3])
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(mapping, list) or not isinstance(manifest, dict):
        raise SystemExit("invalid installed-artifact attestation input")
    for item in mapping:
        installed = Path(item["installed"])
        relative = item["source"]
        expected = manifest.get(relative)
        if not isinstance(expected, str) or not re_fullmatch_sha256(expected):
            raise SystemExit(f"release manifest lacks installed artifact: {relative}")
        observed = hashlib.sha256(
            read_secure(installed, expected_mode=int(item["mode"], 8))
        ).hexdigest()
        if observed != expected:
            raise SystemExit(f"installed artifact differs from release: {installed}")
    print(json.dumps({"status": "attested", "count": len(mapping)}, sort_keys=True))
else:
    raise SystemExit("invalid installed-artifact attestation mode")
""".replace(
    "import sys\n", "import sys\nimport re\n\nre_fullmatch_sha256 = lambda value: re.fullmatch(r'[0-9a-f]{64}', value) is not None\n", 1
)


REMOTE_OFFSITE_MARKER_ATTESTATION = r"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
expected_file = sys.argv[2]
expected_sha256 = sys.argv[3]
descriptor = os.open(
    path,
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_gid != 0
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or before.st_size < 1
        or before.st_size > 64 * 1024
    ):
        raise SystemExit("offsite marker metadata is unsafe")
    raw = b""
    while len(raw) < before.st_size:
        chunk = os.read(descriptor, before.st_size - len(raw))
        if not chunk:
            raise SystemExit("offsite marker read was short")
        raw += chunk
    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise SystemExit("offsite marker changed during attestation")
finally:
    os.close(descriptor)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit("offsite marker contains duplicate keys")
        result[key] = value
    return result


payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
upload = payload.get("upload") if isinstance(payload, dict) else None
encrypted_file = f"{expected_file}.age"
if (
    payload.get("status") != "uploaded"
    or payload.get("source_file") != expected_file
    or payload.get("source_sha256") != expected_sha256
    or payload.get("encrypted_file") != encrypted_file
    or not isinstance(payload.get("encrypted_sha256"), str)
    or len(payload["encrypted_sha256"]) != 64
    or not isinstance(payload.get("encrypted_bytes"), int)
    or payload["encrypted_bytes"] < 1
    or not isinstance(upload, dict)
    or upload.get("status") != "uploaded"
    or upload.get("object_key") != f"witness/{encrypted_file}"
    or upload.get("sha256") != payload["encrypted_sha256"]
    or upload.get("bytes") != payload["encrypted_bytes"]
    or not isinstance(upload.get("version_id"), str)
    or not upload["version_id"]
):
    raise SystemExit("offsite marker does not bind the latest backup upload")
uploaded_at = datetime.fromisoformat(str(payload.get("uploaded_at", "")).replace("Z", "+00:00"))
age = (datetime.now(timezone.utc) - uploaded_at.astimezone(timezone.utc)).total_seconds()
if age < -300 or age > 7 * 24 * 60 * 60:
    raise SystemExit("offsite upload marker is stale or future-dated")
print(json.dumps({"offsite_upload_attested": "yes"}, sort_keys=True))
"""


def python_inline(source: str, *arguments: str) -> str:
    return shlex.join(
        (
            "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
            "/usr/bin/python3.12", "-I", "-S", "-B", "-X", "utf8",
            "-X", "pycache_prefix=/dev/null", "-c", source, *arguments,
        )
    )


def rendered_nginx_sha256() -> str:
    rendered = (
        (ROOT / "deploy/writer-witness/nginx.conf.template")
        .read_text(encoding="utf-8")
        .replace("__WEBAPP_FI_SOURCE_IP__", WEBAPP_FI)
        .replace("__WEBAPP_IR_SOURCE_IP__", WEBAPP_IR)
        .replace("__WITNESS_PUBLIC_IP__", MATRIX_WITNESS)
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def python_runtime_binding() -> dict[str, str]:
    runtime_path = ROOT / "deploy/writer-witness/python-runtime.json"
    runtime_bytes = runtime_path.read_bytes()
    payload = json.loads(runtime_bytes.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "architecture",
        "elf_objects",
        "executable_path",
        "executable_sha256",
        "implementation",
        "loader",
        "os_release",
        "packages",
        "python_version",
        "schema_version",
        "stdlib",
        "venv_elf_system_roots",
    }:
        raise RuntimeError("Writer Witness Python runtime binding has an invalid schema")
    version = payload.get("python_version")
    executable_path = payload.get("executable_path")
    executable_sha256 = payload.get("executable_sha256")
    stdlib = payload.get("stdlib")
    loader = payload.get("loader")
    os_release = payload.get("os_release")
    elf_objects = payload.get("elf_objects")
    packages = payload.get("packages")
    venv_elf_system_roots = payload.get("venv_elf_system_roots")
    if (
        payload.get("schema_version") != "writer_witness_system_runtime_v2"
        or payload.get("implementation") != "CPython"
        or payload.get("architecture") != "x86_64"
        or not isinstance(version, str)
        or re.fullmatch(r"3\.12\.[0-9]+", version) is None
        or executable_path != "/usr/bin/python3.12"
        or not isinstance(executable_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", executable_sha256) is None
        or not isinstance(stdlib, dict)
        or set(stdlib)
        != {
            "entry_count",
            "external_files",
            "file_count",
            "inactive_import_paths",
            "packages",
            "path",
            "python_path",
            "symlink_count",
            "tree_sha256",
        }
        or stdlib.get("path") != "/usr/lib/python3.12"
        or stdlib.get("inactive_import_paths") != ["/usr/lib/python312.zip"]
        or stdlib.get("python_path")
        != [
            "/usr/lib/python312.zip",
            "/usr/lib/python3.12",
            "/usr/lib/python3.12/lib-dynload",
        ]
        or not isinstance(stdlib.get("tree_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(stdlib.get("tree_sha256"))) is None
        or not isinstance(loader, dict)
        or not isinstance(os_release, dict)
        or os_release.get("id") != "ubuntu"
        or os_release.get("version_id") != "24.04"
        or not isinstance(elf_objects, list)
        or not elf_objects
        or not isinstance(packages, list)
        or not packages
        or not isinstance(venv_elf_system_roots, list)
        or not venv_elf_system_roots
        or any(
            not isinstance(path, str) or not path.startswith("/usr/lib/")
            for path in venv_elf_system_roots
        )
    ):
        raise RuntimeError("Writer Witness Python runtime binding is invalid")
    return {
        "executable_path": executable_path,
        "python_version": version,
        "python_sha256": executable_sha256,
        "system_runtime_manifest_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
    }


def nftables_policy_binding() -> dict[str, object]:
    payload = json.loads(
        (ROOT / "deploy/writer-witness/nftables-policy.json").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or set(payload) != {
        "chain_count",
        "policy_sha256",
        "rule_count",
        "schema_version",
        "table_count",
    }:
        raise RuntimeError("Writer Witness nftables policy binding has an invalid schema")
    if (
        payload.get("schema_version") != "writer_witness_nftables_policy_v1"
        or payload.get("table_count") != 2
        or not isinstance(payload.get("chain_count"), int)
        or int(payload["chain_count"]) < 1
        or not isinstance(payload.get("rule_count"), int)
        or int(payload["rule_count"]) < 1
        or not isinstance(payload.get("policy_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(payload["policy_sha256"])) is None
    ):
        raise RuntimeError("Writer Witness nftables policy binding is invalid")
    return payload


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    command: tuple[str, ...]
    host_role: str
    mutates_state: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ssh_command(host: str, script: str, *, port: int = 22) -> tuple[str, ...]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if port != 22:
        command.extend(("-p", str(port)))
    command.extend((f"root@{host}", script))
    return tuple(command)


def remote_check_specs(
    *,
    include_source_tests: bool = True,
    expected_commit: str | None = None,
    expected_release_manifest_sha256: str | None = None,
    expected_active_campaign_tag: str | None = None,
    expected_active_campaign_scenario: str | None = None,
    expected_active_campaign_not_after: str | None = None,
    allow_expired_active_campaign: bool = False,
) -> list[CheckSpec]:
    expected_commit = str(expected_commit or "").strip()
    if not expected_commit:
        expected_commit = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    if len(expected_commit) != 40 or any(char not in "0123456789abcdef" for char in expected_commit):
        raise ValueError("expected_commit must be one lowercase 40-character Git SHA")
    expected_release_manifest_sha256 = str(expected_release_manifest_sha256 or "").strip()
    if not expected_release_manifest_sha256:
        expected_release_manifest_sha256 = witness_release_manifest_sha256()
    if (
        len(expected_release_manifest_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_release_manifest_sha256)
    ):
        raise ValueError("expected_release_manifest_sha256 must be 64 lowercase hex characters")
    release_verifier_sha256 = hashlib.sha256(
        (ROOT / "scripts/verify_writer_witness_release.py").read_bytes()
    ).hexdigest()
    runtime_binding = python_runtime_binding()
    expected_python_executable = runtime_binding["executable_path"]
    expected_python_version = runtime_binding["python_version"]
    expected_python_sha256 = runtime_binding["python_sha256"]
    expected_system_runtime_manifest_sha256 = runtime_binding[
        "system_runtime_manifest_sha256"
    ]
    expected_requirements_lock_sha256 = hashlib.sha256(
        (ROOT / "deploy/writer-witness/requirements.lock").read_bytes()
    ).hexdigest()
    expected_wheelhouse_manifest_sha256 = hashlib.sha256(
        (ROOT / "deploy/writer-witness/wheelhouse.sha256").read_bytes()
    ).hexdigest()
    expected_nftables_policy_sha256 = str(nftables_policy_binding()["policy_sha256"])
    clean_python_prefix = "/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin"
    clean_python_flags = "-I -S -B -X utf8 -X pycache_prefix=/dev/null"
    pip_check_source = (
        "import runpy,sys; site_packages=sys.argv[1]; "
        "sys.path.insert(0,site_packages); "
        "sys.argv=['pip','--isolated','--disable-pip-version-check',"
        "'--no-cache-dir','check']; runpy.run_module('pip',run_name='__main__')"
    )
    installed_release_artifacts = [
        {
            "installed": f"/usr/local/sbin/{installed}",
            "source": source,
            "mode": "0755",
        }
        for installed, source in (
            ("writer-witness-backup", "deploy/writer-witness/writer-witness-backup.sh"),
            (
                "writer-witness-offsite-backup",
                "deploy/writer-witness/writer-witness-offsite-backup.sh",
            ),
            ("writer-witness-s3-put", "deploy/writer-witness/writer-witness-s3-put.py"),
            (
                "writer-witness-live-restore",
                "deploy/writer-witness/writer-witness-live-restore.sh",
            ),
            (
                "writer-witness-rotate-hmac",
                "deploy/writer-witness/writer-witness-rotate-hmac.py",
            ),
            (
                "writer-witness-matrix-campaign",
                "deploy/writer-witness/writer-witness-matrix-campaign.py",
            ),
            (
                "writer-witness-matrix-host-faults",
                "deploy/writer-witness/writer-witness-matrix-host-faults.sh",
            ),
            (
                "writer-witness-matrix-host-fault-state",
                "deploy/writer-witness/writer-witness-matrix-host-fault-state.py",
            ),
            (
                "writer-witness-state-manifest",
                "deploy/writer-witness/writer-witness-state-manifest.sh",
            ),
            (
                "writer-witness-restore-drill",
                "deploy/writer-witness/writer-witness-restore-drill.sh",
            ),
            ("writer-witness-smoke-client", "scripts/smoke_writer_witness_client.py"),
        )
    ]
    installed_release_artifacts.extend(
        (
            {
                "installed": "/usr/local/sbin/writer-witness-activation",
                "source": "deploy/writer-witness/writer-witness-activation.py",
                "mode": "0755",
            },
            {
                "installed": "/usr/local/sbin/writer-witness-activation-watchdog",
                "source": "deploy/writer-witness/writer-witness-activation-watchdog.sh",
                "mode": "0755",
            },
        )
    )
    installed_release_artifacts.extend(
        {
            "installed": f"/etc/systemd/system/{unit}",
            "source": f"deploy/writer-witness/{unit}",
            "mode": "0644",
        }
        for unit in (
            "writer-witness.service",
            "writer-witness-backup.service",
            "writer-witness-backup.timer",
            "writer-witness-offsite-backup.service",
            "writer-witness-offsite-backup.timer",
            "writer-witness-activation-recovery.service",
            "writer-witness-activation-watchdog.service",
            "writer-witness-activation-watchdog.timer",
        )
    )
    installed_release_attestation = (
        python_inline(REMOTE_SECURE_FILE_ATTESTATION, "release-map")
        + ' "$release" '
        + shlex.quote(json.dumps(installed_release_artifacts, separators=(",", ":")))
    )
    rollback_helper_attestation = python_inline(
        REMOTE_SECURE_FILE_ATTESTATION,
        "expected",
        "/usr/local/sbin/writer-witness-state-manifest",
        ROLLBACK_STATE_MANIFEST_SHA256,
        "0755",
    )
    nginx_attestation = python_inline(
        REMOTE_SECURE_FILE_ATTESTATION,
        "expected",
        "/etc/nginx/sites-available/writer-witness",
        rendered_nginx_sha256(),
        "0644",
    )
    offsite_marker_attestation = (
        python_inline(REMOTE_OFFSITE_MARKER_ATTESTATION)
        + ' "$latest.offsite.json" "$(basename "$latest")" "$backup_sha"'
    )
    if expected_active_campaign_tag is not None:
        if not re.fullmatch(r"wwm_[0-9a-f]{12}", expected_active_campaign_tag):
            raise ValueError("expected_active_campaign_tag is invalid")
        if not re.fullmatch(r"RH-(?:00[1-9]|01[0-2])", str(expected_active_campaign_scenario or "")):
            raise ValueError("expected_active_campaign_scenario is required and invalid")
        try:
            campaign_expiry = datetime.fromisoformat(
                str(expected_active_campaign_not_after or "").replace("Z", "+00:00")
            )
            if campaign_expiry.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise ValueError("expected_active_campaign_not_after is required and invalid") from exc
        campaign_guard = (
            "/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin "
            "/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null "
            "/usr/local/sbin/writer-witness-matrix-campaign assert "
            f"--tag {shlex.quote(expected_active_campaign_tag)} "
            f"--expected-commit {shlex.quote(expected_commit)} "
            f"--scenario {shlex.quote(str(expected_active_campaign_scenario))} "
            f"--not-after {shlex.quote(str(expected_active_campaign_not_after))} "
            f"--expect {'active-cleanup' if allow_expired_active_campaign else 'active'} "
            ">/dev/null; campaign_state=owned"
        )
    else:
        if (
            expected_active_campaign_scenario is not None
            or expected_active_campaign_not_after is not None
            or allow_expired_active_campaign
        ):
            raise ValueError("active campaign options require a campaign tag")
        campaign_guard = (
            "test ! -e /var/lib/trading-bot-witness/matrix-campaign/active; "
            "test ! -L /var/lib/trading-bot-witness/matrix-campaign/active; "
            "test ! -e /var/lib/trading-bot-witness/matrix-campaign/active.json; "
            "test ! -L /var/lib/trading-bot-witness/matrix-campaign/active.json; "
            "campaign_state=absent"
        )
    specs = [
        CheckSpec(
            "git_branch_clean",
            (
                "bash",
                "-lc",
                "test \"$(git branch --show-current)\" = "
                f"\"{EXPECTED_BRANCH}\" && test \"$(git rev-parse HEAD)\" = \"{expected_commit}\" "
                "&& test -z \"$(git status --porcelain)\" "
                "&& git diff --check && printf 'branch=%s\\nhead=%s\\nclean=yes\\n' "
                "\"$(git branch --show-current)\" \"$(git rev-parse HEAD)\"",
            ),
            "control",
        ),
        CheckSpec(
            "webapp_fi_baseline",
            ssh_command(
                WEBAPP_FI,
                "set -Eeuo pipefail; "
                "echo role=webapp_fi; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for c in trading_bot_app trading_bot_db trading_bot_redis trading_bot_sync_worker; do "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$c\")\" = running; done; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_app)\" = healthy; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' trading_bot_db)\" = healthy; "
                "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
                "curl -fsS http://127.0.0.1:8000/api/config >/dev/null; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' "
                "/srv/trading-bot/current/.env; for c in trading_bot_app trading_bot_sync_worker; do "
                "docker inspect \"$c\" --format '{{range .Config.Env}}{{println .}}{{end}}'; done | sed -n "
                "'/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                "| awk -F= '{v=tolower($2); gsub(/[[:space:]]/,\"\",v); "
                "if (v ~ /^(1|true|t|yes|y|on)$/) found=1} END {exit found ? 0 : 1}'; then exit 41; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "for c in trading_bot_app trading_bot_sync_worker; do ! docker inspect \"$c\" "
                "--format '{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
                "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done; "
                "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -maxdepth 2 -print -quit 2>/dev/null)\"; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                f"cert=$(timeout 8 openssl s_client -connect {MATRIX_WITNESS}:443 "
                "-servername writer-witness.internal </dev/null 2>/dev/null "
                "| openssl x509 -outform DER | sha256sum | awk '{print $1}'); "
                "test ${#cert} -eq 64; "
                f"unsigned=$(curl -k -sS -o /dev/null -w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); "
                "test \"$unsigned\" = 401; "
                "release=$(docker inspect trading_bot_app --format '{{range .Config.Env}}{{println .}}{{end}}' "
                "| sed -n 's/^RELEASE_SHA=//p' | head -1); test -n \"$release\"; "
                "echo ntp=yes; echo app=healthy; echo db=healthy; echo api=200; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable; "
                "echo witness_unsigned_status=401; echo client_credentials_installed=no; echo witness_cert_sha256=$cert; echo release_sha=$release",
            ),
            "webapp_fi",
        ),
        CheckSpec(
            "webapp_ir_standby_baseline",
            ssh_command(
                WEBAPP_IR,
                "set -Eeuo pipefail; "
                "echo role=webapp_ir; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "app=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=app); test -n \"$app\"; "
                "sync=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=sync_worker); test -n \"$sync\"; "
                "db=$(docker ps -aq --filter label=com.docker.compose.project=current "
                "--filter label=com.docker.compose.service=db); test -n \"$db\"; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$app\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$sync\")\" != running; "
                "test \"$(docker inspect -f '{{.State.Status}}' \"$db\")\" = running; "
                "test \"$(docker inspect -f '{{.State.Health.Status}}' \"$db\")\" = healthy; "
                "test \"$(findmnt -n -o FSTYPE -T /run)\" = tmpfs; "
                "if { sed -n '/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p' "
                "/srv/trading-bot/current/.env; for c in \"$app\" \"$sync\"; do docker inspect \"$c\" "
                "--format '{{range .Config.Env}}{{println .}}{{end}}'; done | sed -n "
                "'/^WRITER_WITNESS_\\(REQUIRED\\|AUTO_RENEW_ENABLED\\|SERVICE_ENABLED\\)=/p'; } "
                "| awk -F= '{v=tolower($2); gsub(/[[:space:]]/,\"\",v); "
                "if (v ~ /^(1|true|t|yes|y|on)$/) found=1} END {exit found ? 0 : 1}'; then exit 42; fi; "
                "! grep -Eq '^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))=' /srv/trading-bot/current/.env; "
                "for c in \"$app\" \"$sync\"; do ! docker inspect \"$c\" --format "
                "'{{range .Config.Env}}{{println .}}{{end}}' | grep -Eq "
                "'^WRITER_WITNESS_(INTERNAL_URL|CLIENT_(KEY_ID|SECRET))='; done; "
                "test -z \"$(find /run/writer-witness-matrix -mindepth 1 -maxdepth 2 -print -quit 2>/dev/null)\"; "
                f"timeout 5 bash -c '</dev/tcp/{MATRIX_WITNESS}/443'; "
                f"cert=$(timeout 8 openssl s_client -connect {MATRIX_WITNESS}:443 "
                "-servername writer-witness.internal </dev/null 2>/dev/null "
                "| openssl x509 -outform DER | sha256sum | awk '{print $1}'); "
                "test ${#cert} -eq 64; "
                f"unsigned=$(curl -k -sS -o /dev/null -w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); "
                "test \"$unsigned\" = 401; "
                "echo ntp=yes; echo app=stopped; echo sync_worker=stopped; echo db=running; "
                "echo witness_flags_enabled=no; echo witness_tcp_443=reachable; "
                "echo witness_unsigned_status=401; echo client_credentials_installed=no; echo witness_cert_sha256=$cert",
                port=WEBAPP_IR_SSH_PORT,
            ),
            "webapp_ir",
        ),
        CheckSpec(
            "matrix_witness_dark_baseline",
            ssh_command(
                MATRIX_WITNESS,
                "set -Eeuo pipefail; export PYTHONDONTWRITEBYTECODE=1; "
                "echo role=matrix_witness; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for u in writer-witness postgresql nginx ufw; do test \"$(systemctl is-active \"$u\")\" = active; done; "
                "curl -fsS http://127.0.0.1:8011/health/ready >/dev/null; "
                "state=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT authority||chr(58)||writer_epoch||chr(58)||lease_status FROM webapp_writer_witness_state;\"); "
                "test \"$state\" = webapp:0:vacant; "
                "receipts=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT count(*) FROM webapp_writer_witness_receipts;\"); test \"$receipts\" = 0; "
                "test \"$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT version_num FROM writer_witness_schema_version;\")\" = 001; "
                "test -L /opt/trading-bot-witness/active; "
                "activation=$(readlink -f /opt/trading-bot-witness/active); "
                "case \"$activation\" in /opt/trading-bot-witness/activations/*) ;; *) exit 50;; esac; "
                "test -d \"$activation\"; test ! -L \"$activation\"; "
                "test \"$(stat -c %u \"$activation\")\" = 0; test \"$(stat -c %g \"$activation\")\" = 0; "
                "test \"$(stat -c %a \"$activation\")\" = 755; "
                "test -L \"$activation/release\"; test -L \"$activation/venv\"; "
                "test -z \"$(find \"$activation\" -mindepth 1 -maxdepth 1 ! -name release ! -name venv ! -name runtime-provenance.json -print -quit)\"; "
                "release=$(readlink -f /opt/trading-bot-witness/active/release); "
                "test -n \"$release\"; test -d \"$release\"; test ! -L \"$release\"; "
                "test \"$(readlink -f /srv/trading-bot-witness/current)\" = \"$release\"; "
                "pid=$(systemctl show -p MainPID --value writer-witness.service); test \"$pid\" -gt 1; "
                "test \"$(readlink -f /proc/$pid/cwd)\" = \"$release\"; "
                "test \"$(sha256sum \"$release/release-manifest.json\" | awk '{print $1}')\" = "
                f"\"{expected_release_manifest_sha256}\"; "
                "test \"$(sha256sum \"$release/scripts/verify_writer_witness_release.py\" | awk '{print $1}')\" = "
                f"\"{release_verifier_sha256}\"; "
                f"{clean_python_prefix} {shlex.quote(expected_python_executable)} "
                f"{clean_python_flags} \"$release/scripts/verify_writer_witness_release.py\" "
                "--release-root \"$release\" "
                f"--expected-manifest-sha256 \"{expected_release_manifest_sha256}\" "
                "--expected-uid 0 --expected-gid 0; "
                f"system_runtime_attestation=$({clean_python_prefix} "
                f"{shlex.quote(expected_python_executable)} {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_runtime.py\" --system-only "
                "--system-runtime-manifest \"$release/deploy/writer-witness/python-runtime.json\" "
                f"--expected-system-runtime-manifest-sha256 {shlex.quote(expected_system_runtime_manifest_sha256)} "
                "--expected-lock-uid 0); "
                "printf '%s' \"$system_runtime_attestation\" "
                "| grep -F '\"system_runtime_attested\":\"yes\"' >/dev/null; "
                + installed_release_attestation
                + "; installed_artifacts_attested=yes; "
                "test \"$(systemctl show -p FragmentPath --value writer-witness.service)\" = "
                "'/etc/systemd/system/writer-witness.service'; "
                "test -z \"$(systemctl show -p DropInPaths --value writer-witness.service)\"; "
                "for property in User:writer-witness Group:writer-witness "
                "WorkingDirectory:/opt/trading-bot-witness/active/release "
                "NoNewPrivileges:yes PrivateTmp:yes PrivateDevices:yes ProtectSystem:strict "
                "ProtectHome:yes MemoryDenyWriteExecute:yes RestrictSUIDSGID:yes "
                "LockPersonality:yes UMask:0077; do key=${property%%:*}; expected=${property#*:}; "
                "test \"$(systemctl show -p \"$key\" --value writer-witness.service)\" = \"$expected\"; done; "
                "test -L /opt/trading-bot-witness/venv; "
                "venv_real=$(readlink -f /opt/trading-bot-witness/active/venv); "
                "case \"$venv_real\" in /opt/trading-bot-witness/venvs/*) ;; *) exit 51;; esac; "
                "test -d \"$venv_real\"; test ! -L \"$venv_real\"; "
                "test \"$(readlink -f /opt/trading-bot-witness/venv)\" = \"$venv_real\"; "
                f"runtime_attestation_before_check=$({clean_python_prefix} "
                f"/opt/trading-bot-witness/active/venv/bin/python {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_runtime.py\" --requirements-lock "
                "\"$release/deploy/writer-witness/requirements.lock\" "
                "--system-runtime-manifest \"$release/deploy/writer-witness/python-runtime.json\" "
                f"--expected-system-runtime-manifest-sha256 {shlex.quote(expected_system_runtime_manifest_sha256)} "
                "--runtime-prefix /opt/trading-bot-witness/active/venv --expected-lock-uid 0 "
                f"--expected-python-version {shlex.quote(expected_python_version)} "
                f"--expected-python-sha256 {shlex.quote(expected_python_sha256)}); "
                "test -n \"$runtime_attestation_before_check\"; "
                f"/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin PIP_CONFIG_FILE=/dev/null "
                f"/opt/trading-bot-witness/active/venv/bin/python {clean_python_flags} "
                f"-c {shlex.quote(pip_check_source)} "
                "/opt/trading-bot-witness/active/venv/lib/python3.12/site-packages >/dev/null; "
                f"runtime_attestation=$({clean_python_prefix} "
                f"/opt/trading-bot-witness/active/venv/bin/python {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_runtime.py\" --requirements-lock "
                "\"$release/deploy/writer-witness/requirements.lock\" "
                "--system-runtime-manifest \"$release/deploy/writer-witness/python-runtime.json\" "
                f"--expected-system-runtime-manifest-sha256 {shlex.quote(expected_system_runtime_manifest_sha256)} "
                "--runtime-prefix /opt/trading-bot-witness/active/venv --expected-lock-uid 0 "
                f"--expected-python-version {shlex.quote(expected_python_version)} "
                f"--expected-python-sha256 {shlex.quote(expected_python_sha256)}); "
                "test \"$runtime_attestation\" = \"$runtime_attestation_before_check\"; "
                f"runtime_provenance_attestation=$({clean_python_prefix} "
                f"/opt/trading-bot-witness/active/venv/bin/python {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_runtime_provenance.py\" "
                "--provenance \"$activation/runtime-provenance.json\" "
                "--runtime-attestation-json \"$runtime_attestation\" "
                f"--expected-release-manifest-sha256 {shlex.quote(expected_release_manifest_sha256)} "
                f"--expected-wheelhouse-manifest-sha256 {shlex.quote(expected_wheelhouse_manifest_sha256)} "
                f"--expected-requirements-lock-sha256 {shlex.quote(expected_requirements_lock_sha256)} "
                f"--expected-python-version {shlex.quote(expected_python_version)} "
                f"--expected-python-sha256 {shlex.quote(expected_python_sha256)} "
                f"--expected-system-runtime-manifest-sha256 {shlex.quote(expected_system_runtime_manifest_sha256)} "
                "--expected-uid 0 --expected-gid 0); "
                "printf '%s' \"$runtime_provenance_attestation\" "
                "| grep -F '\"runtime_provenance_attested\":\"yes\"' >/dev/null; "
                f"process_maps_attestation=$({clean_python_prefix} "
                f"/opt/trading-bot-witness/active/venv/bin/python {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_process_maps.py\" "
                "--pid \"$pid\" --venv \"$venv_real\" "
                "--system-runtime-manifest \"$release/deploy/writer-witness/python-runtime.json\" "
                f"--expected-system-runtime-manifest-sha256 {shlex.quote(expected_system_runtime_manifest_sha256)}); "
                "printf '%s' \"$process_maps_attestation\" "
                "| grep -F '\"process_maps_attested\":\"yes\"' >/dev/null; "
                "systemctl show -p ExecStart --value writer-witness.service "
                "| grep -F '/opt/trading-bot-witness/active/venv/bin/python' >/dev/null; "
                "test \"$(readlink -f /proc/$pid/exe)\" = \"$(readlink -f \"$venv_real/bin/python\")\"; "
                "for forbidden in PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE BASH_ENV ENV SHELLOPTS BASHOPTS CDPATH GLOBIGNORE LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_PROFILE LD_SHOW_AUXV LD_BIND_NOW LD_BIND_NOT LD_ORIGIN_PATH LD_DYNAMIC_WEAK LD_HWCAP_MASK GLIBC_TUNABLES; do "
                "! tr '\\0' '\\n' </proc/$pid/environ | grep -q \"^$forbidden=\"; done; "
                "tr '\\0' '\\n' </proc/$pid/cmdline "
                "| grep -F '/opt/trading-bot-witness/active/venv/bin/python' >/dev/null; "
                "tr '\\0' '\\n' </proc/$pid/cmdline | grep -Fx -- '-I' >/dev/null; "
                "tr '\\0' '\\n' </proc/$pid/cmdline | grep -Fx -- '-B' >/dev/null; "
                "tr '\\0' '\\n' </proc/$pid/cmdline | grep -Fx -- 'uvicorn' >/dev/null; "
                "latest=$(find /var/backups/trading-bot-witness -maxdepth 1 -type f -name 'writer-witness-*.dump' "
                "-printf '%T@|%p\\n' | sort -nr | head -1 | cut -d'|' -f2-); test -n \"$latest\"; "
                "sha256sum --check \"$latest.sha256\" >/dev/null; "
                "backup_sha=$(awk '{print $1}' \"$latest.sha256\"); test ${#backup_sha} -eq 64; "
                "age=$(( $(date +%s) - $(stat -c %Y \"$latest\") )); test \"$age\" -le 86400; "
                "rollbacks=$(runuser -u postgres -- psql -XAt -d postgres -c "
                "\"SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_rollback_%' AND NOT datallowconn;\"); "
                "test \"$rollbacks\" -ge 1; "
                "rotation_root=/var/lib/trading-bot-witness/hmac-rotation; "
                "test -d \"$rotation_root\"; test ! -L \"$rotation_root\"; "
                "test \"$(stat -c %u \"$rotation_root\")\" = 0; "
                "test \"$(stat -c %a \"$rotation_root\")\" = 700; "
                "if test -e \"$rotation_root/.runtime.lock\" || test -L \"$rotation_root/.runtime.lock\"; then "
                "test -f \"$rotation_root/.runtime.lock\"; test ! -L \"$rotation_root/.runtime.lock\"; "
                "test \"$(stat -c %u \"$rotation_root/.runtime.lock\")\" = 0; "
                "test \"$(stat -c %a \"$rotation_root/.runtime.lock\")\" = 600; "
                "test \"$(stat -c %h \"$rotation_root/.runtime.lock\")\" = 1; "
                "flock -n \"$rotation_root/.runtime.lock\" -c true; fi; "
                "test -z \"$(find \"$rotation_root\" -mindepth 1 -maxdepth 1 ! -name '.runtime.lock' -print -quit)\"; "
                "test -z \"$(find /etc/trading-bot-witness -mindepth 1 -maxdepth 1 -name '.runtime.env.*' -print -quit)\"; "
                "test -z \"$(find /root/writer-witness-client-material -mindepth 1 -maxdepth 1 -name '.webapp-*.env.*' -print -quit)\"; "
                "activation_state=/var/lib/trading-bot-witness/activation-state; "
                "test ! -e \"$activation_state/active.json\"; test ! -L \"$activation_state/active.json\"; "
                "test -z \"$(find \"$activation_state\" -mindepth 1 -maxdepth 1 -name '.active.json.activation-*' -print -quit)\"; "
                "for lock in \"$activation_state/.activation.lock\" \"$activation_state/.provision.lock\" \"$rotation_root/.runtime.lock\"; do "
                "test -f \"$lock\"; test ! -L \"$lock\"; test \"$(stat -c %u \"$lock\")\" = 0; "
                "test \"$(stat -c %a \"$lock\")\" = 600; test \"$(stat -c %h \"$lock\")\" = 1; flock -n \"$lock\" -c true; done; "
                "marker=\"$activation_state/credential-state.json\"; bootstrap=/etc/trading-bot-witness/bootstrap-secrets.env; "
                "for file in \"$marker\" \"$bootstrap\"; do test -f \"$file\"; test ! -L \"$file\"; "
                "test \"$(stat -c %u \"$file\")\" = 0; test \"$(stat -c %a \"$file\")\" = 600; test \"$(stat -c %h \"$file\")\" = 1; done; "
                "test \"$(tr -d '\\n' <\"$marker\")\" = '{\"initialized\": true, \"schema_version\": \"writer_witness_credential_state_v1\"}'; "
                "test \"$(grep -Ec '^WITNESS_DB_(MIGRATOR|RUNTIME)_PASSWORD=[0-9a-f]{64}$' \"$bootstrap\")\" = 2; "
                "! grep -Eq '^WITNESS_(FI|IR)_(KEY_ID|HMAC_SECRET)=' \"$bootstrap\"; "
                "test \"$(systemctl is-enabled writer-witness-activation-watchdog.timer)\" = enabled; "
                "test \"$(systemctl is-active writer-witness-activation-watchdog.timer)\" = active; "
                "systemctl cat writer-witness-activation-recovery.service | grep -Fx "
                "'ExecStart=/usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null /usr/local/sbin/writer-witness-activation recover-boot' >/dev/null; "
                "test ! -e /var/lib/trading-bot-witness/restore-state/active.env; "
                "test ! -L /var/lib/trading-bot-witness/restore-state/active.env; "
                "test -z \"$(find /var/lib/trading-bot-witness/restore-state -mindepth 1 -maxdepth 1 -name '.active.*.env' -print -quit)\"; "
                "test ! -d /var/lib/trading-bot-witness/matrix-host-faults || "
                "test -z \"$(find /var/lib/trading-bot-witness/matrix-host-faults -mindepth 1 -maxdepth 1 -print -quit)\"; "
                "test -z \"$(find /run -mindepth 1 -maxdepth 1 -regextype posix-extended "
                "-regex '/run/wwm_[0-9a-f]{12}-(disk|clock)' -print -quit)\"; "
                "! ss -H -ltn | awk '{print $4}' | grep -Eq '(^|:)(55439|55440)$'; "
                "test -z \"$(find /var/backups/trading-bot-witness -maxdepth 1 -name '.replacement-restore.*.dump' -print -quit)\"; "
                "credential_bundle_sha=$(sha256sum /etc/trading-bot-witness/runtime.env "
                "/root/writer-witness-client-material/webapp-fi.env "
                "/root/writer-witness-client-material/webapp-ir.env "
                "/root/writer-witness-client-material/witness-ca.crt | sha256sum | awk '{print $1}'); "
                "test ${#credential_bundle_sha} -eq 64; "
                "cert=$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt -outform DER "
                "| sha256sum | awk '{print $1}'); test ${#cert} -eq 64; "
                "cert_end=$(date -d \"$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt "
                "-noout -enddate | cut -d= -f2-)\" +%s); test \"$cert_end\" -gt $(( $(date +%s) + 604800 )); "
                "for site in webapp_fi webapp_ir; do short=${site#webapp_}; "
                f"url=$(sed -n 's/^WRITER_WITNESS_INTERNAL_URL=//p' /root/writer-witness-client-material/webapp-$short.env); test \"$url\" = https://{MATRIX_WITNESS}; "
                f"{clean_python_prefix} /opt/trading-bot-witness/active/venv/bin/python "
                f"{clean_python_flags} /usr/local/sbin/writer-witness-smoke-client "
                "--env-file /root/writer-witness-client-material/webapp-$short.env "
                "--ca-bundle /etc/trading-bot-witness/tls/ca.crt --site \"$site\" >/dev/null; done; "
                "unsigned=$(curl --cacert /etc/trading-bot-witness/tls/ca.crt -sS -o /dev/null "
                f"-w '%{{http_code}}' https://{MATRIX_WITNESS}/v1/writer-witness/status); test \"$unsigned\" = 401; "
                "manifest=$(/usr/local/sbin/writer-witness-state-manifest); test ${#manifest} -eq 64; "
                + campaign_guard + "; "
                "test -z \"$(find /var/lib/trading-bot-witness/matrix-campaign -name '.campaign-write.*.tmp' -print -quit)\"; "
                "test \"$(dpkg-query -W -f='${Status}' libfaketime)\" = 'install ok installed'; "
                "mapfile -t faketime_libs < <(dpkg-query -L libfaketime | grep -E '/faketime/libfaketime\\.so\\.1$' | sort -u); "
                "test \"${#faketime_libs[@]}\" = 1; faketime_lib=$(realpath -e \"${faketime_libs[0]}\"); "
                "test \"$(stat -c %u \"$faketime_lib\")\" = 0; "
                "test $((8#$(stat -c %a \"$faketime_lib\") & 8#022)) = 0; "
                "! grep -Fq '/faketime/libfaketime.so' /proc/$pid/maps; "
                "production_data=$(runuser -u postgres -- psql -XAt writer_witness -c 'SHOW data_directory'); "
                "production_postgres_pid=$(head -1 \"$production_data/postmaster.pid\"); "
                "test \"$production_postgres_pid\" -gt 1; "
                "! grep -Fq '/faketime/libfaketime.so' /proc/$production_postgres_pid/maps; "
                "test \"$(systemctl is-enabled writer-witness-offsite-backup.timer)\" = enabled; "
                "test \"$(systemctl is-active writer-witness-offsite-backup.timer)\" = active; "
                "for spec in /etc/trading-bot-witness/offsite-backup.env:600 "
                "/etc/trading-bot-witness/offsite-age-recipient.txt:644; do "
                "path=${spec%:*}; mode=${spec#*:}; test -f \"$path\"; test ! -L \"$path\"; "
                "test \"$(stat -c %u \"$path\")\" = 0; test \"$(stat -c %g \"$path\")\" = 0; "
                "test \"$(stat -c %a \"$path\")\" = \"$mode\"; test \"$(stat -c %h \"$path\")\" = 1; done; "
                "command -v age >/dev/null; "
                "recipient=$(tr -d '\\r\\n' </etc/trading-bot-witness/offsite-age-recipient.txt); "
                "printf '%s' \"$recipient\" | grep -Eq '^age1[0-9a-z]+$'; "
                "effective_nginx=; age_probe=$(mktemp); "
                "trap 'rm -f \"${effective_nginx:-}\" \"${age_probe:-}\"' EXIT; "
                "age --encrypt --recipient \"$recipient\" --output \"$age_probe\" /dev/null; test -s \"$age_probe\"; "
                + offsite_marker_attestation
                + "; test \"$(systemctl show -p Result --value writer-witness-offsite-backup.service)\" = success; "
                "test \"$(systemctl show -p ExecMainStatus --value writer-witness-offsite-backup.service)\" = 0; "
                + nginx_attestation
                + "; test \"$(readlink /etc/nginx/sites-enabled/writer-witness)\" = "
                "'/etc/nginx/sites-available/writer-witness'; "
                "test -z \"$(find /etc/nginx/sites-enabled -mindepth 1 -maxdepth 1 ! -name writer-witness -print -quit)\"; "
                "effective_nginx=$(mktemp); nginx -T >\"$effective_nginx\" 2>/dev/null; "
                f"for rule in 'allow {WEBAPP_FI};' 'allow {WEBAPP_IR};' 'allow {MATRIX_WITNESS};' "
                "'allow 127.0.0.1;' 'allow ::1;' 'deny all;' 'client_max_body_size 16k;' "
                "'location = /v1/writer-witness/status {'; do "
                "grep -F -- \"$rule\" \"$effective_nginx\" >/dev/null; done; "
                "grep -F -- \"location = /v1/writer-witness/\"\"transitions {\" \"$effective_nginx\" >/dev/null; "
                "test \"$(grep -Ec '^[[:space:]]*listen[[:space:]].*443([[:space:]]|;)' \"$effective_nginx\")\" = 2; "
                "! grep -Eq '^[[:space:]]*allow[[:space:]]+(all|0\\.0\\.0\\.0/0|::/0);' \"$effective_nginx\"; "
                "ufw_status=$(ufw status verbose); printf '%s' \"$ufw_status\" | grep -F 'Default: deny (incoming), allow (outgoing)' >/dev/null; "
                "ufw_rules=$(ufw status numbered | sed -n 's/^\\[[[:space:]]*[0-9]\\+\\][[:space:]]*//p' "
                "| sed 's/[[:space:]]\\+/ /g'); test \"$(printf '%s\\n' \"$ufw_rules\" | sed '/^$/d' | wc -l)\" = 3; "
                f"printf '%s\\n' \"$ufw_rules\" | grep -Fx '22/tcp ALLOW IN {CONTROL_SSH_SOURCE} # writer-witness-control-ssh' >/dev/null; "
                f"printf '%s\\n' \"$ufw_rules\" | grep -Fx '443/tcp ALLOW IN {WEBAPP_FI} # writer-witness-webapp-fi' >/dev/null; "
                f"printf '%s\\n' \"$ufw_rules\" | grep -Fx '443/tcp ALLOW IN {WEBAPP_IR} # writer-witness-webapp-ir' >/dev/null; "
                f"nftables_attestation=$(nft -j list ruleset | {clean_python_prefix} "
                f"{shlex.quote(expected_python_executable)} {clean_python_flags} "
                "\"$release/scripts/verify_writer_witness_nftables.py\" "
                f"--expected-policy-sha256 {shlex.quote(expected_nftables_policy_sha256)}); "
                "printf '%s' \"$nftables_attestation\" "
                "| grep -F '\"nftables_policy_attested\":\"yes\"' >/dev/null; "
                "inventory=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT datname||':'||oid||':'||datallowconn FROM pg_database WHERE datname='writer_witness' OR datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%' ORDER BY datname\"); "
                "bad=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT count(*) FROM pg_database WHERE datname<>'writer_witness' AND (datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%' OR datname LIKE 'writer_witness_rollback_%') AND datallowconn\"); test \"$bad\" = 0; "
                "orphans=$(runuser -u postgres -- psql -XAt postgres -c \"SELECT count(*) FROM pg_database WHERE datname LIKE 'writer_witness_candidate_%' OR datname LIKE 'writer_witness_failed_%'\"); test \"$orphans\" = 0; "
                "inventory_sha=$(printf '%s' \"$inventory\" | sha256sum | awk '{print $1}'); "
                "nginx_sha=$(nginx -T 2>/dev/null | sha256sum | awk '{print $1}'); "
                "firewall_sha=$(nft list ruleset | sha256sum | awk '{print $1}'); "
                "release_manifest_sha=$(sha256sum /srv/trading-bot-witness/current/release-manifest.json | awk '{print $1}'); "
                "used=$(df -P / | awk 'NR==2 {gsub(/%/,\"\",$5); print $5}'); test \"$used\" -lt 80; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; echo receipts=$receipts; "
                "echo backup=$(basename \"$latest\"); echo backup_sha256=$backup_sha; echo backup_age_seconds=$age; "
                "echo rollback_databases=$rollbacks; echo disk_used_percent=$used; echo rotation_state=absent; "
                "echo restore_state=absent; echo signed_status_fi=200; echo signed_status_ir=200; "
                "echo unsigned_status=401; echo cert_sha256=$cert; echo cert_not_after_epoch=$cert_end; "
                "echo manifest_sha256=$manifest; echo nginx_sha256=$nginx_sha; echo firewall_sha256=$firewall_sha; "
                "echo release_manifest_sha256=$release_manifest_sha; echo installed_helpers_match=yes; echo effective_unit_attested=yes; "
                "echo system_runtime_attested=yes; echo runtime_attested=yes; echo runtime_provenance_attested=yes; echo offsite_upload_attested=yes; echo running_release_match=yes; echo network_policy_semantics_match=yes; "
                f"echo nftables_policy_sha256={expected_nftables_policy_sha256}; "
                "echo database_inventory_sha256=$inventory_sha; echo connection_enabled_aux_databases=0; echo orphan_candidate_failed_databases=0; "
                "echo campaign_state=$campaign_state; echo isolated_pressure_state=absent; "
                "echo credential_bundle_sha256=$credential_bundle_sha",
            ),
            "matrix_witness",
        ),
        CheckSpec(
            "rollback_witness_baseline",
            ssh_command(
                ROLLBACK_WITNESS,
                "set -Eeuo pipefail; "
                "echo role=rollback_witness; "
                "test \"$(timedatectl show -p NTPSynchronized --value)\" = yes; "
                "for u in writer-witness postgresql nginx ufw; do test \"$(systemctl is-active \"$u\")\" = active; done; "
                "curl -fsS http://127.0.0.1:8011/health/ready >/dev/null; "
                "state=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT authority||chr(58)||writer_epoch||chr(58)||lease_status FROM webapp_writer_witness_state;\"); "
                "receipts=$(runuser -u postgres -- psql -XAt -d writer_witness -c "
                "\"SELECT count(*) FROM webapp_writer_witness_receipts;\"); "
                "test \"$state\" = webapp:0:vacant; test \"$receipts\" = 0; "
                + rollback_helper_attestation
                + "; rollback_helper_attested=yes; "
                "manifest=$(/usr/local/sbin/writer-witness-state-manifest); test ${#manifest} -eq 64; "
                "cert=$(openssl x509 -in /etc/trading-bot-witness/tls/server.crt -outform DER "
                "| sha256sum | awk '{print $1}'); test ${#cert} -eq 64; "
                "echo ntp=yes; echo services=active; echo ready=200; echo state=$state; "
                "echo receipts=$receipts; echo manifest_sha256=$manifest; echo cert_sha256=$cert; "
                "echo rollback_helper_attested=yes",
            ),
            "rollback_witness",
        ),
    ]
    if include_source_tests:
        specs.insert(
            1,
            CheckSpec(
                "source_regression_gate",
                ("bash", str(SOURCE_GATE)),
                "control",
            ),
        )
    return specs


def scenario_catalog() -> list[dict[str, object]]:
    return [
        {"id": "RH-001", "name": "concurrent FI and IR acquire", "risk": "dark-witness-write"},
        {"id": "RH-002", "name": "response lost after Witness commit", "risk": "dark-witness-write"},
        {"id": "RH-003", "name": "delayed rejected packet replay", "risk": "dark-witness-write"},
        {"id": "RH-004", "name": "FI to Witness directional partition", "risk": "scoped-network-fault"},
        {"id": "RH-005", "name": "IR to Witness directional partition", "risk": "scoped-network-fault"},
        {"id": "RH-006", "name": "Witness process restart and pause", "risk": "dark-service-fault"},
        {"id": "RH-007", "name": "Witness PostgreSQL pause and restart", "risk": "dark-service-fault"},
        {"id": "RH-008", "name": "Witness VM reboot and temporary host loss", "risk": "dark-host-fault"},
        {"id": "RH-009", "name": "isolated disk-full boundary", "risk": "isolated-filesystem-only"},
        {
            "id": "RH-010",
            "name": "clock skew and real database-clock jump on disposable PostgreSQL",
            "risk": "tmpfs-postgresql-unix-socket-libfaketime-only",
        },
        {"id": "RH-011", "name": "key rotation during one-site partition", "risk": "dark-witness-credential"},
        {"id": "RH-012", "name": "restore exact vacant baseline", "risk": "guarded-dark-restore"},
    ]


def abort_and_rollback_contract() -> dict[str, object]:
    return {
        "abort_conditions": [
            "WebApp-FI app, database, Redis, sync worker, or API loses baseline health",
            "WebApp-IR application or sync worker starts unexpectedly",
            "either WebApp Witness enable flag becomes true",
            "original rollback Witness changes from webapp:0:vacant or gains a receipt",
            "a fault escapes the exact replacement-Witness IP, port, service, or isolated filesystem scope",
            "replacement Witness state differs from the scenario's expected epoch, holder, lease, or receipt count",
            "NTP is unsynchronized before a scenario that does not explicitly test clock behavior",
            "backup checksum, rollback database, SSH access, or cleanup trap is unavailable",
            "Arvan/CDN, public DNS, product database, or product container state changes",
        ],
        "ordered_steps": [
            {
                "order": 1,
                "step_id": "stop_and_join_requesters",
                "requirement": "while isolation remains active, stop every matrix requester and prove no retry process or socket remains",
            },
            {
                "order": 2,
                "step_id": "retain_pre_recovery_evidence",
                "requirement": "capture, redact, hash, and copy scenario evidence to the controller before restoring the database",
            },
            {
                "order": 3,
                "step_id": "recover_active_live_restore",
                "requirement": "reconcile any durable active restore journal by OID before starting or resuming the replacement runtime",
            },
            {
                "order": 4,
                "step_id": "revoke_transient_capability",
                "requirement": "restore every scenario HMAC scope while the Witness service remains stopped, then attest no owned staging or tombstone remains",
            },
            {
                "order": 5,
                "step_id": "resume_paused_runtime",
                "requirement": "CONT/unpause and start only replacement Witness PostgreSQL, service, Nginx, and NTP components affected by the scenario",
            },
            {
                "order": 6,
                "step_id": "remove_isolated_pressure",
                "requirement": "unmount and delete only matrix-owned tmpfs, loop, and clone artifacts",
            },
            {
                "order": 7,
                "step_id": "remove_scoped_network_faults",
                "requirement": "delete only matrix-owned firewall/tc objects after all requesters and capabilities are gone",
            },
            {
                "order": 8,
                "step_id": "restore_vacant_baseline",
                "requirement": "use the journaled live-restore path and the pinned checksumed full-manifest epoch-0 backup on replacement Witness",
            },
            {
                "order": 9,
                "step_id": "verify_complete_baseline",
                "requirement": "prove both Witness manifests, FI health, IR stopped writers, disabled flags, restored paths, no hidden resources, and retained failure status",
            },
        ],
        "success_barrier": [
            "every scenario-specific cleanup passes",
            "the same baseline preflight passes again",
            "replacement Witness is restored to webapp:0:vacant with zero receipts",
            "original Witness remains byte-for-business-state unchanged",
            "no transient credential, network rule, pause, mount, or clock override remains",
        ],
    }


def git_metadata(expected_commit: str | None = None) -> dict[str, object]:
    def output(*command: str) -> str:
        return subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    branch = output("git", "branch", "--show-current")
    head = output("git", "rev-parse", "HEAD")
    pinned = str(expected_commit or head).strip()
    if len(pinned) != 40 or any(char not in "0123456789abcdef" for char in pinned):
        raise ValueError("expected_commit must be one lowercase 40-character Git SHA")
    return {
        "branch": branch,
        "head": head,
        "clean": output("git", "status", "--porcelain") == "",
        "expected_branch": EXPECTED_BRANCH,
        "expected_commit": pinned,
        "exact_commit_matches": head == pinned,
        "main_merge_authorized": False,
        "main_integration_claimed": False,
    }


def source_manifest() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in PINNED_SOURCE_PATHS:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"matrix source artifact is missing: {relative}")
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def witness_release_manifest_sha256() -> str:
    with tempfile.TemporaryDirectory(prefix="writer-witness-matrix-release-") as parent:
        destination = Path(parent) / "release"
        subprocess.check_call(
            ("bash", str(ROOT / "scripts/build_writer_witness_release.sh"), str(destination)),
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return hashlib.sha256((destination / "release-manifest.json").read_bytes()).hexdigest()


def build_plan(
    *,
    include_source_tests: bool = True,
    expected_commit: str | None = None,
    expected_active_campaign_tag: str | None = None,
    expected_active_campaign_scenario: str | None = None,
    expected_active_campaign_not_after: str | None = None,
    allow_expired_active_campaign: bool = False,
) -> dict[str, object]:
    git = git_metadata(expected_commit)
    expected_release_manifest_sha256 = witness_release_manifest_sha256()
    checks = remote_check_specs(
        include_source_tests=include_source_tests,
        expected_commit=str(git["expected_commit"]),
        expected_release_manifest_sha256=expected_release_manifest_sha256,
        expected_active_campaign_tag=expected_active_campaign_tag,
        expected_active_campaign_scenario=expected_active_campaign_scenario,
        expected_active_campaign_not_after=expected_active_campaign_not_after,
        allow_expired_active_campaign=allow_expired_active_campaign,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "scope": "dark_writer_witness_control_plane_only",
        "git": git,
        "run_bundle": {
            "expected_commit": git["expected_commit"],
            "source_sha256": source_manifest(),
            "witness_release_manifest_sha256": expected_release_manifest_sha256,
            "python_runtime": python_runtime_binding(),
            "requirements_lock_sha256": hashlib.sha256(
                (ROOT / "deploy/writer-witness/requirements.lock").read_bytes()
            ).hexdigest(),
            "wheelhouse_manifest_sha256": hashlib.sha256(
                (ROOT / "deploy/writer-witness/wheelhouse.sha256").read_bytes()
            ).hexdigest(),
            "nftables_policy": nftables_policy_binding(),
            "expected_active_campaign_tag": expected_active_campaign_tag,
            "expected_active_campaign_scenario": expected_active_campaign_scenario,
            "expected_active_campaign_not_after": expected_active_campaign_not_after,
            "allow_expired_active_campaign": allow_expired_active_campaign,
            "source_gate_requires_zero_skips": True,
            "source_gate_requires_guarded_postgres_tests": 5,
            "source_gate_requires_four_database_drill": True,
        },
        "hosts": {
            "webapp_fi": {"host": WEBAPP_FI, "production_mutation_allowed": False},
            "webapp_ir": {"host": WEBAPP_IR, "ssh_port": WEBAPP_IR_SSH_PORT, "production_mutation_allowed": False},
            "matrix_witness": {"host": MATRIX_WITNESS, "must_start": "webapp:0:vacant"},
            "rollback_witness": {"host": ROLLBACK_WITNESS, "must_remain_unchanged": True},
        },
        "safety_contract": {
            "allowed_before_matrix": [
                "read-only host and service inspection",
                "read-only TCP reachability probes",
                "source regression tests",
                "local Witness backup and isolated restore-smoke",
            ],
            "forbidden_before_matrix": [
                "merge main into the feature branch",
                "merge the feature branch into main",
                "issue a writer lease",
                "enable Witness flags in a WebApp runtime",
                "start WebApp-IR application writers",
                "stop or restart a production WebApp service",
                "change Arvan or CDN routing",
                "inject firewall, process, database, VM, disk, or clock faults",
                "persist a Witness client credential on a WebApp host",
            ],
            "claim_boundary": (
                "Passing this preflight authorizes only the dark-Witness real-host fault matrix. "
                "It does not prove the feature branch is integrated with current main and does not "
                "authorize production writer activation."
            ),
        },
        "preflight_checks": [
            {
                "check_id": spec.check_id,
                "host_role": spec.host_role,
                "mutates_state": spec.mutates_state,
                "command": list(spec.command),
            }
            for spec in checks
        ],
        "matrix_scenarios_after_preflight": scenario_catalog(),
        "abort_and_rollback": abort_and_rollback_contract(),
        "entry_criteria": [
            "all preflight checks pass on one retained artifact",
            "matrix Witness is vacant at epoch 0 with zero receipts",
            "fresh checksumed backup restore-smoke passes",
            "disabled rollback database exists and remains connection-blocked",
            "both WebApp sites can reach only the dark Witness control endpoint",
            "WebApp-FI production remains healthy and WebApp-IR writers remain stopped",
            "an operator abort and rollback order is recorded before RH-001",
        ],
    }


def bounded_output(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def parse_key_value_output(value: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        key, separator, item = line.partition("=")
        if (
            separator
            and key
            and all(character.isalnum() or character == "_" for character in key)
        ):
            parsed[key] = item.strip()
    return parsed


def execute_preflight(plan: dict[str, object]) -> tuple[dict[str, object], int]:
    git_info = plan["git"]
    if not isinstance(git_info, dict):
        raise RuntimeError("invalid git metadata")
    if (
        git_info.get("branch") != EXPECTED_BRANCH
        or not git_info.get("clean")
        or git_info.get("head") != git_info.get("expected_commit")
    ):
        plan["status"] = "blocked_git_baseline"
        return plan, 2

    bundle = plan.get("run_bundle")
    if not isinstance(bundle, dict) or bundle.get("source_sha256") != source_manifest():
        plan["status"] = "blocked_source_bundle_drift"
        return plan, 2
    expected_release_manifest = witness_release_manifest_sha256()
    if bundle.get("witness_release_manifest_sha256") != expected_release_manifest:
        plan["status"] = "blocked_release_bundle_drift"
        return plan, 2

    results: list[dict[str, object]] = []
    failed: list[str] = []
    for spec in remote_check_specs(
        expected_commit=str(git_info["expected_commit"]),
        expected_release_manifest_sha256=expected_release_manifest,
        expected_active_campaign_tag=(
            str(bundle.get("expected_active_campaign_tag"))
            if bundle.get("expected_active_campaign_tag") is not None
            else None
        ),
        expected_active_campaign_scenario=(
            str(bundle.get("expected_active_campaign_scenario"))
            if bundle.get("expected_active_campaign_scenario") is not None
            else None
        ),
        expected_active_campaign_not_after=(
            str(bundle.get("expected_active_campaign_not_after"))
            if bundle.get("expected_active_campaign_not_after") is not None
            else None
        ),
        allow_expired_active_campaign=(
            bundle.get("allow_expired_active_campaign") is True
        ),
    ):
        completed = subprocess.run(
            spec.command,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        if status == "failed":
            failed.append(spec.check_id)
        results.append(
            {
                "check_id": spec.check_id,
                "host_role": spec.host_role,
                "status": status,
                "return_code": completed.returncode,
                "stdout": bounded_output(completed.stdout),
                "stderr": bounded_output(completed.stderr),
            }
        )
    parsed = {
        result["check_id"]: parse_key_value_output(str(result.get("stdout") or ""))
        for result in results
    }
    matrix_certificate = parsed.get("matrix_witness_dark_baseline", {}).get("cert_sha256")
    for check_id in ("webapp_fi_baseline", "webapp_ir_standby_baseline"):
        if not matrix_certificate or parsed.get(check_id, {}).get("witness_cert_sha256") != matrix_certificate:
            failed.append(f"{check_id}_certificate_identity")
    matrix_manifest = parsed.get("matrix_witness_dark_baseline", {}).get("manifest_sha256")
    rollback_manifest = parsed.get("rollback_witness_baseline", {}).get("manifest_sha256")
    if not matrix_manifest or not rollback_manifest:
        failed.append("witness_baseline_manifest_missing")
    if (
        not expected_release_manifest
        or parsed.get("matrix_witness_dark_baseline", {}).get("release_manifest_sha256")
        != expected_release_manifest
    ):
        failed.append("matrix_witness_release_manifest_mismatch")
    matrix_release = parsed.get("matrix_witness_dark_baseline", {})
    manifest_entries = str(matrix_release.get("release_manifest_entries") or "")
    if (
        matrix_release.get("release_manifest_attested") != "yes"
        or matrix_release.get("release_metadata_attested") != "yes"
        or not manifest_entries.isdigit()
        or int(manifest_entries) < 1
    ):
        failed.append("matrix_witness_release_manifest_attestation_missing")
    expected_matrix_markers = {
        "installed_helpers_match": "yes",
        "effective_unit_attested": "yes",
        "system_runtime_attested": "yes",
        "runtime_attested": "yes",
        "runtime_provenance_attested": "yes",
        "offsite_upload_attested": "yes",
        "running_release_match": "yes",
        "network_policy_semantics_match": "yes",
        "nftables_policy_sha256": str(nftables_policy_binding()["policy_sha256"]),
        "connection_enabled_aux_databases": "0",
        "orphan_candidate_failed_databases": "0",
    }
    for marker, expected in expected_matrix_markers.items():
        if matrix_release.get(marker) != expected:
            failed.append(f"matrix_witness_{marker}_missing_or_drifted")
    source_stdout = str(
        next(
            (item.get("stdout") for item in results if item.get("check_id") == "source_regression_gate"),
            "",
        )
        or ""
    )
    if (
        '"guarded_postgres_tests":5' not in source_stdout
        or '"skipped":0' not in source_stdout
        or '"four_database_drill":true' not in source_stdout
    ):
        failed.append("source_regression_gate_zero_skip_contract")
    failed = list(dict.fromkeys(failed))
    plan["preflight_results"] = results
    plan["observed_baseline"] = parsed
    plan["failed_checks"] = failed
    plan["status"] = "preflight_passed" if not failed else "preflight_failed"
    # The execution authorization window begins only after every expensive
    # source/remote check has completed. generated_at remains build provenance.
    plan["completed_at"] = utc_now()
    return plan, 0 if not failed else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "preflight"), default="plan")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/trading-bot-writer-witness-real-host-matrix/preflight.json"),
    )
    parser.add_argument(
        "--skip-source-tests",
        action="store_true",
        help="Plan-only convenience for unit tests; executed preflight always runs source tests.",
    )
    parser.add_argument(
        "--expected-commit",
        help="Exact 40-character feature-branch commit authorized for this run bundle.",
    )
    parser.add_argument(
        "--expected-active-campaign-tag",
        help="Postflight-only exact remote campaign tag that must remain owned during inspection.",
    )
    parser.add_argument(
        "--expected-active-campaign-scenario",
        help="Postflight-only exact scenario bound to the active remote campaign.",
    )
    parser.add_argument(
        "--expected-active-campaign-not-after",
        help="Postflight-only server-clock expiry bound to the active remote campaign.",
    )
    parser.add_argument(
        "--allow-expired-active-campaign",
        action="store_true",
        help="Cleanup postflight only: prove exact ownership even after server expiry.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "preflight" and args.skip_source_tests:
        raise SystemExit("--skip-source-tests is not allowed in preflight mode")
    if args.mode == "preflight" and not args.expected_commit:
        raise SystemExit("--expected-commit is mandatory in preflight mode")
    plan = build_plan(
        include_source_tests=not args.skip_source_tests,
        expected_commit=args.expected_commit,
        expected_active_campaign_tag=args.expected_active_campaign_tag,
        expected_active_campaign_scenario=args.expected_active_campaign_scenario,
        expected_active_campaign_not_after=args.expected_active_campaign_not_after,
        allow_expired_active_campaign=args.allow_expired_active_campaign,
    )
    exit_code = 0
    if args.mode == "preflight":
        plan, exit_code = execute_preflight(plan)
    else:
        plan["status"] = "planned"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "status": plan["status"],
                "scope": plan["scope"],
                "output": str(args.output),
                "scenario_count": len(plan["matrix_scenarios_after_preflight"]),
                "failed_checks": plan.get("failed_checks", []),
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
