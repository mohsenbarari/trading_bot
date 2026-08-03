#!/usr/bin/env python3
"""Prepare and apply one evidence-bound staging Bot token runtime amendment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
import urllib.request
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.verify_three_site_staging_image_inventory import verify_image_document
from scripts.verify_three_site_staging_role_bundle import _verify_bundle_source


DOCKER = "/usr/bin/docker"
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
TOKEN_RE = re.compile(r"[0-9]{6,12}:[A-Za-z0-9_-]{30,}")
SHA40_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class BotMaterialAmendmentError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _secure_env(path: Path, *, label: str) -> tuple[bytes, dict[str, str]]:
    raw = _verify_bundle_source(path, expected_mode=0o600)
    try:
        values = parse_env_values(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BotMaterialAmendmentError(f"{label} is invalid") from exc
    if not values:
        raise BotMaterialAmendmentError(f"{label} is empty")
    return raw, values


def _identity(campaign_id: str, release_sha: str, plan_sha256: str) -> None:
    try:
        UUID(campaign_id)
    except ValueError as exc:
        raise BotMaterialAmendmentError("campaign ID is invalid") from exc
    if SHA40_RE.fullmatch(release_sha) is None or SHA256_RE.fullmatch(plan_sha256) is None:
        raise BotMaterialAmendmentError("release or migration plan identity is invalid")


def _telegram_identity(token: str, *, api_base: str) -> dict[str, str]:
    try:
        with urllib.request.urlopen(
            f"{api_base.rstrip('/')}/bot{token}/getMe", timeout=15
        ) as response:
            raw = response.read(64 * 1024)
            status = int(response.status)
        payload = json.loads(raw)
    except Exception as exc:
        raise BotMaterialAmendmentError("dedicated staging token getMe failed closed") from None
    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        status != 200
        or payload.get("ok") is not True
        or not isinstance(result, dict)
        or type(result.get("id")) is not int
        or result.get("is_bot") is not True
        or not isinstance(result.get("username"), str)
        or not result["username"]
    ):
        raise BotMaterialAmendmentError("dedicated staging token identity is invalid")
    identity = {"id": result["id"], "username": result["username"]}
    return {
        "status": "passed",
        "identity_sha256": _canonical_sha(identity),
        "observed_at": _now(),
    }


def prepare(
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    base_env: Path,
    source_env: Path,
    forbidden_envs: list[Path],
    runtime_env_output: Path,
    evidence_output: Path,
    telegram_api_base: str,
) -> dict[str, Any]:
    _identity(campaign_id, release_sha, plan_sha256)
    if runtime_env_output.exists() or evidence_output.exists():
        raise BotMaterialAmendmentError("amendment output already exists")
    base_raw, base = _secure_env(base_env, label="approved base role env")
    _source_raw, source = _secure_env(source_env, label="dedicated staging token source")
    old_token = base.get("BOT_TOKEN", "")
    token = source.get("BOT_TOKEN", "")
    if TOKEN_RE.fullmatch(token) is None:
        raise BotMaterialAmendmentError("dedicated staging token has an invalid format")
    if token == old_token:
        raise BotMaterialAmendmentError("dedicated staging token does not amend the base env")
    for forbidden_path in forbidden_envs:
        _raw, forbidden = _secure_env(forbidden_path, label="forbidden token source")
        if token == forbidden.get("BOT_TOKEN"):
            raise BotMaterialAmendmentError("dedicated staging token matches a forbidden source")
    runtime = dict(base)
    runtime["BOT_TOKEN"] = token
    # Preserve the base file's key order while replacing exactly one value.
    lines = []
    replaced = False
    for line in base_raw.decode("utf-8").splitlines():
        if line and not line.startswith("#") and line.partition("=")[0] == "BOT_TOKEN":
            lines.append(f"BOT_TOKEN={token}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        raise BotMaterialAmendmentError("approved base role env lacks BOT_TOKEN")
    runtime_raw_bytes = ("\n".join(lines) + "\n").encode()
    if parse_env_values(runtime_raw_bytes.decode()) != runtime:
        raise BotMaterialAmendmentError("runtime amendment changed an unexpected env value")
    telegram = _telegram_identity(token, api_base=telegram_api_base)
    evidence = {
        "schema": "three-site-staging-bot-token-amendment-v1",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "role": "bot_fi",
        "base_role_env_sha256": _sha(base_raw),
        "runtime_role_env_sha256": _sha(runtime_raw_bytes),
        "amended_keys": ["BOT_TOKEN"],
        "token_sha256": _sha(token.encode()),
        "token_format": "telegram-bot-token-v1",
        "telegram_get_me": telegram,
        "authorization_basis": "owner-issued-dedicated-staging-token-under-approved-stage4",
        "created_at": _now(),
    }
    write_secure_atomic_bytes(runtime_env_output, runtime_raw_bytes, mode=0o600)
    write_secure_atomic_bytes(
        evidence_output,
        (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return {
        "status": "prepared",
        "runtime_env_sha256": evidence["runtime_role_env_sha256"],
        "evidence_sha256": _canonical_sha(evidence),
        "token_sha256": evidence["token_sha256"],
        "telegram_identity_sha256": telegram["identity_sha256"],
    }


def verify_amendment(
    *,
    evidence_path: Path,
    base_env: Path,
    runtime_env: Path,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> dict[str, Any]:
    _identity(campaign_id, release_sha, plan_sha256)
    base_raw, base = _secure_env(base_env, label="approved base role env")
    runtime_raw, runtime = _secure_env(runtime_env, label="amended runtime role env")
    try:
        evidence_raw = read_secure_bytes(
            evidence_path, label="Bot token amendment evidence", max_size=1024 * 1024
        )
        evidence = json.loads(evidence_raw)
    except Exception as exc:
        raise BotMaterialAmendmentError("Bot token amendment evidence is invalid") from exc
    fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "role",
        "base_role_env_sha256", "runtime_role_env_sha256", "amended_keys",
        "token_sha256", "token_format", "telegram_get_me",
        "authorization_basis", "created_at",
    }
    telegram = evidence.get("telegram_get_me") if isinstance(evidence, dict) else None
    if (
        set(evidence) != fields
        or evidence.get("schema") != "three-site-staging-bot-token-amendment-v1"
        or evidence.get("campaign_id") != campaign_id
        or evidence.get("release_sha") != release_sha
        or evidence.get("plan_sha256") != plan_sha256
        or evidence.get("role") != "bot_fi"
        or evidence.get("base_role_env_sha256") != _sha(base_raw)
        or evidence.get("runtime_role_env_sha256") != _sha(runtime_raw)
        or evidence.get("amended_keys") != ["BOT_TOKEN"]
        or evidence.get("token_format") != "telegram-bot-token-v1"
        or evidence.get("authorization_basis")
        != "owner-issued-dedicated-staging-token-under-approved-stage4"
        or not isinstance(telegram, dict)
        or set(telegram) != {"status", "identity_sha256", "observed_at"}
        or telegram.get("status") != "passed"
        or SHA256_RE.fullmatch(str(telegram.get("identity_sha256", ""))) is None
    ):
        raise BotMaterialAmendmentError("Bot token amendment identity is invalid")
    differences = {key for key in set(base) | set(runtime) if base.get(key) != runtime.get(key)}
    token = runtime.get("BOT_TOKEN", "")
    if (
        differences != {"BOT_TOKEN"}
        or TOKEN_RE.fullmatch(token) is None
        or evidence.get("token_sha256") != _sha(token.encode())
    ):
        raise BotMaterialAmendmentError("Bot token amendment scope or fingerprint is invalid")
    return {
        "evidence": evidence,
        "evidence_file_sha256": _sha(evidence_raw),
        "evidence_canonical_sha256": _canonical_sha(evidence),
        "runtime_env_sha256": _sha(runtime_raw),
        "token_sha256": evidence["token_sha256"],
        "telegram_identity_sha256": telegram["identity_sha256"],
    }


def _run(arguments: list[str], *, timeout: int = 180, include_stderr: bool = False) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BotMaterialAmendmentError("Bot material command unavailable") from exc
    if result.returncode != 0:
        raise BotMaterialAmendmentError(
            f"Bot material command failed closed: {Path(arguments[0]).name}"
        )
    return ((result.stdout or "") + (result.stderr or "") if include_stderr else result.stdout).strip()


def _compose(role_compose: Path, runtime_env: Path) -> list[str]:
    return [DOCKER, "compose", "-f", str(role_compose), "--env-file", str(runtime_env)]


def apply(
    *,
    evidence_path: Path,
    base_env: Path,
    runtime_env: Path,
    role_compose: Path,
    image_inventory: Path,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    receipt_output: Path,
    do_apply: bool,
    confirm: str | None,
) -> dict[str, Any]:
    verified = verify_amendment(
        evidence_path=evidence_path,
        base_env=base_env,
        runtime_env=runtime_env,
        campaign_id=campaign_id,
        release_sha=release_sha,
        plan_sha256=plan_sha256,
    )
    compose_raw = _verify_bundle_source(role_compose, expected_mode=0o640)
    image_raw = read_secure_bytes(
        image_inventory, label="Bot-FI image inventory", max_size=4 * 1024 * 1024
    )
    image_document = json.loads(image_raw)
    image_result = verify_image_document(
        image_document,
        role="bot-fi",
        campaign_id=campaign_id,
        release_sha=release_sha,
        role_compose_sha256=_sha(compose_raw),
        role_env_sha256=verified["evidence"]["base_role_env_sha256"],
    )
    prefix = _compose(role_compose, runtime_env)
    resolved = json.loads(_run([*prefix, "config", "--format", "json"]))
    service = (resolved.get("services") or {}).get("bot_fi_bot")
    reference = service.get("image") if isinstance(service, dict) else None
    expected_image_id = image_result["image_ids"].get(reference)
    if not expected_image_id:
        raise BotMaterialAmendmentError("Bot service image is not in approved inventory")
    required = (
        f"amend-staging-bot-token:{campaign_id}:{plan_sha256}:"
        f"{verified['runtime_env_sha256']}"
    )
    if not do_apply:
        return {
            "status": "planned",
            "required_confirmation": required,
            "runtime_env_sha256": verified["runtime_env_sha256"],
            "evidence_sha256": verified["evidence_canonical_sha256"],
        }
    if confirm != required:
        raise BotMaterialAmendmentError("Bot material amendment confirmation mismatch")
    if receipt_output.exists():
        raise BotMaterialAmendmentError("Bot material amendment receipt already exists")
    started_at = _now()
    mutated = False
    try:
        _run([*prefix, "up", "-d", "--no-deps", "--force-recreate", "bot_fi_bot"])
        mutated = True
        stable_since: float | None = None
        deadline = time.monotonic() + 45
        container = ""
        while time.monotonic() < deadline:
            container = _run([*prefix, "ps", "-q", "bot_fi_bot"])
            if not container:
                stable_since = None
                time.sleep(1)
                continue
            state = json.loads(
                _run([DOCKER, "inspect", "--format", "{{json .State}}", container])
            )
            if state.get("Running") is not True or int(state.get("Restarting") is True):
                stable_since = None
                time.sleep(1)
                continue
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 15:
                break
            time.sleep(1)
        if not container or stable_since is None or time.monotonic() - stable_since < 15:
            raise BotMaterialAmendmentError("Bot container did not become stable")
        image_id = _run([DOCKER, "inspect", "--format", "{{.Image}}", container])
        restart_count = int(
            _run([DOCKER, "inspect", "--format", "{{.RestartCount}}", container])
        )
        release_and_token = _run(
            [
                *prefix, "exec", "-T", "bot_fi_bot", "python", "-c",
                "import hashlib,os; print(os.environ.get('RELEASE_SHA','')); "
                "print(hashlib.sha256(os.environ.get('BOT_TOKEN','').encode()).hexdigest())",
            ]
        ).splitlines()
        logs = _run(
            [DOCKER, "logs", "--since", "2m", "--timestamps", container],
            timeout=30,
            include_stderr=True,
        )
        lowered = logs.lower()
        if (
            image_id != expected_image_id
            or restart_count != 0
            or release_and_token != [release_sha, verified["token_sha256"]]
            or "traceback (most recent call last)" in lowered
            or "tokenvalidationerror" in lowered
            or "telegramconflicterror" in lowered
            or "[error]" in lowered
        ):
            raise BotMaterialAmendmentError("Bot runtime amendment did not become stable")
    except Exception:
        if mutated:
            try:
                _run([*prefix, "stop", "--timeout", "20", "bot_fi_bot"])
            except Exception:
                pass
        raise
    receipt = {
        "schema": "three-site-staging-bot-token-amendment-receipt-v1",
        "status": "passed",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "role": "bot_fi",
        "service": "bot_fi_bot",
        "container_id": container,
        "image_id": image_id,
        "runtime_role_env_sha256": verified["runtime_env_sha256"],
        "amendment_evidence_sha256": verified["evidence_file_sha256"],
        "token_sha256": verified["token_sha256"],
        "restart_count": restart_count,
        "started_at": started_at,
        "observed_at": _now(),
    }
    write_secure_atomic_bytes(
        receipt_output,
        (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(),
        mode=0o600,
    )
    return {
        "status": "passed",
        "service": "bot_fi_bot",
        "runtime_env_sha256": verified["runtime_env_sha256"],
        "receipt_sha256": _canonical_sha(receipt),
        "restart_count": restart_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--campaign-id", required=True)
    common.add_argument("--release-sha", required=True)
    common.add_argument("--plan-sha256", required=True)
    common.add_argument("--base-env", type=Path, required=True)
    prepare_parser = sub.add_parser("prepare", parents=[common])
    prepare_parser.add_argument("--source-env", type=Path, required=True)
    prepare_parser.add_argument("--forbidden-env", action="append", type=Path, default=[])
    prepare_parser.add_argument("--runtime-env-output", type=Path, required=True)
    prepare_parser.add_argument("--evidence-output", type=Path, required=True)
    prepare_parser.add_argument("--telegram-api-base", default="https://api.telegram.org")
    apply_parser = sub.add_parser("apply", parents=[common])
    apply_parser.add_argument("--runtime-env", type=Path, required=True)
    apply_parser.add_argument("--evidence", type=Path, required=True)
    apply_parser.add_argument("--role-compose", type=Path, required=True)
    apply_parser.add_argument("--image-inventory", type=Path, required=True)
    apply_parser.add_argument("--receipt-output", type=Path, required=True)
    apply_parser.add_argument("--apply", action="store_true")
    apply_parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare(
                campaign_id=args.campaign_id,
                release_sha=args.release_sha,
                plan_sha256=args.plan_sha256,
                base_env=args.base_env,
                source_env=args.source_env,
                forbidden_envs=args.forbidden_env,
                runtime_env_output=args.runtime_env_output,
                evidence_output=args.evidence_output,
                telegram_api_base=args.telegram_api_base,
            )
        else:
            result = apply(
                evidence_path=args.evidence,
                base_env=args.base_env,
                runtime_env=args.runtime_env,
                role_compose=args.role_compose,
                image_inventory=args.image_inventory,
                campaign_id=args.campaign_id,
                release_sha=args.release_sha,
                plan_sha256=args.plan_sha256,
                receipt_output=args.receipt_output,
                do_apply=args.apply,
                confirm=args.confirm,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
