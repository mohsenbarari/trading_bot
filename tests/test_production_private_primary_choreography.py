from __future__ import annotations

from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from scripts import run_production_private_primary_choreography as controller


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "scripts/production_deploy_online.sh"
REAL_ROLE_ENV_ASSERT = controller._assert_role_env_bindings


@pytest.fixture(autouse=True)
def _unit_release_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unit plans use the intentionally dirty shared development checkout.
    # The exact-clean-origin contract has its own real temporary-Git test
    # below; all other tests stay focused on their independent invariant.
    monkeypatch.setattr(
        controller, "_assert_exact_release_checkout", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        controller, "_assert_role_env_bindings", lambda *_a, **_k: None
    )


def _git_identity() -> tuple[str, str]:
    head = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    return head, tree


def _arguments(
    root: Path,
    tool: str,
    action: str,
    role: str | None,
    release_sha: str,
    release_tree: str,
) -> list[str]:
    values: list[str] = []
    if action != "execute":
        values.append(action)
    if role is not None:
        option = "--host-role" if tool == "quiesce_production_legacy_market_collectors.py" else "--role"
        values.extend([option, role])
    if tool in {
        "upgrade_market_pipeline_bluegreen.py",
        "audit_production_market_catchup.py",
        "observe_production_private_primary.py",
        "quiesce_production_legacy_market_collectors.py",
    }:
        values.extend(["--release-sha", release_sha])
    elif tool == "run_release_bound_product_readiness.py":
        values.extend(
            [
                "--release-sha",
                release_sha,
                "--release-tree",
                release_tree,
                "--control-root",
                str(
                    root / "control-releases" / release_sha
                    if role == "bot"
                    else Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}"
                    )
                ),
                "--expected-control-manifest-sha256",
                "CONTROL_MANIFEST_PLACEHOLDER",
                "--container",
                "trading_bot_bot" if role == "bot" else "trading_bot_app",
                "--project",
                "trading_bot" if role == "bot" else "current",
                "--expected-image-id",
                "sha256:" + ("b" if role == "bot" else "c") * 64,
                "--expected-snapshot-sha256",
                "a" * 64,
                "--confirm",
                "run-release-bound-product-readiness",
            ]
        )
    elif tool in {
        "reconcile_estimator_snapshot_publication_outbox.py",
        "promote_production_private_primary_product.py",
    }:
        values.extend(["--expected-release-sha", release_sha, "--expected-release-tree", release_tree])
    elif tool == "verify_production_private_primary_promotion.py":
        values.extend(["--release-sha", release_sha, "--release-tree", release_tree])
    if tool == "backup_market_pipeline_archive.py":
        values.extend(
            [
                "--env-file",
                str(
                    Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                    )
                ),
                "--backup-dir",
                str(root / "web-backup"),
                "--receipt",
                str(root / "web-backup" / "market-pipeline-backup-receipt.json"),
            ]
        )
    if tool == "crypt_market_pipeline_backup.py":
        if action == "encrypt":
            values.extend(
                [
                    "--source",
                    str(root / "web-backup" / "planned.dump"),
                    "--destination",
                    str(root / "web-backup" / "planned.dump.enc"),
                    "--receipt",
                    str(root / "web-backup" / "planned.dump.encryption.json"),
                ]
            )
        else:
            values.extend(
                [
                    "--artifact",
                    str(root / "bot-backup" / "planned.dump.enc"),
                    "--receipt",
                    str(root / "bot-backup" / "planned.dump.encryption.json"),
                ]
            )
    if tool == "migrate_market_pipeline_archive.py":
        values.extend(
            [
                "--env-file",
                str(
                    Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                    )
                ),
                "--backup-env-file",
                str(
                    Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                    )
                ),
                "--release-sha",
                release_sha,
                "--release-tree",
                release_tree,
                "--backup-receipt",
                str(root / "web-backup" / "market-pipeline-backup-receipt.json"),
                "--offhost-receipt-sha256",
                "a" * 64,
                "--host-preflight-receipt-sha256",
                "b" * 64,
            ]
        )
    if tool == "rollout_market_pipeline_shadow.py":
        values.extend(
            [
                "--release-sha",
                release_sha,
                "--feed-mode",
                "PRIVATE_PRIMARY",
                "--journal",
                str(root / f"{role}-base-services.json"),
                "--env-file",
                str(
                    root / "control-releases" / release_sha / "bot-new.env"
                    if role == "bot"
                    else Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                    )
                ),
            ]
        )
    if tool == "upgrade_market_pipeline_bluegreen.py":
        values.extend(["--journal", str(root / f"{role}-bluegreen.json")])
        if action == "plan":
            values.extend(
                [
                    "--new-env",
                    str(
                        root / "control-releases" / release_sha / "bot-new.env"
                        if role == "bot"
                        else Path(
                            f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                        )
                    ),
                    "--old-env",
                    str(
                        root / "bot-old.env"
                        if role == "bot"
                        else Path("/srv/trading-bot/runtime/web-old.env")
                    ),
                ]
            )
        if action == "quiesce-database":
            values.extend(
                [
                    "--backup-receipt",
                    str(root / "web-backup" / "market-pipeline-backup-receipt.json"),
                    "--expected-backup-receipt-sha256",
                    "c" * 64,
                    "--offhost-backup-receipt",
                    str(root / "web-backup" / "offhost-copy-receipt.json"),
                    "--expected-offhost-backup-receipt-sha256",
                    "d" * 64,
                ]
            )
        if action == "authorize-captures":
            values.extend(
                [
                    "--bot-legacy-collector-receipt",
                    str(root / "web-bot-legacy-mirror.json"),
                ]
            )
    if tool == "quiesce_production_legacy_market_collectors.py":
        values.extend(["--journal", str(root / f"{role}-legacy.json")])
        if action == "commit":
            values.extend(
                [
                    "--primary-verification",
                    str(
                        root
                        / (
                            "web-promotion-verification.json"
                            if role == "web"
                            else "promotion-verification.json"
                        )
                    ),
                ]
            )
    if tool == "verify_production_private_primary_promotion.py":
        values.extend(
            [
                "--receipt",
                str(root / "promotion-verification.json"),
                "--bot-env",
                str(root / "control-releases" / release_sha / "bot-new.env"),
            ]
        )
    if tool == "audit_production_market_catchup.py" and action == "web":
        values.extend(
            [
                "--runtime-env",
                str(
                    Path(
                        f"/srv/trading-bot/market-pipeline-releases/{release_sha}/web-new.env"
                    )
                ),
            ]
        )
    if tool == "promote_production_private_primary_product.py":
        values.extend(
            [
                "--web-maintenance-journal",
                str(root / "web-maintenance-mirror.json"),
                "--release-checkout",
                str(REPO_ROOT),
                "--transaction-id",
                "product-cutover-12345678",
            ]
        )
    return values


def _command(
    root: Path,
    signature: tuple[str, str, str | None],
    release_sha: str,
    release_tree: str,
) -> dict[str, object]:
    tool, action, role = signature
    host = "web" if role == "web" else "local"
    if tool == "backup_market_pipeline_archive.py" or (
        tool == "crypt_market_pipeline_backup.py" and action == "encrypt"
    ) or tool == "migrate_market_pipeline_archive.py":
        host = "web"
    if tool == "audit_production_market_catchup.py" and action == "web":
        host = "web"
    if tool == "observe_production_private_primary.py" and role == "web":
        host = "web"
    if tool == "check_production_coin_inference_readiness.py" and role == "web":
        host = "web"
    if tool == "reconcile_estimator_snapshot_publication_outbox.py":
        host = "web"
    if (
        tool == "quiesce_production_legacy_market_collectors.py"
        and action in {"quiesce", "verify", "commit"}
        and role == "web"
    ):
        host = "web"
    return {
        "host": host,
        "remote_release_root": f"/srv/trading-bot/market-pipeline-releases/{release_sha}",
        "tool": tool,
        "arguments": _arguments(
            root, tool, action, role, release_sha, release_tree
        ),
    }


