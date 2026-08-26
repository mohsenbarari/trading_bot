#!/usr/bin/env python3
"""Run a disposable Docker/Compose rehearsal for market pipeline Stage 3.

The rehearsal requires a clean Git worktree so the OCI revision label is
truthful.  It uses only generated fixture secrets, loopback listeners, bind
directories beneath a unique temporary root, and removes all containers,
networks, images, and temporary state in ``finally``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.manage_market_pipeline_stage3 import (
    COMPOSE_BASE,
    COMPOSE_ROLE,
    Stage3Error,
    audit_compose,
    image_metadata,
    inventory,
    prepare_path_contract,
)


DOCKERFILE = REPO_ROOT / "deploy" / "market-data" / "Dockerfile"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "market_private_pipeline"
FACT_FIXTURE = FIXTURES / "market_fact_batch.json"
SNAPSHOT_FIXTURE = FIXTURES / "estimator_snapshot.json"


class RehearsalError(RuntimeError):
    pass


def command(
    arguments: Sequence[str],
    *,
    label: str,
    environment: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if environment:
        merged.update(environment)
    result = subprocess.run(
        list(arguments),
        cwd=REPO_ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RehearsalError(f"{label}_failed_rc_{result.returncode}")
    return result


def git_release_sha() -> str:
    status = command(
        ["git", "status", "--porcelain=v1"], label="git_status"
    ).stdout.strip()
    if status:
        raise RehearsalError("git_worktree_must_be_clean")
    value = command(["git", "rev-parse", "HEAD"], label="git_revision").stdout.strip()
    if len(value) != 40:
        raise RehearsalError("git_revision_invalid")
    return value


def git_source_epoch() -> int:
    value = command(
        ["git", "show", "-s", "--format=%ct", "HEAD"],
        label="git_source_epoch",
    ).stdout.strip()
    if not value.isdigit() or int(value) <= 0:
        raise RehearsalError("git_source_epoch_invalid")
    return int(value)


def free_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def write_secret(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fixture_environment(
    root: Path,
    *,
    project: str,
    image: str,
    release_sha: str,
    web_port: int,
    bot_port: int,
) -> dict[str, str]:
    secrets_root = root / "secrets"
    mapping = {
        "MARKET_POSTGRES_PASSWORD_FILE": secrets_root / "postgres-password",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE": secrets_root / "account1-config",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE": secrets_root / "account2-config",
        "MARKET_TRANSPORT_CA_FILE": secrets_root / "transport-ca",
        "MARKET_WEB_TRANSPORT_CERT_FILE": secrets_root / "web-cert",
        "MARKET_WEB_TRANSPORT_KEY_FILE": secrets_root / "web-key",
        "MARKET_BOT_TRANSPORT_CERT_FILE": secrets_root / "bot-cert",
        "MARKET_BOT_TRANSPORT_KEY_FILE": secrets_root / "bot-key",
        "MARKET_HMAC_ACTIVE_FILE": secrets_root / "hmac-active",
        "MARKET_HMAC_PREVIOUS_FILE": secrets_root / "hmac-previous",
    }
    return {
        "MARKET_PIPELINE_PROJECT_NAME": project,
        "MARKET_PIPELINE_IMAGE": image,
        "MARKET_PIPELINE_RELEASE_SHA": release_sha,
        "MARKET_PIPELINE_MODE": "fixture",
        "MARKET_WEB_DATA_ROOT": str(root / "web"),
        "MARKET_BOT_DATA_ROOT": str(root / "bot"),
        "MARKET_PRIVATE_BIND_IP": "127.0.0.1",
        "MARKET_WEB_SNAPSHOT_RECEIVER_PORT": str(web_port),
        "MARKET_BOT_FACT_RECEIVER_PORT": str(bot_port),
        "MARKET_POSTGRES_USER": "market_data",
        "MARKET_POSTGRES_DB": "market_archive",
        **{key: str(value) for key, value in mapping.items()},
    }


def prepare_fixture_root(root: Path) -> None:
    secrets_root = root / "secrets"
    secrets_root.mkdir(mode=0o700)
    write_secret(secrets_root / "postgres-password", secrets.token_hex(24))
    write_secret(secrets_root / "account1-config", "{}")
    write_secret(secrets_root / "account2-config", "{}")
    for name in (
        "transport-ca",
        "web-cert",
        "web-key",
        "bot-cert",
        "bot-key",
        "hmac-active",
        "hmac-previous",
    ):
        write_secret(secrets_root / name, secrets.token_hex(32))
    prepare_path_contract(root / "web", "web")
    prepare_path_contract(root / "bot", "bot")


def compose(role: str, project: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE_BASE),
        "-f",
        str(COMPOSE_ROLE[role]),
        "--profile",
        role,
    ]


def build_image(
    tag: str,
    release_sha: str,
    source_epoch: int,
    version: str,
    *,
    no_cache: bool,
) -> float:
    arguments = [
        "docker",
        "build",
        "--file",
        str(DOCKERFILE),
        "--tag",
        tag,
        "--build-arg",
        f"SOURCE_SHA={release_sha}",
        "--build-arg",
        f"IMAGE_VERSION={version}",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={source_epoch}",
    ]
    if no_cache:
        arguments.append("--no-cache")
    arguments.append(".")
    started = time.monotonic()
    command(arguments, label=f"build_{version}")
    return time.monotonic() - started


def image_id(tag: str) -> str:
    return command(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        label="inspect_image_id",
    ).stdout.strip()


def render(role: str, project: str, environment: Mapping[str, str]) -> dict[str, Any]:
    output = command(
        [*compose(role, project), "config", "--format", "json"],
        label=f"render_{role}",
        environment=environment,
    ).stdout
    document = json.loads(output)
    audit_compose(document, role=role, fixture=True)
    return document


def wait_healthy(
    role: str,
    project: str,
    services: Sequence[str],
    environment: Mapping[str, str],
    timeout_seconds: float = 90.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ready = True
        for service in services:
            container_id = command(
                [*compose(role, project), "ps", "-q", service],
                label="compose_ps",
                environment=environment,
            ).stdout.strip()
            if not container_id:
                ready = False
                break
            status = command(
                [
                    "docker",
                    "inspect",
                    container_id,
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                ],
                label="container_health",
            ).stdout.strip()
            if status != "healthy":
                ready = False
                break
        if ready:
            return
        time.sleep(0.5)
    raise RehearsalError(f"{role}_health_timeout")


def post_json(port: int, path: str, document: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(document, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())
    except URLError as exc:
        raise RehearsalError("fixture_receiver_unreachable") from exc


def database_table_count(project: str, environment: Mapping[str, str]) -> int:
    output = command(
        [
            *compose("web", project),
            "exec",
            "-T",
            "market-database",
            "psql",
            "-U",
            "market_data",
            "-d",
            "market_archive",
            "-Atc",
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='market_data'",
        ],
        label="database_table_count",
        environment=environment,
    ).stdout.strip()
    return int(output)


def inspect_running_image(
    role: str,
    project: str,
    service: str,
    environment: Mapping[str, str],
) -> str:
    container_id = command(
        [*compose(role, project), "ps", "-q", service],
        label="compose_running_id",
        environment=environment,
    ).stdout.strip()
    return command(
        ["docker", "inspect", container_id, "--format", "{{.Image}}"],
        label="running_image",
    ).stdout.strip()


def verify_second_owner_fails(
    image: str,
    release_sha: str,
    root: Path,
) -> bool:
    result = command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=8m,mode=1777,noexec,nosuid,nodev",
            "--mount",
            "type=bind,"
            f"source={root / 'web/state/market-capture-account1'},"
            "target=/var/lib/market-data/state",
            "--mount",
            "type=bind,"
            f"source={root / 'web/sessions/account1'},"
            "target=/var/lib/market-data/session",
            "--env",
            "MARKET_PIPELINE_MODE=fixture",
            "--env",
            f"MARKET_PIPELINE_RELEASE_SHA={release_sha}",
            "--env",
            "MARKET_PIPELINE_STATE_ROOT=/var/lib/market-data/state",
            "--env",
            "MARKET_PIPELINE_SESSION_ROOT=/var/lib/market-data/session",
            image,
            "service",
            "--role",
            "market-capture-account1",
        ],
        label="second_owner_probe",
        check=False,
    )
    return result.returncode == 78 and "role_owner_lock_already_held" in result.stderr


def verify_market_store(path: Path, release_sha: str) -> bool:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT release_sha FROM stage3_foundation_state WHERE singleton=1"
        ).fetchone()
        return row is not None and row[0] == release_sha
    finally:
        connection.close()


def run_rehearsal() -> dict[str, Any]:
    release_sha = git_release_sha()
    source_epoch = git_source_epoch()
    temporary = Path(tempfile.mkdtemp(prefix="market-stage3-"))
    project = f"market-stage3-{os.getpid()}"
    candidate = f"market-pipeline-stage3:{os.getpid()}-candidate"
    repeated = f"market-pipeline-stage3:{os.getpid()}-repeat"
    rollback = f"market-pipeline-stage3:{os.getpid()}-rollback"
    images = [candidate, repeated, rollback]
    cleanup = {
        "containers_removed": False,
        "networks_removed": False,
        "images_removed": False,
        "temporary_root_removed": False,
    }
    result: dict[str, Any] = {}
    environment: dict[str, str] = {}
    try:
        prepare_fixture_root(temporary)
        web_port = free_port()
        bot_port = free_port()
        while bot_port == web_port:
            bot_port = free_port()

        first_build = build_image(
            candidate,
            release_sha,
            source_epoch,
            "stage3-candidate",
            no_cache=True,
        )
        repeat_build = build_image(
            repeated,
            release_sha,
            source_epoch,
            "stage3-candidate",
            no_cache=True,
        )
        rollback_build = build_image(
            rollback,
            release_sha,
            source_epoch,
            "stage3-rollback-fixture",
            no_cache=False,
        )
        candidate_id = image_id(candidate)
        repeated_id = image_id(repeated)
        rollback_id = image_id(rollback)
        if candidate_id != repeated_id:
            raise RehearsalError("same_source_image_not_reproducible")
        if rollback_id == candidate_id:
            raise RehearsalError("rollback_fixture_image_not_distinct")

        metadata = image_metadata(candidate, release_sha, fixture=True)
        environment = fixture_environment(
            temporary,
            project=project,
            image=candidate,
            release_sha=release_sha,
            web_port=web_port,
            bot_port=bot_port,
        )
        web_config = render("web", project, environment)
        bot_config = render("bot", project, environment)
        web_inventory = inventory(web_config, role="web", image=metadata)
        bot_inventory = inventory(bot_config, role="bot", image=metadata)

        command(
            [*compose("web", project), "up", "-d"],
            label="compose_web_up",
            environment=environment,
        )
        command(
            [*compose("bot", project), "up", "-d"],
            label="compose_bot_up",
            environment=environment,
        )
        web_services = sorted(EXPECTED_RUNTIME_WEB)
        bot_services = sorted(EXPECTED_RUNTIME_BOT)
        wait_healthy("web", project, web_services, environment)
        wait_healthy("bot", project, bot_services, environment)

        table_count = database_table_count(project, environment)
        if table_count != 22:
            raise RehearsalError("market_schema_table_count_mismatch")
        second_migration = command(
            [*compose("web", project), "run", "--rm", "market-migration"],
            label="second_migration",
            environment=environment,
        ).stdout
        if '"status":"already_current"' not in second_migration:
            raise RehearsalError("migration_second_pass_not_idempotent")

        fact = json.loads(FACT_FIXTURE.read_text(encoding="utf-8"))
        status, first_ack = post_json(bot_port, "/fixture/market-facts", fact)
        if status != 200 or first_ack.get("accepted_count") != 1:
            raise RehearsalError("fixture_fact_first_ack_failed")
        status, replay_ack = post_json(bot_port, "/fixture/market-facts", fact)
        if status != 200 or replay_ack.get("duplicate_count") != 1:
            raise RehearsalError("fixture_fact_replay_not_idempotent")
        snapshot = json.loads(SNAPSHOT_FIXTURE.read_text(encoding="utf-8"))
        status, snapshot_ack = post_json(
            web_port, "/fixture/estimator-snapshot", snapshot
        )
        if status != 200 or snapshot_ack.get("status") != "ACK":
            raise RehearsalError("fixture_snapshot_ack_failed")

        market_store = temporary / "bot/market-store/market-store.sqlite"
        if not verify_market_store(market_store, release_sha):
            raise RehearsalError("market_store_fixture_missing")
        if not verify_second_owner_fails(candidate, release_sha, temporary):
            raise RehearsalError("capture_second_owner_did_not_fail_closed")

        command(
            [
                *compose("bot", project),
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "market-store-adapter",
            ],
            label="recreate_store_adapter",
            environment=environment,
        )
        wait_healthy("bot", project, ["market-store-adapter"], environment)
        if not verify_market_store(market_store, release_sha):
            raise RehearsalError("market_store_not_persistent_after_recreate")

        rollback_environment = dict(environment)
        rollback_environment["MARKET_PIPELINE_IMAGE"] = rollback
        for role in ("web", "bot"):
            command(
                [*compose(role, project), "up", "-d", "--force-recreate"],
                label=f"rollback_{role}",
                environment=rollback_environment,
            )
        wait_healthy("web", project, web_services, rollback_environment)
        wait_healthy("bot", project, bot_services, rollback_environment)
        if inspect_running_image(
            "bot", project, "market-fact-receiver", rollback_environment
        ) != rollback_id:
            raise RehearsalError("rollback_image_not_active")
        if database_table_count(project, rollback_environment) != 22:
            raise RehearsalError("rollback_schema_compatibility_failed")
        if not verify_market_store(market_store, release_sha):
            raise RehearsalError("rollback_lost_market_store")

        result = {
            "status": "pass",
            "release_sha": release_sha,
            "image": {
                "candidate_id": candidate_id,
                "rollback_fixture_id": rollback_id,
                "same_source_reproducible": True,
                "source_date_epoch": source_epoch,
                "first_build_seconds": round(first_build, 3),
                "repeat_build_seconds": round(repeat_build, 3),
                "rollback_build_seconds": round(rollback_build, 3),
            },
            "compose": {
                "web_services": len(web_inventory["services"]),
                "bot_services": len(bot_inventory["services"]),
                "private_receiver_count": 2,
                "unexpected_published_ports": 0,
                "nonroot_readonly_services": True,
            },
            "migration": {
                "table_count": table_count,
                "second_pass_noop": True,
                "product_database_touched": False,
            },
            "fixture_transport": {
                "fact_first_accepted": first_ack["accepted_count"],
                "fact_replay_duplicates": replay_ack["duplicate_count"],
                "snapshot_ack": snapshot_ack["status"],
            },
            "persistence": {
                "market_store_survived_recreate": True,
                "capture_second_owner_failed_closed": True,
                "rollback_preserved_schema_and_state": True,
            },
        }
    finally:
        for role in ("bot", "web"):
            if environment:
                command(
                    [*compose(role, project), "down", "--remove-orphans"],
                    label=f"cleanup_{role}",
                    environment=environment,
                    check=False,
                )
        remaining = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            label="cleanup_container_check",
            check=False,
        ).stdout.strip()
        cleanup["containers_removed"] = not remaining
        networks = command(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            label="cleanup_network_check",
            check=False,
        ).stdout.strip()
        cleanup["networks_removed"] = not networks
        for image in images:
            command(
                ["docker", "image", "rm", "--force", image],
                label="cleanup_image",
                check=False,
            )
        cleanup["images_removed"] = all(
            command(
                ["docker", "image", "inspect", image],
                label="cleanup_image_check",
                check=False,
            ).returncode
            != 0
            for image in images
        )
        if temporary.name.startswith("market-stage3-") and temporary.parent == Path("/tmp"):
            shutil.rmtree(temporary)
        cleanup["temporary_root_removed"] = not temporary.exists()
    result["cleanup"] = cleanup
    if not all(cleanup.values()):
        raise RehearsalError("stage3_rehearsal_cleanup_incomplete")
    return result


EXPECTED_RUNTIME_WEB = {
    "market-capture-account1",
    "market-capture-account2",
    "market-processor",
    "market-fact-sync-worker",
    "estimator-snapshot-receiver",
}
EXPECTED_RUNTIME_BOT = {
    "market-fact-receiver",
    "market-store-adapter",
    "coin-estimator",
    "estimator-snapshot-sender",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        result = run_rehearsal()
    except (OSError, ValueError, json.JSONDecodeError, RehearsalError, Stage3Error) as exc:
        print(json.dumps({"status": "fail", "reason_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