def _control_release_fixture(
    root: Path, release_sha: str, release_tree: str
) -> tuple[Path, str]:
    release = root / "control-releases" / release_sha
    scripts = release / "scripts"
    scripts.mkdir(parents=True, mode=0o700, exist_ok=True)
    release.chmod(0o700)
    entries: list[tuple[str, str]] = []
    relative_files = {
        *(f"scripts/{name}" for name in controller.KNOWN_COMMANDS),
        "scripts/run_production_private_primary_choreography.py",
        "scripts/production_deploy_online.sh",
        "scripts/capture_production_baseline.py",
        "scripts/deploy_config.py",
        "scripts/plan_telegram_delivery_queue_production.py",
        "scripts/run_production_backup.py",
        "scripts/scan_telegram_queue_artifacts.py",
        "scripts/cutover_telegram_delivery_queue_production.py",
        "scripts/run_fenced_production_deploy.py",
        "scripts/update_production_coin_inference_source.py",
        "scripts/build_production_private_primary_choreography_plan.py",
        "scripts/prepare_production_private_primary_manifest.py",
        "scripts/check_production_coin_inference_readiness.py",
        "core/__init__.py",
        "core/telegram_delivery_cutover_contract.py",
    }
    relative_files.update(
        subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "ls-files", "core/market_intelligence"],
            text=True,
        ).splitlines()
    )
    for relative in sorted(relative_files):
        source = REPO_ROOT / relative
        destination = release / relative
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o644)
        entries.append((relative, sha256(destination.read_bytes()).hexdigest()))
    manifest = release / controller.CONTROL_PAYLOAD_MANIFEST
    manifest.write_text(
        "".join(f"{digest}  ./{relative}\n" for relative, digest in entries),
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    pair = release / controller.CONTROL_RELEASE_PAIR_RECEIPT
    pair.write_text(
        json.dumps(
            {
                "schema": "market_pipeline_release_pair/1.0",
                "release_sha": release_sha,
                "release_tree": release_tree,
                "secrets_disclosed": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    pair.chmod(0o600)
    return release, sha256(manifest.read_bytes()).hexdigest()


def _plan(root: Path) -> tuple[Path, Path, dict[str, object]]:
    release_sha, release_tree = _git_identity()
    local_control_root, control_manifest_sha256 = _control_release_fixture(
        root, release_sha, release_tree
    )
    remote_control_root = Path(
        f"/srv/trading-bot/market-pipeline-releases/{release_sha}"
    )
    for directory in (root / "web-backup", root / "bot-backup"):
        directory.mkdir(mode=0o700, exist_ok=True)
    bot_new_env = local_control_root / "bot-new.env"
    bot_old_env = root / "bot-old.env"
    for path, payload in (
        (bot_new_env, b"ROLE=bot-new\n"),
        (bot_old_env, b"ROLE=bot-old\n"),
    ):
        path.write_bytes(payload)
        path.chmod(0o600)
    web_new_env = remote_control_root / "web-new.env"
    web_old_env = Path("/srv/trading-bot/runtime/web-old.env")
    source = root / "runtime-source.env"
    source.write_text(
        "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    deployment_manifest = root / "production-deploy.env"
    deployment_manifest.write_text(
        "IRAN_SSH_AUTH_METHOD=key\n", encoding="utf-8"
    )
    deployment_manifest.chmod(0o600)
    phases: list[dict[str, object]] = []
    for phase in controller.PHASES:
        commands: list[dict[str, object]] = []
        for signature in controller.REQUIRED_COMMAND_SEQUENCES[phase]:
            commands.append(
                _command(root, signature, release_sha, release_tree)
            )
        if phase == "base_services_start":
            services = iter(
                (
                    "estimator-snapshot-receiver",
                    "market-fact-receiver",
                    "market-processor",
                    "market-fact-sync-worker",
                    "market-store-adapter",
                    "coin-estimator",
                    "estimator-snapshot-sender",
                    "estimator-snapshot-receiver",
                )
            )
            for command in commands:
                if controller._signature(command)[1] == "start":
                    command["arguments"].extend(["--service", next(services)])
        recovery: list[dict[str, object]] = []
        rollback: list[dict[str, object]] = []
        evidence = [
            {
                "host": "local",
                "path": str(root / f"{phase}.json"),
                "schema": f"test_{phase}/1.0",
                "statuses": ["PASS"],
            }
        ]
        if phase == "promotion_verification":
            evidence = [{**evidence[0], "schema": "production_private_primary_promotion_verification/1.0"}]
        elif phase == "product_promotion":
            evidence = [
                {**evidence[0], "schema": "production_private_primary_product_promotion/1.0"},
                {
                    "host": "local",
                    "path": str(root / "postdeploy.json"),
                    "schema": "production_private_primary_product_postdeploy_verification/1.0",
                    "statuses": ["PASS"],
                },
            ]
        phases.append(
            {
                "id": phase,
                "commands": commands,
                "recovery_commands": recovery,
                "rollback_commands": rollback,
                "evidence": evidence,
            }
        )
    document: dict[str, object] = {
        "schema": controller.PLAN_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "approved_release_ref": controller.APPROVED_RELEASE_REF,
        "source_manifest": str(source),
        "deployment_manifest": str(deployment_manifest),
        "expected_source_sha256": sha256(source.read_bytes()).hexdigest(),
        "controller_lock": str(root / controller.CONTROLLER_LOCK_NAME),
        "local_control_release_root": str(local_control_root),
        "remote_control_release_root": str(remote_control_root),
        "control_payload_manifest_sha256": control_manifest_sha256,
        "product_image_ids": {
            "bot": "sha256:" + "b" * 64,
            "web": "sha256:" + "c" * 64,
        },
        "role_env_bindings": {
            "bot": {
                "new_path": str(bot_new_env),
                "new_sha256": sha256(bot_new_env.read_bytes()).hexdigest(),
                "old_path": str(bot_old_env),
                "old_sha256": sha256(bot_old_env.read_bytes()).hexdigest(),
            },
            "web": {
                "new_path": str(web_new_env),
                "new_sha256": "d" * 64,
                "old_path": str(web_old_env),
                "old_sha256": "e" * 64,
            },
        },
        "web_ssh_argv": ["/usr/bin/ssh", "root@web.example"],
        "product_authority_initial": "LEGACY",
        "product_authority_final": "PRIVATE_PRIMARY",
        "transaction_id": "product-cutover-12345678",
        "builder_tool": "scripts/build_production_private_primary_choreography_plan.py",
        "legacy_collectors_restart_forbidden": True,
        "product_promotion_last": True,
        "secrets_disclosed": False,
        "phases": phases,
    }
    for phase in phases:
        if phase["id"] != "nine_source_evidence":
            continue
        for command in phase["commands"]:
            command["arguments"] = controller._set_option(
                command["arguments"],
                "--expected-control-manifest-sha256",
                control_manifest_sha256,
            )
    plan = root / "plan.json"
    plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    plan.chmod(0o600)
    inputs_root = root / "plan-inputs"
    inputs_root.mkdir(mode=0o700, exist_ok=True)

    def _bound_bytes(path: Path, payload: bytes) -> str:
        path.write_bytes(payload)
        path.chmod(0o600)
        return sha256(payload).hexdigest()

    web_env = inputs_root / "web.env"
    web_old_input = inputs_root / "web-old.env"
    control_manifest = local_control_root / controller.CONTROL_PAYLOAD_MANIFEST
    control_pair = local_control_root / controller.CONTROL_RELEASE_PAIR_RECEIPT
    builder_relative = "scripts/build_production_private_primary_choreography_plan.py"
    builder_script_sha256 = sha256(
        (local_control_root / builder_relative).read_bytes()
    ).hexdigest()
    input_paths = {
        "runtime_source": source,
        "deployment_manifest": deployment_manifest,
        "control_manifest": control_manifest,
        "control_pair_receipt": control_pair,
        "primary_pair_receipt": inputs_root / "primary-pair.json",
        "market_image_receipt": inputs_root / "market-image.json",
        "preflight_receipt": inputs_root / "preflight.json",
        "web_env": web_env,
        "bot_env": bot_new_env,
        "web_old_env": web_old_input,
        "bot_old_env": bot_old_env,
        "product_bot_image_receipt": inputs_root / "product-bot.json",
        "product_web_image_receipt": inputs_root / "product-web.json",
        "private_manifest": inputs_root / "private.env",
        "private_manifest_receipt": inputs_root / "private-receipt.json",
    }
    input_digests = {
        "runtime_source": sha256(source.read_bytes()).hexdigest(),
        "deployment_manifest": sha256(deployment_manifest.read_bytes()).hexdigest(),
        "control_manifest": control_manifest_sha256,
        "control_pair_receipt": sha256(control_pair.read_bytes()).hexdigest(),
        "bot_env": sha256(bot_new_env.read_bytes()).hexdigest(),
        "bot_old_env": sha256(bot_old_env.read_bytes()).hexdigest(),
        "primary_pair_receipt": _bound_bytes(
            input_paths["primary_pair_receipt"], b'{"schema":"test-primary-pair"}\n'
        ),
        "market_image_receipt": _bound_bytes(
            input_paths["market_image_receipt"], b'{"schema":"test-market-image"}\n'
        ),
        "preflight_receipt": _bound_bytes(
            input_paths["preflight_receipt"], b'{"schema":"test-preflight"}\n'
        ),
        "web_env": _bound_bytes(web_env, b"ROLE=web-new\n"),
        "web_old_env": _bound_bytes(web_old_input, b"ROLE=web-old\n"),
        "product_bot_image_receipt": _bound_bytes(
            input_paths["product_bot_image_receipt"], b'{"schema":"test-bot-image"}\n'
        ),
        "product_web_image_receipt": _bound_bytes(
            input_paths["product_web_image_receipt"], b'{"schema":"test-web-image"}\n'
        ),
        "private_manifest": _bound_bytes(
            input_paths["private_manifest"], b"PRIVATE=1\n"
        ),
        "private_manifest_receipt": _bound_bytes(
            input_paths["private_manifest_receipt"], b'{"schema":"test-private"}\n'
        ),
    }
    required_inputs = set(input_paths)
    build_receipt = root / "plan-build.json"
    ssh_digest = sha256(
        b"\0".join(value.encode("utf-8") for value in document["web_ssh_argv"])
        + b"\0"
    ).hexdigest()
    build_receipt.write_text(
        json.dumps(
            {
                "schema": controller.PLAN_BUILD_RECEIPT_SCHEMA,
                "status": "PASS",
                "release_sha": release_sha,
                "release_tree": release_tree,
                "approved_release_ref": controller.APPROVED_RELEASE_REF,
                "transaction_id": "product-cutover-12345678",
                "builder_tool": builder_relative,
                "builder_script_sha256": builder_script_sha256,
                "plan_sha256": sha256(plan.read_bytes()).hexdigest(),
                "phase_count": len(controller.PHASES),
                "command_count": sum(
                    len(phase["commands"]) for phase in phases
                ),
                "plan_output_path_sha256": sha256(
                    str(plan).encode("utf-8")
                ).hexdigest(),
                "receipt_output_path_sha256": sha256(
                    str(build_receipt).encode("utf-8")
                ).hexdigest(),
                "required_input_labels": sorted(required_inputs),
                "input_sha256": input_digests,
                "input_paths": {
                    label: str(path) for label, path in sorted(input_paths.items())
                },
                "path_sha256": {
                    "local_control_release_root": sha256(
                        str(local_control_root).encode("utf-8")
                    ).hexdigest(),
                    "remote_control_release_root": sha256(
                        str(remote_control_root).encode("utf-8")
                    ).hexdigest(),
                    "release_checkout": sha256(
                        str(REPO_ROOT).encode("utf-8")
                    ).hexdigest(),
                },
                "web_ssh_argv_sha256": ssh_digest,
                "product_image_ids": document["product_image_ids"],
                "secret_values_included": False,
                "live_state_inspected": False,
                "git_inspected": False,
                "recovery_commands_embedded": False,
                "rollback_commands_embedded": False,
                "secrets_disclosed": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    build_receipt.chmod(0o600)
    return plan, source, document


def _refresh_build_receipt(root: Path, plan: Path) -> None:
    receipt_path = root / "plan-build.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    document = json.loads(plan.read_text(encoding="utf-8"))
    receipt["plan_sha256"] = sha256(plan.read_bytes()).hexdigest()
    receipt["command_count"] = sum(
        len(phase["commands"])
        for phase in document["phases"]
        if isinstance(phase, dict) and isinstance(phase.get("commands"), list)
    )
    receipt["product_image_ids"] = document["product_image_ids"]
    receipt["transaction_id"] = document.get(
        "transaction_id", receipt.get("transaction_id")
    )
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
    )
    receipt_path.chmod(0o600)


def _args(root: Path, plan: Path) -> SimpleNamespace:
    document = json.loads(plan.read_text(encoding="utf-8"))
    ssh_argv = document["web_ssh_argv"]
    return SimpleNamespace(
        command="execute",
        plan=str(plan),
        expected_plan_sha256=sha256(plan.read_bytes()).hexdigest(),
        plan_build_receipt=str(root / "plan-build.json"),
        expected_plan_build_receipt_sha256=sha256(
            (root / "plan-build.json").read_bytes()
        ).hexdigest(),
        release_root=str(REPO_ROOT),
        expected_source_manifest=document["source_manifest"],
        expected_deployment_manifest=document["deployment_manifest"],
        expected_deployment_manifest_sha256=sha256(
            Path(document["deployment_manifest"]).read_bytes()
        ).hexdigest(),
        expected_web_ssh_argv_sha256=sha256(
            b"\0".join(value.encode("utf-8") for value in ssh_argv) + b"\0"
        ).hexdigest(),
        expected_local_control_release_root=document["local_control_release_root"],
        expected_remote_control_release_root=document["remote_control_release_root"],
        expected_control_payload_manifest_sha256=document[
            "control_payload_manifest_sha256"
        ],
        journal=str(root / "journal.json"),
        receipt=str(root / "receipt.json"),
        confirm=controller.CONFIRMATION,
    )


def test_validate_binds_exact_release_legacy_source_and_fixed_phase_order() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-controller-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, _document = _plan(root)
        result = controller.validate(_args(root, plan))
        assert result["status"] == "PLAN_PASS"
        assert result["phase_count"] == len(controller.PHASES)
        assert result["product_authority_initial"] == "LEGACY"
        assert result["runtime_or_database_mutated"] is False


def test_exact_release_checkout_rejects_dirty_or_unapproved_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Restore the real implementation hidden by the unit autouse fixture.
    monkeypatch.undo()
    with tempfile.TemporaryDirectory(prefix="private-primary-exact-git-") as temporary:
        root = Path(temporary)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        tracked = root / "tracked.txt"
        tracked.write_text("exact\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "commit", "-qm", "exact"], check=True
        )
        release_sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        release_tree = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "update-ref",
                controller.APPROVED_RELEASE_REF,
                release_sha,
            ],
            check=True,
        )
        controller._assert_exact_release_checkout(
            root,
            release_sha=release_sha,
            release_tree=release_tree,
            approved_release_ref=controller.APPROVED_RELEASE_REF,
        )
        (root / "untracked.txt").write_text("drift\n", encoding="utf-8")
        with pytest.raises(
            controller.ChoreographyError,
            match="release_checkout_not_exact_clean_approved",
        ):
            controller._assert_exact_release_checkout(
                root,
                release_sha=release_sha,
                release_tree=release_tree,
                approved_release_ref=controller.APPROVED_RELEASE_REF,
            )


def test_validate_rejects_missing_phase_command_and_product_source_tamper() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-controller-tamper-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, source, document = _plan(root)
        document["phases"][0]["commands"].pop()
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_phase_commands_incomplete",
        ):
            controller.validate(_args(root, plan))


def test_plan_rejects_duplicate_binding_and_product_image_tamper() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-binding-tamper-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        readiness = next(
            phase
            for phase in document["phases"]
            if phase["id"] == "nine_source_evidence"
        )["commands"][0]
        readiness["arguments"].extend(
            ["--expected-image-id", "sha256:" + "d" * 64]
        )
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError, match="plan_argument_duplicate"
        ):
            controller.validate(_args(root, plan))


def test_role_env_bytes_are_rechecked_on_both_hosts_and_tamper_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-role-env-") as temporary:
        root = Path(temporary)
        bot_new = root / "bot-new.env"
        bot_old = root / "bot-old.env"
        bot_new.write_bytes(b"ROLE=bot-new\n")
        bot_old.write_bytes(b"ROLE=bot-old\n")
        bot_new.chmod(0o600)
        bot_old.chmod(0o600)
        web_new = Path("/srv/release/web-new.env")
        web_old = Path("/srv/runtime/web-old.env")
        remote_payloads = {
            web_new: b"ROLE=web-new\n",
            web_old: b"ROLE=web-old\n",
        }
        bindings = {
            "bot": {
                "new_path": str(bot_new),
                "new_sha256": sha256(bot_new.read_bytes()).hexdigest(),
                "old_path": str(bot_old),
                "old_sha256": sha256(bot_old.read_bytes()).hexdigest(),
            },
            "web": {
                "new_path": str(web_new),
                "new_sha256": sha256(remote_payloads[web_new]).hexdigest(),
                "old_path": str(web_old),
                "old_sha256": sha256(remote_payloads[web_old]).hexdigest(),
            },
        }
        monkeypatch.setattr(
            controller,
            "_remote_read",
            lambda path, _ssh: remote_payloads[path],
        )
        REAL_ROLE_ENV_ASSERT(bindings, ssh_argv=["/usr/bin/ssh", "web"])
        remote_payloads[web_new] = b"ROLE=tampered\n"
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_role_env_digest_mismatch",
        ):
            REAL_ROLE_ENV_ASSERT(bindings, ssh_argv=["/usr/bin/ssh", "web"])

        plan, _source, document = _plan(root)
        document["product_image_ids"]["web"] = "sha256:" + "d" * 64
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_product_readiness_wrapper_invalid",
        ):
            controller.validate(_args(root, plan))

        plan, _source, document = _plan(root)
        product = next(
            phase
            for phase in document["phases"]
            if phase["id"] == "product_promotion"
        )["commands"][0]
        index = product["arguments"].index("--release-checkout")
        product["arguments"][index + 1] = str(root / "decoy-checkout")
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_product_release_checkout_invalid",
        ):
            controller.validate(_args(root, plan))


def test_plan_rejects_backup_before_workload_quiesce_and_same_host_copy() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-order-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        document["phases"][0], document["phases"][1] = (
            document["phases"][1],
            document["phases"][0],
        )
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError, match="plan_phase_order_invalid"
        ):
            controller.validate(_args(root, plan))


def test_official_source_ssh_and_late_phase_hosts_are_not_plan_selectable() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-official-binding-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        args = _args(root, plan)
        args.expected_source_manifest = str(root / "decoy.env")
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_(official_invocation_binding|plan_build_receipt)_invalid",
        ):
            controller.validate(args)
        args = _args(root, plan)
        args.expected_web_ssh_argv_sha256 = "f" * 64
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_(official_invocation_binding|plan_build_receipt)_invalid",
        ):
            controller.validate(args)

        catchup = next(
            item for item in document["phases"] if item["id"] == "catchup_audit"
        )
        catchup["commands"][0]["host"] = "local"
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_phase_host_topology_invalid",
        ):
            controller.validate(_args(root, plan))

        plan, _source, document = _plan(root)
        backup = document["phases"][1]
        backup["commands"][3]["host"] = "web"
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_phase_host_topology_invalid",
        ):
            controller.validate(_args(root, plan))


def test_plan_binds_receiver_first_cross_host_settle_and_no_bot_capture_start() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-receiver-first-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        controller.validate(_args(root, plan))
        base = next(
            item for item in document["phases"] if item["id"] == "base_services_start"
        )
        observed = [
            (
                controller._signature(command),
                controller._option(command["arguments"], "--service"),
            )
            for command in base["commands"]
        ]
        assert observed[0][0] == (
            "rollout_market_pipeline_shadow.py",
            "prepare",
            "web",
        )
        assert [observed[1][1], observed[3][1]] == [
            "estimator-snapshot-receiver", "market-fact-receiver"
        ]
        assert [row[1] for row in observed[4:6]] == [
            "market-processor", "market-fact-sync-worker"
        ]
        assert [row[1] for row in observed[6:9]] == [
            "market-store-adapter",
            "coin-estimator",
            "estimator-snapshot-sender",
        ]
        assert observed[-2][1] == "estimator-snapshot-receiver"
        activate = next(
            item for item in document["phases"] if item["id"] == "bluegreen_activate"
        )
        assert (
            "upgrade_market_pipeline_bluegreen.py",
            "start-captures",
            "bot",
        ) not in [controller._signature(command) for command in activate["commands"]]

        base["commands"][-2]["arguments"][-1] = "market-processor"
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_receiver_first_order_invalid",
        ):
            controller.validate(_args(root, plan))

        plan, source, document = _plan(root)
        source.write_text(
            "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY\n",
            encoding="utf-8",
        )
        document["expected_source_sha256"] = sha256(source.read_bytes()).hexdigest()
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        with pytest.raises(
            controller.ChoreographyError,
                match="controller_plan_build_receipt_invalid",
        ):
            controller.validate(_args(root, plan))


def test_sigkill_like_interruption_resumes_phase_before_product_last(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-controller-resume-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, source, document = _plan(root)
        args = _args(root, plan)
        calls: list[tuple[str, tuple[str, str, str | None]]] = []
        failed = False

        def run(command: dict[str, object], **_kwargs: object) -> None:
            nonlocal failed
            tool = str(command["tool"])
            phase = next(
                item["id"]
                for item in document["phases"]
                if command in item["commands"] or command in item["recovery_commands"]
            )
            calls.append((str(phase), controller._signature(command)))
            if (
                phase == "bluegreen_workload_quiesce"
                and len(
                    [row for row in calls if row[0] == "bluegreen_workload_quiesce"]
                )
                == 3
                and not failed
            ):
                failed = True
                raise controller.ChoreographyError("controller_command_failed")
            if (
                phase == "product_promotion"
                and tool == "promote_production_private_primary_product.py"
            ):
                source.write_text(
                    "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR={controller.BOT_SNAPSHOT_ROOT}\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR={controller.BOT_SNAPSHOT_ROOT}\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR={controller.WEB_SNAPSHOT_ROOT}\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH={controller.CONTAINER_SNAPSHOT}\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_BOT_PRIVATE_PRIMARY_SNAPSHOT_PATH={controller.CONTAINER_SNAPSHOT}\n"
                    f"PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH={controller.CONTAINER_SNAPSHOT}\n",
                    encoding="utf-8",
                )
            return {"status": "PASS", "release_sha": document["release_sha"]}

        def evidence(phase: dict[str, object], **_kwargs: object) -> list[dict[str, str]]:
            if phase["id"] == "promotion_verification":
                return [{"schema": "production_private_primary_promotion_verification/1.0", "status": "PASS", "sha256": "a" * 64}]
            if phase["id"] == "product_promotion":
                return [
                    {"schema": "production_private_primary_product_promotion/1.0", "status": "PASS", "sha256": "b" * 64},
                    {"schema": "production_private_primary_product_postdeploy_verification/1.0", "status": "PASS", "sha256": "c" * 64},
                ]
            return [{"schema": "test/1.0", "status": "PASS", "sha256": "d" * 64}]

        monkeypatch.setattr(controller, "_run_command", run)
        monkeypatch.setattr(controller, "_arm_zero_owner_watchdog", lambda **_k: None)
        monkeypatch.setattr(controller, "_assert_zero_owner_watchdog", lambda *_a, **_k: None)
        monkeypatch.setattr(controller, "_phase_evidence", evidence)
        monkeypatch.setattr(
            controller,
            "_materialize_command",
            lambda command, **_kwargs: dict(command),
        )
        monkeypatch.setattr(
            controller,
            "_record_dynamic_context",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            controller,
            "_assert_product_runtime",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_command_failed",
        ):
            controller.execute(args)
        interrupted = json.loads(Path(args.journal).read_text(encoding="utf-8"))
        assert interrupted["active_phase"] == "bluegreen_workload_quiesce"
        assert interrupted["next_phase_index"] == 0

        args.command = "recover"
        args.confirm = controller.RECOVERY_CONFIRMATION
        result = controller.execute(args)
        assert result["status"] == "PASS"
        assert result["completed_phases"] == list(controller.PHASES)
        assert result["completed_phases"][-1] == "product_promotion"
        web_plan = (
            "bluegreen_workload_quiesce",
            ("upgrade_market_pipeline_bluegreen.py", "plan", "web"),
        )
        bot_plan = (
            "bluegreen_workload_quiesce",
            ("upgrade_market_pipeline_bluegreen.py", "plan", "bot"),
        )
        web_quiesce = (
            "bluegreen_workload_quiesce",
            ("upgrade_market_pipeline_bluegreen.py", "quiesce-workload", "web"),
        )
        assert calls.count(web_plan) == 1
        assert calls.count(bot_plan) == 1
        assert calls.count(web_quiesce) == 2


def test_terminal_pass_recovery_revalidates_live_product_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-terminal-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        args = _args(root, plan)
        args.command = "recover"
        args.confirm = controller.RECOVERY_CONFIRMATION
        journal = controller._initial_journal(
            plan_sha256=args.expected_plan_sha256,
            release_sha=str(document["release_sha"]),
            release_tree=str(document["release_tree"]),
            source_sha256=str(document["expected_source_sha256"]),
        )
        journal.update(
            {
                "status": "PASS",
                "next_phase_index": len(controller.PHASES),
                "completed": [
                    {"phase": phase, "commands": [], "evidence": []}
                    for phase in controller.PHASES
                ],
                "pipeline_forward_only": True,
                "authority_forward_only": True,
                "primary_commit_forward_only": True,
                "product_transaction_started": True,
                "source_sha256_after": "d" * 64,
            }
        )
        controller._write_atomic(Path(args.journal), journal, exclusive=True)
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            controller,
            "_assert_product_source",
            lambda *_args, **_kwargs: "d" * 64,
        )
        monkeypatch.setattr(
            controller, "_assert_product_runtime", lambda **_kwargs: None
        )
        monkeypatch.setattr(
            controller,
            "_materialize_command",
            lambda command, **_kwargs: dict(command),
        )
        monkeypatch.setattr(
            controller,
            "_materialize_product_recovery",
            lambda command: {**command, "terminal_recovery": True},
        )

        def run(command: dict[str, object], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            return {"status": "PASS"}

        monkeypatch.setattr(controller, "_run_command", run)
        monkeypatch.setattr(controller, "_arm_zero_owner_watchdog", lambda **_k: None)
        monkeypatch.setattr(controller, "_assert_zero_owner_watchdog", lambda *_a, **_k: None)
        result = controller.execute(args)
        assert result["status"] == "PASS"
        assert len(calls) == 1
        assert calls[0]["terminal_recovery"] is True


def test_official_shell_exposes_only_explicit_validate_and_execute_actions() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    assert "validate-private-primary-release" in source
    assert "private-primary-release" in source
    assert "recover-private-primary-release" in source
    assert "prepare-private-primary-control-release" in source
    assert "run_prepare_private_primary_control_release" in source
    assert "--private-primary-choreography-recovery-strategy resume|rollback" in source
    assert "run_private_primary_choreography_controller validate" in source
    assert "run_private_primary_choreography_controller execute" in source
    assert "run_private_primary_choreography_controller recover" in source
    assert "--recovery-strategy \"$PRODUCTION_PRIVATE_PRIMARY_CHOREOGRAPHY_RECOVERY_STRATEGY\"" in source
    assert "rollback-production-private-primary-choreography" in source
    function = source.split("run_private_primary_choreography_controller() {", 1)[1].split("\n}", 1)[0]
    assert "run-production-private-primary-choreography" in function
    assert "controller=\"$LOCAL_MARKET_PIPELINE_CONTROL_RELEASE_DIR/scripts/run_production_private_primary_choreography.py\"" in function
    assert "run_exact_control_release_python" in function
    assert "build_official_private_primary_choreography_plan" in function
    assert "verify_official_private_primary_plan_build_receipt" in function
    assert '"${controller_web_ssh_argv[0]}" == "/usr/bin/ssh"' in function
    assert "PATH-selected SSH executable" in function
    assert "python3 \"$controller\"" not in function
    assert "eval" not in function
    builder = source.split("build_official_private_primary_choreography_plan() {", 1)[1].split("\n}", 1)[0]
    assert "scripts/build_production_private_primary_choreography_plan.py" in builder
    assert "build-production-private-primary-choreography-plan" in builder
    exact = source.split("run_exact_control_release_python() {", 1)[1].split("\n}", 1)[0]
    assert "/proc/self/fd/" in exact
    assert "O_NOFOLLOW" in exact
    assert "PATH=/usr/bin:/bin" in exact
    assert "0o022" in exact
    assert "exact_control_release_tool_invalid" in exact
    assert "set_inheritable" in exact


def test_control_release_manifest_accepts_primary_pair_schema(
    tmp_path: Path,
) -> None:
    head, tree = _git_identity()
    release, manifest_sha = _control_release_fixture(tmp_path, head, tree)
    pair = release / controller.CONTROL_RELEASE_PAIR_RECEIPT
    pair.write_text(
        json.dumps(
            {
                "schema": "market_pipeline_primary_release_pair/1.1",
                "release_sha": head,
                "release_tree": tree,
                "feed_mode": "PRIVATE_PRIMARY",
                "product_authority_changed": False,
                "secrets_disclosed": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    pair.chmod(0o600)
    entries = controller._control_release_manifest(
        release,
        expected_manifest_sha256=manifest_sha,
        release_sha=head,
        release_tree=tree,
    )
    assert "scripts/run_production_private_primary_choreography.py" in entries

    payload = json.loads(pair.read_text(encoding="utf-8"))
    payload["feed_mode"] = "PRIVATE_SHADOW"
    pair.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    pair.chmod(0o600)
    with pytest.raises(
        controller.ChoreographyError, match="control_release_pair_binding_invalid"
    ):
        controller._control_release_manifest(
            release,
            expected_manifest_sha256=manifest_sha,
            release_sha=head,
            release_tree=tree,
        )


def test_exact_control_release_python_inherits_verified_tool_fd(
    tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    tool = scripts / "probe_exact_tool.py"
    tool.write_text(
        "import sys\nprint('exact-fd-ok', sys.argv[1])\n",
        encoding="utf-8",
    )
    os.chmod(tool, 0o444)
    digest = sha256(tool.read_bytes()).hexdigest()
    manifest = root / "control-payload.sha256"
    manifest.write_text(f"{digest}  ./scripts/probe_exact_tool.py\n", encoding="utf-8")
    os.chmod(manifest, 0o600)
    ready = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\nrun_exact_control_release_python "$2" scripts/probe_exact_tool.py token-one\n',
            "exact-fd-test",
            str(DEPLOY),
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert ready.returncode == 0, ready.stderr + ready.stdout
    assert "exact-fd-ok token-one" in ready.stdout

    os.chmod(tool, 0o664)
    blocked = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\nrun_exact_control_release_python "$2" scripts/probe_exact_tool.py token-one\n',
            "exact-fd-test",
            str(DEPLOY),
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode != 0
    assert "exact_control_release_tool_invalid" in blocked.stderr


def test_readiness_result_requires_fresh_fourteen_rate_nine_source_proof() -> None:
    command = {
        "host": "local",
        "tool": "check_production_coin_inference_readiness.py",
        "arguments": ["private-primary-consumer", "--role", "bot"],
    }
    result = {
        "status": "READY",
        "authority": "PRIVATE_PRIMARY",
        "rate_cell_count": 14,
        "required_source_input_trace_count": 9,
        "source_input_trace_sha256": "a" * 64,
        "snapshot_age_seconds": 1,
        "secrets_disclosed": False,
    }
    controller._validate_command_result(command, result)
    for field, value in (
        ("snapshot_age_seconds", -0.01),
        ("snapshot_age_seconds", 120.01),
        ("rate_cell_count", 13),
        ("required_source_input_trace_count", 8),
    ):
        invalid = {**result, field: value}
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_private_primary_readiness_invalid",
        ):
            controller._validate_command_result(command, invalid)


def test_product_runtime_probe_is_self_contained_and_checks_three_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_sha, release_tree = _git_identity()
    images = {
        "bot": "sha256:" + "b" * 64,
        "web": "sha256:" + "c" * 64,
    }

    def inspect(
        mode: str, project: str, image_id: str
    ) -> subprocess.CompletedProcess[bytes]:
        payload = [
            {
                "State": {"Running": True},
                "Image": image_id,
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": project,
                        "org.opencontainers.image.revision": release_sha,
                        "io.gold-trade.release.tree": release_tree,
                    },
                    "Env": [f"PRODUCT_ESTIMATOR_SNAPSHOT_MODE={mode}"],
                },
            }
        ]
        return subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(payload).encode(), stderr=b""
        )

    observed = iter(
        (
            inspect("LEGACY", "trading_bot", images["bot"]),
            inspect("LEGACY", "trading_bot", images["bot"]),
            inspect("LEGACY", "current", images["web"]),
        )
    )
    monkeypatch.setattr(controller.subprocess, "run", lambda *_a, **_k: next(observed))
    controller._assert_product_runtime(
        ssh_argv=["ssh", "root@web.example"], expected_mode="LEGACY",
        expected_image_ids=images, release_sha=release_sha,
        release_tree=release_tree,
    )

    observed = iter(
        (
            inspect("LEGACY", "trading_bot", images["bot"]),
            inspect("PRIVATE_PRIMARY", "trading_bot", images["bot"]),
            inspect("LEGACY", "current", images["web"]),
        )
    )
    monkeypatch.setattr(controller.subprocess, "run", lambda *_a, **_k: next(observed))
    with pytest.raises(
        controller.ChoreographyError,
        match="controller_product_runtime_mode_invalid",
    ):
        controller._assert_product_runtime(
            ssh_argv=["ssh", "root@web.example"], expected_mode="LEGACY",
            expected_image_ids=images, release_sha=release_sha,
            release_tree=release_tree,
        )


def test_migration_result_requires_real_second_pass_noop() -> None:
    command = {
        "host": "web",
        "tool": "migrate_market_pipeline_archive.py",
        "arguments": [],
    }
    result = {
        "schema": "market_pipeline_migration_receipt/1.0",
        "status": "PASS",
        "second_pass": {"status": "already_current", "version": 3, "table_count": 28},
        "product_authority_changed": False,
        "telegram_capture_cutover_authorized": False,
        "secrets_disclosed": False,
    }
    controller._validate_command_result(command, result)
    for second in (
        {"status": "applied", "version": 3, "table_count": 28},
        {"status": "already_current", "version": 2, "table_count": 28},
    ):
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_migration_second_pass_not_noop",
        ):
            controller._validate_command_result(command, {**result, "second_pass": second})


def test_remote_evidence_mirror_is_exact_idempotent_and_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-mirror-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        source = root / "source.json"
        destination = root / "destination.json"
        source.write_text('{"status":"PASS"}\n', encoding="utf-8")
        source.chmod(0o600)
        fake_ssh = root / "ssh.py"
        fake_ssh.write_text(
            "import shlex,subprocess,sys\n"
            "p=subprocess.run(shlex.split(sys.argv[-1]),input=sys.stdin.buffer.read(),stdout=subprocess.PIPE)\n"
            "sys.stdout.buffer.write(p.stdout)\n"
            "raise SystemExit(p.returncode)\n",
            encoding="utf-8",
        )
        expected = sha256(source.read_bytes()).hexdigest()
        ssh = [sys.executable, str(fake_ssh)]
        monkeypatch.setattr(controller, "SSH_BINARY", sys.executable)
        assert controller._mirror_remote_exact(source, destination, ssh) == expected
        assert controller._mirror_remote_exact(source, destination, ssh) == expected
        destination.write_text('{"status":"DRIFT"}\n', encoding="utf-8")
        destination.chmod(0o600)
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_remote_mirror_failed",
        ):
            controller._mirror_remote_exact(source, destination, ssh)


def test_encrypted_offhost_copy_streams_exact_inode_and_rejects_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-offhost-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        source = root / "remote.enc"
        destination = root / "local.enc"
        source.write_bytes(os.urandom(2 * 1024 * 1024 + 31))
        source.chmod(0o600)
        fake_ssh = root / "ssh.py"
        fake_ssh.write_text(
            "import shlex,subprocess,sys\n"
            "p=subprocess.run(shlex.split(sys.argv[-1]),stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
            "sys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr)\n"
            "raise SystemExit(p.returncode)\n",
            encoding="utf-8",
        )
        expected = sha256(source.read_bytes()).hexdigest()
        ssh = [sys.executable, str(fake_ssh)]
        monkeypatch.setattr(controller, "SSH_BINARY", sys.executable)
        assert controller._copy_remote_artifact_exact(
            source,
            destination,
            ssh,
            expected_size=source.stat().st_size,
            expected_sha256=expected,
        ) == expected
        assert destination.read_bytes() == source.read_bytes()
        destination.write_bytes(b"tampered")
        destination.chmod(0o600)
        with pytest.raises(
            controller.ChoreographyError, match="offhost_copy_existing_drift"
        ):
            controller._copy_remote_artifact_exact(
                source,
                destination,
                ssh,
                expected_size=source.stat().st_size,
                expected_sha256=expected,
            )


def test_nonce_backup_name_replaces_plan_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-backup-name-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        release_sha, release_tree = _git_identity()
        _plan_path, _source, document = _plan(root)
        phases = document["phases"]
        nonce_source = root / "web-backup" / (
            "market-archive-before-abcdef123456-20260828T120000Z-deadbeef.dump"
        )
        monkeypatch.setattr(
            controller,
            "_backup_receipt_binding",
            lambda *_args, **_kwargs: (
                {"backup": {"path": str(nonce_source)}},
                "a" * 64,
            ),
        )
        backup_phase = next(
            phase for phase in phases if phase["id"] == "backup_restore_offhost"
        )
        encrypt = backup_phase["commands"][2]
        materialized = controller._materialize_command(
            encrypt, phases=phases, context={}, ssh_argv=[]
        )
        assert controller._option(materialized["arguments"], "--source") == str(
            nonce_source
        )
        assert controller._option(
            materialized["arguments"], "--destination"
        ) == str(nonce_source) + ".enc"
        assert controller._option(
            materialized["arguments"], "--receipt"
        ) == str(nonce_source) + ".encryption.json"


def test_interrupted_exclusive_artifact_is_adopted_without_replay() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-adopt-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        release_sha, release_tree = _git_identity()
        output = root / "bot-catchup.json"
        artifact = {
            "schema": "production_market_catchup_bot/1.1",
            "role": "bot",
            "release_sha": release_sha,
            "secrets_disclosed": False,
        }
        output.write_text(
            json.dumps(artifact, sort_keys=True) + "\n", encoding="utf-8"
        )
        output.chmod(0o600)
        command = {
            "host": "local",
            "tool": "audit_production_market_catchup.py",
            "arguments": [
                "bot",
                "--release-sha",
                release_sha,
                "--output",
                str(output),
            ],
        }
        result = controller._existing_interrupted_result(command, ssh_argv=[])
        assert result == {
            "status": "PASS",
            "schema": "production_market_catchup_bot/1.1",
            "artifact_sha256": sha256(output.read_bytes()).hexdigest(),
        }


def test_pre_migration_rollback_requires_every_recorded_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-rollback-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        _plan_path, _source, document = _plan(root)
        release_sha = str(document["release_sha"])
        release_tree = str(document["release_tree"])
        phases = document["phases"]
        web = root / "web-bluegreen.json"
        bot = root / "bot-bluegreen.json"
        for path, role in ((web, "web"), (bot, "bot")):
            path.write_text(
                json.dumps(
                    {"release_sha": release_sha, "role": role}, sort_keys=True
                )
                + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
        monkeypatch.setattr(
            controller,
            "_remote_read",
            lambda path, _ssh: path.read_bytes(),
        )
        calls: list[str] = []

        def run(command: dict[str, object], **_kwargs: object) -> dict[str, object]:
            role = str(controller._signature(command)[2])
            calls.append(role)
            return {"status": "ROLLED_BACK", "role": role}

        monkeypatch.setattr(controller, "_run_command", run)
        journal_path = root / "controller.json"
        journal = controller._initial_journal(
            plan_sha256="a" * 64,
            release_sha=release_sha,
            release_tree=release_tree,
            source_sha256="b" * 64,
        )
        journal.update(
            {
                "active_phase": "bluegreen_workload_quiesce",
                "active_command_index": 2,
                "active_command_results": [{}, {}],
            }
        )
        result = controller._run_pre_migration_rollback(
            journal=journal,
            journal_path=journal_path,
            phases=phases,
            local_control_root=root,
            remote_control_root=root,
            control_entries={},
            control_manifest_sha256="c" * 64,
            release_sha=release_sha,
            release_tree=release_tree,
            ssh_argv=[],
        )
        assert result["status"] == "ROLLED_BACK"
        assert calls == ["web", "bot"]

        bot.unlink()
        calls.clear()
        journal.update(
            {
                "status": "RUNNING",
                "active_phase": "bluegreen_workload_quiesce",
                "active_command_index": 2,
                "active_command_started_at_utc": None,
                "active_command_results": [{}, {}],
            }
        )
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_pre_migration_rollback_state_missing",
        ):
            controller._run_pre_migration_rollback(
                journal=journal,
                journal_path=journal_path,
                phases=phases,
                local_control_root=root,
                remote_control_root=root,
                control_entries={},
                control_manifest_sha256="c" * 64,
                release_sha=release_sha,
                release_tree=release_tree,
                ssh_argv=[],
            )


def test_controller_lock_is_recoverable_after_real_sigkill() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-lock-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        lock = root / controller.CONTROLLER_LOCK_NAME
        script = (
            "import sys,time\n"
            "from pathlib import Path\n"
            "from scripts.run_production_private_primary_choreography import _ControllerGuard\n"
            "g=_ControllerGuard(Path(sys.argv[1]),plan_sha256='a'*64,release_sha='b'*40,release_tree='c'*40)\n"
            "g.acquire();print('READY',flush=True);time.sleep(60)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(lock)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=10) == -signal.SIGKILL
        process.stdout.close()
        assert process.stderr is not None
        process.stderr.close()
        guard = controller._ControllerGuard(
            lock,
            plan_sha256="a" * 64,
            release_sha="b" * 40,
            release_tree="c" * 40,
        )
        guard.acquire()
        guard.release(terminal=True)
        assert not lock.exists()


def test_controller_child_keeps_exact_lock_after_parent_sigkill() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-child-fence-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        lock = root / controller.CONTROLLER_LOCK_NAME
        started = root / "started"
        finished = root / "finished"
        worker = root / "worker.py"
        worker.write_text(
            "import pathlib,sys,time\n"
            "pathlib.Path(sys.argv[1]).write_text('yes')\n"
            "time.sleep(1.2)\n"
            "pathlib.Path(sys.argv[2]).write_text('yes')\n",
            encoding="utf-8",
        )
        parent_code = (
            "import os,sys\n"
            "from pathlib import Path\n"
            "from scripts.run_production_private_primary_choreography import _ControllerGuard,_run_guarded_process\n"
            "lock,worker,started,finished=map(Path,sys.argv[1:5])\n"
            "guard=_ControllerGuard(lock,plan_sha256='a'*64,release_sha='b'*40,release_tree='c'*40)\n"
            "guard.acquire()\n"
            "_run_guarded_process([sys.executable,str(worker),str(started),str(finished)],cwd=lock.parent,env={'PATH':'/usr/bin:/bin'},pass_fds=(),timeout_seconds=10,guard=guard)\n"
        )
        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                parent_code,
                str(lock),
                str(worker),
                str(started),
                str(finished),
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = 5.0
        import time
        end = time.monotonic() + deadline
        while time.monotonic() < end and not started.exists():
            if parent.poll() is not None:
                raise AssertionError(parent.stderr.read() if parent.stderr else "")
            time.sleep(0.02)
        assert started.exists()
        os.kill(parent.pid, signal.SIGKILL)
        assert parent.wait(timeout=3) == -signal.SIGKILL
        if parent.stderr is not None:
            parent.stderr.close()
        contender = controller._ControllerGuard(
            lock,
            plan_sha256="a" * 64,
            release_sha="b" * 40,
            release_tree="c" * 40,
        )
        with pytest.raises(
            controller.ChoreographyError, match="controller_lock_unavailable"
        ):
            contender.acquire()
        end = time.monotonic() + 5
        while time.monotonic() < end and not finished.exists():
            time.sleep(0.02)
        assert finished.exists()
        acquired = False
        end = time.monotonic() + 5
        while time.monotonic() < end:
            contender = controller._ControllerGuard(
                lock,
                plan_sha256="a" * 64,
                release_sha="b" * 40,
                release_tree="c" * 40,
            )
            try:
                contender.acquire()
            except controller.ChoreographyError:
                time.sleep(0.02)
                continue
            acquired = True
            break
        assert acquired
        contender.release(terminal=True)


def test_product_pre_child_retry_requires_no_transaction_artifact() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-product-prechild-") as temporary:
        root = Path(temporary)
        transaction_root = root / "production-transactions"
        artifact_root = root / "production-queue-artifacts"
        transaction_root.mkdir()
        artifact_root.mkdir()
        transaction_id = "product-cutover-12345678"
        receipt = transaction_root / "terminal.json"
        command = {
            "host": "local",
            "tool": "promote_production_private_primary_product.py",
            "arguments": [
                "--transaction-root", str(transaction_root),
                "--transaction-id", transaction_id,
                "--queue-artifact-dir", str(artifact_root),
                "--receipt", str(receipt),
            ],
        }
        assert controller._product_pre_child_retry_is_clean(command) is True
        (transaction_root / transaction_id).mkdir()
        assert controller._product_pre_child_retry_is_clean(command) is False
        (transaction_root / transaction_id).rmdir()
        receipt.write_text("{}\n", encoding="utf-8")
        assert controller._product_pre_child_retry_is_clean(command) is False


def test_killing_lock_keeper_cannot_orphan_an_unfenced_phase_child() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-keeper-kill-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        lock = root / controller.CONTROLLER_LOCK_NAME
        identity = root / "identity"
        worker = root / "worker.py"
        worker.write_text(
            "import os,pathlib,sys,time\n"
            "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getppid()}')\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        parent_code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from scripts.run_production_private_primary_choreography import _ControllerGuard,_run_guarded_process\n"
            "lock,worker,identity=map(Path,sys.argv[1:4])\n"
            "guard=_ControllerGuard(lock,plan_sha256='a'*64,release_sha='b'*40,release_tree='c'*40)\n"
            "guard.acquire()\n"
            "_run_guarded_process([sys.executable,str(worker),str(identity)],cwd=lock.parent,env={'PATH':'/usr/bin:/bin'},pass_fds=(),timeout_seconds=120,guard=guard)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code, str(lock), str(worker), str(identity)],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        import time
        end = time.monotonic() + 5
        while time.monotonic() < end and not identity.exists():
            if parent.poll() is not None:
                raise AssertionError(parent.stderr.read() if parent.stderr else "")
            time.sleep(0.02)
        assert identity.exists()
        child_pid, keeper_pid = (
            int(value) for value in identity.read_text(encoding="ascii").split(":")
        )
        os.kill(keeper_pid, signal.SIGKILL)
        while Path(f"/proc/{child_pid}").exists():
            contender = controller._ControllerGuard(
                lock,
                plan_sha256="a" * 64,
                release_sha="b" * 40,
                release_tree="c" * 40,
            )
            with pytest.raises(
                controller.ChoreographyError,
                match="controller_lock_unavailable",
            ):
                contender.acquire()
            if time.monotonic() >= end:
                raise AssertionError("phase child survived keeper death")
            time.sleep(0.01)
        parent.wait(timeout=5)
        if parent.stderr is not None:
            parent.stderr.close()
        contender = controller._ControllerGuard(
            lock,
            plan_sha256="a" * 64,
            release_sha="b" * 40,
            release_tree="c" * 40,
        )
        contender.acquire()
        contender.release(terminal=True)


def test_controller_cli_validate_and_shell_help_are_reproducible() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-cli-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, _document = _plan(root)
        old_sha = str(_document["release_sha"])
        old_tree = str(_document["release_tree"])
        release_checkout = root / "exact-release"
        subprocess.run(["git", "init", "-q", str(release_checkout)], check=True)
        subprocess.run(
            ["git", "-C", str(release_checkout), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(release_checkout), "config", "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        (release_checkout / "release.txt").write_text("exact\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(release_checkout), "add", "release.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(release_checkout), "commit", "-qm", "exact"],
            check=True,
        )
        release_sha = subprocess.check_output(
            ["git", "-C", str(release_checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        release_tree = subprocess.check_output(
            ["git", "-C", str(release_checkout), "rev-parse", "HEAD^{tree}"],
            text=True,
        ).strip()
        subprocess.run(
            [
                "git", "-C", str(release_checkout), "update-ref",
                controller.APPROVED_RELEASE_REF, release_sha,
            ],
            check=True,
        )
        old_control = Path(_document["local_control_release_root"])
        new_control = old_control.parent / release_sha
        old_control.rename(new_control)
        pair = new_control / controller.CONTROL_RELEASE_PAIR_RECEIPT
        pair_value = json.loads(pair.read_text(encoding="utf-8"))
        pair_value.update({"release_sha": release_sha, "release_tree": release_tree})
        pair.write_text(
            json.dumps(pair_value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        control_manifest = new_control / controller.CONTROL_PAYLOAD_MANIFEST
        pair_digest = sha256(pair.read_bytes()).hexdigest()
        manifest_lines = []
        for line in control_manifest.read_text(encoding="utf-8").splitlines():
            if line.endswith(
                "  ./" + controller.CONTROL_RELEASE_PAIR_RECEIPT
            ):
                line = (
                    pair_digest
                    + "  ./"
                    + controller.CONTROL_RELEASE_PAIR_RECEIPT
                )
            manifest_lines.append(line)
        control_manifest.write_text(
            "\n".join(manifest_lines) + "\n", encoding="utf-8"
        )
        control_manifest_digest = sha256(
            control_manifest.read_bytes()
        ).hexdigest()

        def rebind(value: object) -> object:
            if isinstance(value, str):
                return value.replace(old_sha, release_sha).replace(
                    old_tree, release_tree
                )
            if isinstance(value, list):
                return [rebind(item) for item in value]
            if isinstance(value, dict):
                return {key: rebind(item) for key, item in value.items()}
            return value

        _document = rebind(_document)
        assert isinstance(_document, dict)
        product_phase = next(
            phase
            for phase in _document["phases"]
            if phase["id"] == "product_promotion"
        )
        product_arguments = product_phase["commands"][0]["arguments"]
        release_checkout_index = product_arguments.index(
            "--release-checkout"
        )
        product_arguments[release_checkout_index + 1] = str(
            release_checkout
        )
        _document["control_payload_manifest_sha256"] = control_manifest_digest
        evidence_phase = next(
            phase
            for phase in _document["phases"]
            if phase["id"] == "nine_source_evidence"
        )
        for command in evidence_phase["commands"]:
            command["arguments"] = controller._set_option(
                command["arguments"],
                "--expected-control-manifest-sha256",
                control_manifest_digest,
            )
        plan.write_text(json.dumps(_document, sort_keys=True), encoding="utf-8")
        build_receipt_path = root / "plan-build.json"
        build_receipt = json.loads(
            build_receipt_path.read_text(encoding="utf-8")
        )
        build_receipt.update(
            {
                "release_sha": release_sha,
                "release_tree": release_tree,
                "plan_sha256": sha256(plan.read_bytes()).hexdigest(),
                "product_image_ids": _document["product_image_ids"],
            }
        )
        build_receipt["path_sha256"]["local_control_release_root"] = sha256(
            str(new_control).encode("utf-8")
        ).hexdigest()
        build_receipt["path_sha256"]["remote_control_release_root"] = sha256(
            str(_document["remote_control_release_root"]).encode("utf-8")
        ).hexdigest()
        build_receipt["path_sha256"]["release_checkout"] = sha256(
            str(release_checkout).encode("utf-8")
        ).hexdigest()
        build_receipt["input_sha256"]["control_manifest"] = (
            control_manifest_digest
        )
        build_receipt["input_sha256"]["control_pair_receipt"] = pair_digest
        if isinstance(build_receipt.get("input_paths"), dict):
            build_receipt["input_paths"] = rebind(build_receipt["input_paths"])
            build_receipt["input_paths"]["control_manifest"] = str(
                control_manifest
            )
            build_receipt["input_paths"]["control_pair_receipt"] = str(pair)
        build_receipt["web_ssh_argv_sha256"] = sha256(
            b"/usr/bin/ssh\0root@web.example\0"
        ).hexdigest()
        build_receipt_path.write_text(
            json.dumps(build_receipt, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        installed_controller = Path(
            _document["local_control_release_root"]
        ) / "scripts/run_production_private_primary_choreography.py"
        hostile_cwd = root / "hostile-cwd"
        (hostile_cwd / "scripts").mkdir(parents=True)
        (hostile_cwd / "scripts/cutover_telegram_delivery_queue_production.py").write_text(
            "raise RuntimeError('hostile cwd imported')\n", encoding="utf-8"
        )
        environment = dict(os.environ)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(installed_controller),
                "validate",
                "--plan",
                str(plan),
                    "--expected-plan-sha256",
                    sha256(plan.read_bytes()).hexdigest(),
                    "--plan-build-receipt",
                    str(build_receipt_path),
                    "--expected-plan-build-receipt-sha256",
                    sha256(build_receipt_path.read_bytes()).hexdigest(),
                "--release-root",
                str(release_checkout),
                "--expected-source-manifest",
                str(_source),
                "--expected-deployment-manifest",
                str(_document["deployment_manifest"]),
                "--expected-deployment-manifest-sha256",
                sha256(
                    Path(_document["deployment_manifest"]).read_bytes()
                ).hexdigest(),
                "--expected-web-ssh-argv-sha256",
                    sha256(b"/usr/bin/ssh\0root@web.example\0").hexdigest(),
                "--expected-local-control-release-root",
                str(_document["local_control_release_root"]),
                "--expected-remote-control-release-root",
                str(_document["remote_control_release_root"]),
                "--expected-control-payload-manifest-sha256",
                str(_document["control_payload_manifest_sha256"]),
            ],
            cwd=hostile_cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["status"] == "PLAN_PASS"
        promoter = Path(
            _document["local_control_release_root"]
        ) / "scripts/promote_production_private_primary_product.py"
        imported = subprocess.run(
            [sys.executable, str(promoter), "--help"],
            cwd=hostile_cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert imported.returncode == 0, imported.stderr
        assert "--recovery-action" in imported.stdout

        manifest_entries = controller._control_release_manifest(
            Path(_document["local_control_release_root"]),
            expected_manifest_sha256=str(
                _document["control_payload_manifest_sha256"]
            ),
            release_sha=str(_document["release_sha"]),
            release_tree=str(_document["release_tree"]),
        )
        dependency = Path(
            _document["local_control_release_root"]
        ) / "scripts/cutover_telegram_delivery_queue_production.py"
        dependency.write_text("raise RuntimeError('tampered')\n", encoding="utf-8")
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_payload_dependency_drift",
        ):
            controller._run_local_release_tool(
                Path(_document["local_control_release_root"]),
                relative="scripts/promote_production_private_primary_product.py",
                expected_sha256=manifest_entries[
                    "scripts/promote_production_private_primary_product.py"
                ],
                manifest_entries=manifest_entries,
                arguments=["--help"],
            )
    help_result = subprocess.run(
        ["bash", str(DEPLOY), "help"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "validate-private-primary-release" in help_result.stdout
    assert "private-primary-release" in help_result.stdout
    assert "recover-private-primary-release" in help_result.stdout


def test_handmade_plan_receipt_without_builder_binding_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-handmade-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, _document = _plan(root)
        receipt_path = root / "plan-build.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("input_paths")
        receipt.pop("builder_script_sha256")
        receipt.pop("builder_tool")
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
        receipt_path.chmod(0o600)
        with pytest.raises(
            controller.ChoreographyError,
            match="controller_plan_build_receipt_invalid",
        ):
            controller.validate(_args(root, plan))


def test_controller_rejects_path_selected_ssh_binary() -> None:
    with tempfile.TemporaryDirectory(prefix="private-primary-ssh-path-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        plan, _source, document = _plan(root)
        document["web_ssh_argv"] = ["ssh", "root@web.example"]
        plan.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _refresh_build_receipt(root, plan)
        receipt_path = root / "plan-build.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["web_ssh_argv_sha256"] = sha256(
            b"ssh\0root@web.example\0"
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
        receipt_path.chmod(0o600)
        args = _args(root, plan)
        args.expected_web_ssh_argv_sha256 = receipt["web_ssh_argv_sha256"]
        with pytest.raises(
            controller.ChoreographyError,
            match="plan_web_ssh_invalid",
        ):
            controller.validate(args)


def test_zero_owner_watchdog_holds_lock_then_releases_for_recovery() -> None:
    import time

    with tempfile.TemporaryDirectory(prefix="private-primary-watchdog-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        lock = root / controller.CONTROLLER_LOCK_NAME
        journal_path = root / "journal.json"
        now = controller._utc_now()
        journal = {
            "schema": controller.JOURNAL_SCHEMA,
            "status": "RUNNING",
            "zero_owner_started_at_utc": controller._utc_text(now),
            "zero_owner_deadline_utc": controller._utc_text(
                now + controller.timedelta(seconds=120)
            ),
            "pipeline_forward_only": False,
            "secrets_disclosed": False,
        }
        journal_path.write_text(
            json.dumps(journal, sort_keys=True) + "\n", encoding="utf-8"
        )
        journal_path.chmod(0o600)
        parent_code = (
            "import json,sys,time\n"
            "from pathlib import Path\n"
            "from scripts.run_production_private_primary_choreography import (\n"
            "    _ControllerGuard,_arm_zero_owner_watchdog\n"
            ")\n"
            "lock,journal_path=map(Path,sys.argv[1:3])\n"
            "journal=json.loads(journal_path.read_text())\n"
            "guard=_ControllerGuard(lock,plan_sha256='a'*64,release_sha='b'*40,release_tree='c'*40)\n"
            "guard.acquire()\n"
            "_arm_zero_owner_watchdog(journal_path=journal_path,journal=journal,guard=guard)\n"
            "print('READY',flush=True)\n"
            "time.sleep(60)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code, str(lock), str(journal_path)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert parent.stdout is not None
            assert parent.stdout.readline().strip() == "READY"
            watchdog_path = controller._watchdog_path(journal_path)
            end = time.monotonic() + 3
            payload: dict[str, object] = {}
            while time.monotonic() < end:
                if watchdog_path.exists():
                    payload = json.loads(
                        watchdog_path.read_text(encoding="utf-8")
                    )
                    if payload.get("status") == "ARMED" and payload.get("pid"):
                        break
                time.sleep(0.05)
            assert payload.get("status") == "ARMED"
            contender = controller._ControllerGuard(
                lock,
                plan_sha256="a" * 64,
                release_sha="b" * 40,
                release_tree="c" * 40,
            )
            with pytest.raises(
                controller.ChoreographyError,
                match="controller_lock_unavailable",
            ):
                contender.acquire()
            os.kill(parent.pid, signal.SIGKILL)
            assert parent.wait(timeout=5) == -signal.SIGKILL
            released = False
            end = time.monotonic() + 5
            while time.monotonic() < end:
                current = json.loads(watchdog_path.read_text(encoding="utf-8"))
                if current.get("status") == "CONTROLLER_DEAD_RESTORE_REQUIRED":
                    try:
                        contender.acquire()
                        released = True
                        break
                    except controller.ChoreographyError:
                        time.sleep(0.05)
                        continue
                time.sleep(0.05)
            assert released
            assert (
                json.loads(watchdog_path.read_text(encoding="utf-8"))["status"]
                == "CONTROLLER_DEAD_RESTORE_REQUIRED"
            )
            contender.release(terminal=True)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            if parent.stdout is not None:
                parent.stdout.close()
            if parent.stderr is not None:
                parent.stderr.close()


def test_official_shell_refuses_hostile_path_ssh_and_rebuilds_on_recover() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    controller_fn = source.split(
        "run_private_primary_choreography_controller() {", 1
    )[1].split("\n}", 1)[0]
    assert '"$value" == "/usr/bin/ssh"' in controller_fn
    assert 'PATH-selected SSH executable' in controller_fn
    assert 'action" == "recover"' in controller_fn
    assert "build_official_private_primary_choreography_plan" in controller_fn
    recover_branch = controller_fn.split('action" == "recover"', 1)[1].split(
        "else", 1
    )[0]
    assert "build_official_private_primary_choreography_plan" not in recover_branch
    assert "verify_official_private_primary_plan_build_receipt" in recover_branch


def test_remote_helpers_pin_absolute_python_not_path_selected_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_ssh(ssh_argv, remote_command, **_kwargs):
        captured.append(str(remote_command))
        return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(controller, "_ssh_completed", fake_ssh)
    with tempfile.TemporaryDirectory(prefix="private-primary-remote-python-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        path = root / "artifact.json"
        path.write_bytes(b'{"status":"PASS"}\n')
        path.chmod(0o600)
        ssh = [controller.SSH_BINARY, "-o", "BatchMode=yes", "root@example"]
        assert controller._remote_read(path, ssh) == b"{}"
        with pytest.raises(controller.ChoreographyError):
            controller._mirror_remote_exact(path, root / "dest.json", ssh)
    assert captured
    assert all(item.startswith("/usr/bin/python3 ") for item in captured)
    assert all(" python3 " not in f" {item} " for item in captured)
    assert controller.REMOTE_PYTHON == "/usr/bin/python3"
    run_source = inspect.getsource(controller._run_command)
    copy_source = inspect.getsource(controller._copy_remote_artifact_exact)
    assert "REMOTE_PYTHON" in run_source
    assert "REMOTE_PYTHON" in copy_source
    assert '"python3"' not in run_source
    assert '"python3"' not in copy_source
