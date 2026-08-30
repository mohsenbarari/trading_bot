from __future__ import annotations

from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_production_private_primary_choreography_plan as builder
from scripts import run_production_private_primary_choreography as controller


SHA = "a" * 40
TREE = "b" * 40
MARKET_IMAGE = "sha256:" + "c" * 64
BOT_IMAGE = "sha256:" + "d" * 64
WEB_IMAGE = "sha256:" + "e" * 64
SIGNATURE = "f" * 64
WEB_HOST = "65.109.220.59"
TOOLS = (
    "upgrade_market_pipeline_bluegreen.py",
    "backup_market_pipeline_archive.py",
    "crypt_market_pipeline_backup.py",
    "migrate_market_pipeline_archive.py",
    "rollout_market_pipeline_shadow.py",
    "quiesce_production_legacy_market_collectors.py",
    "audit_production_market_catchup.py",
    "observe_production_private_primary.py",
    "run_release_bound_product_readiness.py",
    "reconcile_estimator_snapshot_publication_outbox.py",
    "verify_production_private_primary_promotion.py",
    "promote_production_private_primary_product.py",
    "run_production_private_primary_choreography.py",
    *builder.TRANSITIVE_RUNTIME_PAYLOADS,
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        payload = payload.encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _json(path: Path, document: dict[str, object]) -> Path:
    return _write(
        path,
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
    )


def _ssh(deployment: dict[str, str]) -> list[str]:
    return [
        builder.SSH_BINARY, "-p", deployment["IRAN_SSH_PORT"],
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={deployment['IRAN_SSH_CONNECT_TIMEOUT_SECONDS']}",
        "-o", f"ServerAliveInterval={deployment['IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS']}",
        "-o", f"ServerAliveCountMax={deployment['IRAN_SSH_SERVER_ALIVE_COUNT_MAX']}",
        "-o", "ConnectionAttempts=1", "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
        "-o", "IdentitiesOnly=yes", "-i", deployment["IRAN_SSH_PRIVATE_KEY_PATH"],
        f"{deployment['IRAN_SSH_USER']}@{deployment['IRAN_HOST']}",
    ]


def _argv_digest(values: list[str]) -> str:
    return sha256(b"\0".join(item.encode() for item in values) + b"\0").hexdigest()


def _fixture(tmp_path: Path) -> tuple[SimpleNamespace, dict[str, Path]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    secure = tmp_path / "production-transaction"
    secure.mkdir(mode=0o700)
    control = tmp_path / "control" / SHA
    control.mkdir(parents=True, mode=0o700)
    control.chmod(0o700)
    scripts = control / "scripts"
    scripts.mkdir(mode=0o700)
    entries: list[tuple[str, str]] = []
    for index, tool in enumerate(TOOLS):
        payload = f"#!/usr/bin/env python3\n# exact-{index}\n".encode()
        target = scripts / tool
        target.write_bytes(payload)
        target.chmod(0o644)
        entries.append((f"scripts/{tool}", sha256(payload).hexdigest()))
    manifest = _write(
        control / builder.CONTROL_MANIFEST_NAME,
        "".join(f"{digest}  ./{relative}\n" for relative, digest in entries),
    )
    control_pair = _json(
        control / builder.CONTROL_PAIR_NAME,
        {
            "schema": "market_pipeline_release_pair/1.0",
            "release_sha": SHA,
            "release_tree": TREE,
            "secrets_disclosed": False,
        },
    )
    runtime = _write(
        secure / "runtime.env",
        "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\nSECRET_SENTINEL=never-copy-this\n",
    )
    deployment_values = {
        "IRAN_SSH_AUTH_METHOD": "key",
        "IRAN_SSH_USER": "root",
        "IRAN_HOST": WEB_HOST,
        "IRAN_SSH_PORT": "22",
        "IRAN_SSH_CONNECT_TIMEOUT_SECONDS": "10",
        "IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS": "15",
        "IRAN_SSH_SERVER_ALIVE_COUNT_MAX": "3",
        "IRAN_SSH_COMMAND_TIMEOUT_SECONDS": "900",
        "IRAN_SSH_PRIVATE_KEY_PATH": "/root/secure-envs/trading-bot/wa-fi.key",
        "IRAN_SSH_PASSWORD": "never-copy-password",
    }
    deployment = _write(
        secure / "deployment.env",
        "".join(f"{key}={value}\n" for key, value in deployment_values.items()),
    )
    web_root = "/srv/trading-bot/market-data-production"
    bot_root = "/srv/trading-bot/production-data/market-pipeline"
    web_env_text = (
        f"MARKET_WEB_DATA_ROOT={web_root}\n"
        f"MARKET_PRODUCT_SNAPSHOT_ROOT={web_root}/snapshots\n"
        "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-primary-web\n"
        f"MARKET_PIPELINE_IMAGE={MARKET_IMAGE}\n"
        f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
        "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
        "MARKET_POSTGRES_USER=market_data\nMARKET_POSTGRES_DB=market_archive\n"
    )
    bot_env_text = (
        f"MARKET_BOT_DATA_ROOT={bot_root}\n"
        f"MARKET_PRODUCT_SNAPSHOT_ROOT={bot_root}/snapshots\n"
        "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-primary-bot\n"
        f"MARKET_PIPELINE_IMAGE={MARKET_IMAGE}\n"
        f"MARKET_PIPELINE_RELEASE_SHA={SHA}\n"
        "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY\n"
    )
    web_env = _write(control / "web.primary.env", web_env_text)
    bot_env = _write(control / "bot.primary.env", bot_env_text)
    web_old = _write(
        control / "web.old.env",
        "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-web\nMARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n",
    )
    bot_old = _write(
        control / "bot.old.env",
        "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-bot\nMARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n",
    )
    market_receipt = _json(
        secure / "market-image.json",
        {
            "schema": "market_pipeline_image_release/1.0",
            "environment": "production",
            "release_sha": SHA,
            "release_tree": TREE,
            "image_id": MARKET_IMAGE,
            "input_signature": SIGNATURE,
            "platform": "linux/amd64",
            "runtime_user": "10001:10001",
            "transport": "ssh_stream_then_verify_content_id",
            "secrets_disclosed": False,
        },
    )
    primary_pair = _json(
        secure / "primary-pair.json",
        {
            "schema": "market_pipeline_primary_release_pair/1.0",
            "release_sha": SHA,
            "release_tree": TREE,
            "project_name": "market-private-pipeline-primary",
            "feed_mode": "PRIVATE_PRIMARY",
            "private_primary_allowed": True,
            "expected_snapshot_lane": "PRIVATE_PRIMARY",
            "product_authority_changed": False,
            "legacy_retirement_authorized": False,
            "capture_backfill": {
                "not_before_utc": "2026-08-25T09:33:00Z",
                "source_codes": ["MELTED_PRIMARY_FLOW", "GROUP_1", "GROUP_2"],
                "max_messages": 100000,
            },
            "roles": {
                "web": {
                    "source_sha256": "1" * 64,
                    "output_sha256": _digest(web_env),
                    "product_snapshot_root": f"{web_root}/snapshots",
                },
                "bot": {
                    "source_sha256": "2" * 64,
                    "output_sha256": _digest(bot_env),
                    "product_snapshot_root": f"{bot_root}/snapshots",
                },
            },
            "image_id": MARKET_IMAGE,
            "secrets_disclosed": False,
        },
    )
    preflight = _json(
        secure / "preflight.json",
        {
            "schema": "market_pipeline_two_host_preflight/1.0",
            "environment": "production",
            "release_sha": SHA,
            "release_tree": TREE,
            "image_id": MARKET_IMAGE,
            "image_input_signature": SIGNATURE,
            "control_payload_manifest_sha256": _digest(manifest),
            "role_env_sha256": {"bot": _digest(bot_env), "web": _digest(web_env)},
            "host_preflight_sha256": {"bot": "3" * 64, "web": "4" * 64},
            "private_shadow_only": True,
            "image_loaded_on_both_hosts": True,
            "services_started": False,
            "database_mutated": False,
            "product_authority_changed": False,
            "telegram_capture_cutover_authorized": False,
            "secrets_disclosed": False,
        },
    )
    bot_image = _json(
        secure / "product-bot-image.json",
        {
            "schema_version": 1,
            "environment": "production",
            "release_sha": SHA,
            "release_tree": TREE,
            "image_id": BOT_IMAGE,
            "input_signature": "5" * 64,
            "secrets_disclosed": False,
        },
    )
    web_image = _json(
        secure / "product-web-image.json",
        {
            "schema_version": 1,
            "environment": "production",
            "role": "iran",
            "release_sha": SHA,
            "release_tree": TREE,
            "image_id": WEB_IMAGE,
            "input_signature": "6" * 64,
            "bundle_sha256": "7" * 64,
            "target": {
                "host": WEB_HOST,
                "project_dir": "/srv/trading-bot/current",
                "compose_project": "current",
                "image": "trading_bot_base_iran:latest",
            },
            "secrets_disclosed": False,
        },
    )
    private_manifest = _write(secure / "private.env", b"PRIVATE=1\n")
    private_manifest_receipt = _json(
        secure / "private-receipt.json",
        {
            "schema": "production_private_primary_deploy_manifest/1.0",
            "status": "PASS",
            "source_sha256": _digest(deployment),
            "output_sha256": _digest(private_manifest),
            "source_preserved_by_tool": True,
            "secrets_disclosed": False,
        },
    )
    offhost_root = secure / "offhost-backups"
    offhost_root.mkdir(mode=0o700)
    local_key = _write(secure / "market-backup.key", b"opaque-test-key\n")
    files = {
        "runtime_source": runtime,
        "deployment_manifest": deployment,
        "control_pair_receipt": control_pair,
        "primary_pair_receipt": primary_pair,
        "market_image_receipt": market_receipt,
        "preflight_receipt": preflight,
        "web_env": web_env,
        "bot_env": bot_env,
        "web_old_env": web_old,
        "bot_old_env": bot_old,
        "product_bot_image_receipt": bot_image,
        "product_web_image_receipt": web_image,
        "private_manifest": private_manifest,
        "private_manifest_receipt": private_manifest_receipt,
    }
    namespace: dict[str, object] = {
        "release_sha": SHA,
        "release_tree": TREE,
        "local_control_release_root": str(control),
        "remote_control_release_root": f"/srv/trading-bot/market-pipeline-releases/{SHA}",
        "expected_control_manifest_sha256": _digest(manifest),
        "expected_web_ssh_target": f"root@{WEB_HOST}",
        "expected_web_ssh_argv_sha256": _argv_digest(_ssh(deployment_values)),
        "web_backup_root": "/srv/trading-bot/market-data-production/backups",
        "local_offhost_backup_root": str(offhost_root),
        "web_backup_key_file": "/root/secure-envs/trading-bot/market-backup.key",
        "local_backup_key_file": str(local_key),
        "remote_web_env": f"/srv/trading-bot/market-pipeline-releases/{SHA}/web.primary.env",
        "remote_web_old_env": "/root/secure-envs/trading-bot/market-pipeline/web.old.env",
        "release_checkout": str(tmp_path),
        "secure_transaction_root": str(secure),
        "transaction_id": "private-primary-release-0001",
        "output": str(secure / "plan.json"),
        "receipt": str(secure / "plan-receipt.json"),
        "confirm": builder.CONFIRMATION,
    }
    for label, path in files.items():
        namespace[label] = str(path)
        namespace[f"expected_{label}_sha256"] = _digest(path)
    return SimpleNamespace(**namespace), files


def _option(arguments: list[str], name: str) -> str | None:
    return arguments[arguments.index(name) + 1] if name in arguments else None


def test_builds_canonical_value_free_deterministic_plan(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    plan, receipt = builder.build(args)
    first_plan = Path(args.output).read_bytes()
    first_receipt = Path(args.receipt).read_bytes()
    assert stat_mode(Path(args.output)) == 0o600
    assert stat_mode(Path(args.receipt)) == 0o600
    assert [phase["id"] for phase in plan["phases"]] == list(builder.PHASES)
    assert plan["product_image_ids"] == {"bot": BOT_IMAGE, "web": WEB_IMAGE}
    assert plan["web_ssh_argv"][0] == "/usr/bin/ssh"
    assert plan["role_env_bindings"] == {
        "bot": {
            "new_path": str(_files["bot_env"]),
            "new_sha256": _digest(_files["bot_env"]),
            "old_path": str(_files["bot_old_env"]),
            "old_sha256": _digest(_files["bot_old_env"]),
        },
        "web": {
            "new_path": args.remote_web_env,
            "new_sha256": _digest(_files["web_env"]),
            "old_path": args.remote_web_old_env,
            "old_sha256": _digest(_files["web_old_env"]),
        },
    }
    assert receipt["plan_sha256"] == sha256(first_plan).hexdigest()
    assert receipt["phase_count"] == 12
    assert plan["required_nine_sources"] == list(builder.REQUIRED_NINE_SOURCES)
    assert plan["sparse_one_gram"]["cells"] == ["COIN_ONE_GRAM:CASH", "COIN_ONE_GRAM:TOMORROW"]
    assert plan["sparse_one_gram"]["method"] == "ABSTAIN_NO_SAFE_SAME_COMMODITY_ANCHOR"
    assert plan["catchup_cutoff_utc"] == "2026-08-25T09:33:00Z"
    assert receipt["transaction_id"] == args.transaction_id
    assert receipt["plan_output_path_sha256"] == sha256(
        args.output.encode("utf-8")
    ).hexdigest()
    assert receipt["receipt_output_path_sha256"] == sha256(
        args.receipt.encode("utf-8")
    ).hexdigest()
    assert (
        receipt["plan_output_path_sha256"]
        != receipt["receipt_output_path_sha256"]
    )
    assert receipt["required_input_labels"] == list(builder.REQUIRED_INPUT_LABELS)
    assert tuple(receipt["input_sha256"]) == builder.REQUIRED_INPUT_LABELS
    assert receipt["builder_tool"] == (
        "scripts/build_production_private_primary_choreography_plan.py"
    )
    assert receipt["builder_script_sha256"] == _digest(
        Path(args.local_control_release_root)
        / "scripts/build_production_private_primary_choreography_plan.py"
    )
    assert receipt["input_paths"]["control_manifest"] == str(
        Path(args.local_control_release_root) / builder.CONTROL_MANIFEST_NAME
    )
    assert plan["transaction_id"] == args.transaction_id
    rebuilt_plan, rebuilt_receipt = builder.build(args)
    assert Path(args.output).read_bytes() == first_plan
    assert Path(args.receipt).read_bytes() == first_receipt
    assert rebuilt_receipt["plan_sha256"] == receipt["plan_sha256"]
    assert rebuilt_plan["transaction_id"] == plan["transaction_id"]
    assert b"never-copy-this" not in first_plan + first_receipt
    assert b"never-copy-password" not in first_plan + first_receipt
    for phase in plan["phases"]:
        assert phase["recovery_commands"] == []
        assert phase["rollback_commands"] == []
    readiness = plan["phases"][8]["commands"]
    assert _option(readiness[0]["arguments"], "--expected-image-id") == WEB_IMAGE
    assert _option(readiness[1]["arguments"], "--expected-image-id") == BOT_IMAGE
    assert "--expected-snapshot-sha256" not in readiness[0]["arguments"]
    settle_arguments = plan["phases"][7]["commands"][2]["arguments"]
    assert settle_arguments[0] == "settle"
    assert "--maximum-window-seconds" in settle_arguments
    assert "--settle-seconds" not in settle_arguments
    assert settle_arguments[
        settle_arguments.index("--maximum-window-seconds") + 1
    ] == "30"
    web_audit = plan["phases"][7]["commands"][0]["arguments"]
    bot_audit = plan["phases"][7]["commands"][1]["arguments"]
    assert "/market-capture-account1/market-capture-account1/" in str(
        _option(web_audit, "--account1-db")
    )
    assert "/market-fact-receiver/market-fact-receiver/" in str(
        _option(bot_audit, "--receiver-db")
    )

    args.output = str(Path(args.secure_transaction_root) / "plan-2.json")
    args.receipt = str(Path(args.secure_transaction_root) / "plan-receipt-2.json")
    _second_plan, second_receipt = builder.build(args)
    assert Path(args.output).read_bytes() == first_plan
    assert Path(args.receipt).read_bytes() != first_receipt
    assert second_receipt["plan_sha256"] == receipt["plan_sha256"]
    assert second_receipt["plan_output_path_sha256"] == sha256(
        args.output.encode("utf-8")
    ).hexdigest()
    assert second_receipt["receipt_output_path_sha256"] == sha256(
        args.receipt.encode("utf-8")
    ).hexdigest()


def test_rejects_old_and_new_data_root_drift(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    _write(
        files["bot_old_env"],
        (
            "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-bot\n"
            "MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n"
            "MARKET_BOT_DATA_ROOT=/srv/trading-bot/other-data-root\n"
        ),
    )
    args.expected_bot_old_env_sha256 = _digest(files["bot_old_env"])
    with pytest.raises(builder.PlanBuildError, match="bot_data_root_drift"):
        builder.build(args)


def test_accepts_empty_committed_control_package_marker(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    control = Path(args.local_control_release_root)
    marker = control / "core" / "__init__.py"
    marker.parent.mkdir(mode=0o700)
    marker.write_bytes(b"")
    marker.chmod(0o444)
    manifest = control / builder.CONTROL_MANIFEST_NAME
    empty_digest = sha256(b"").hexdigest()
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + f"{empty_digest}  ./core/__init__.py\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    args.expected_control_manifest_sha256 = _digest(manifest)
    preflight = Path(args.preflight_receipt)
    payload = json.loads(preflight.read_text(encoding="utf-8"))
    payload["control_payload_manifest_sha256"] = _digest(manifest)
    _json(preflight, payload)
    args.expected_preflight_receipt_sha256 = _digest(preflight)
    plan, receipt = builder.build(args)
    assert receipt["status"] == "PASS"
    assert plan["release_sha"] == SHA


def test_accepts_attested_private_primary_deploy_as_loaded_manifest(
    tmp_path: Path,
) -> None:
    args, files = _fixture(tmp_path)
    source = files["deployment_manifest"]
    private = files["private_manifest"]
    _write(private, source.read_bytes() + b"PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0\n")
    _json(
        files["private_manifest_receipt"],
        {
            "schema": "production_private_primary_deploy_manifest/1.0",
            "status": "PASS",
            "source_sha256": _digest(source),
            "output_sha256": _digest(private),
            "source_preserved_by_tool": True,
            "secrets_disclosed": False,
        },
    )
    args.deployment_manifest = str(private)
    args.expected_deployment_manifest_sha256 = _digest(private)
    args.expected_private_manifest_sha256 = _digest(private)
    args.expected_private_manifest_receipt_sha256 = _digest(
        files["private_manifest_receipt"]
    )
    plan, receipt = builder.build(args)
    assert receipt["status"] == "PASS"
    assert plan["release_sha"] == SHA


def test_rejects_unbound_private_manifest_receipt(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    payload = json.loads(files["private_manifest_receipt"].read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    payload["output_sha256"] = "1" * 64
    _json(files["private_manifest_receipt"], payload)
    args.expected_private_manifest_receipt_sha256 = _digest(
        files["private_manifest_receipt"]
    )
    with pytest.raises(builder.PlanBuildError, match="private_manifest_receipt_invalid"):
        builder.build(args)


def test_rejects_portable_digest_mismatch_on_preflight_v11(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    payload = json.loads(files["preflight_receipt"].read_text(encoding="utf-8"))
    payload["schema"] = "market_pipeline_two_host_preflight/1.1"
    payload["portable_content_digest"] = "1" * 64
    payload["portable_content_digests"] = {"bot": "1" * 64, "web": "2" * 64}
    _json(files["preflight_receipt"], payload)
    args.expected_preflight_receipt_sha256 = _digest(files["preflight_receipt"])
    with pytest.raises(builder.PlanBuildError, match="portable_image_digest_mismatch"):
        builder.build(args)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_generated_plan_passes_controller_structural_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _files = _fixture(tmp_path)
    plan, _receipt = builder.build(args)
    monkeypatch.setattr(controller, "_assert_exact_release_checkout", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_git_identity", lambda _root: (SHA, TREE))
    validated = controller._validate_plan(plan, release_root=tmp_path)
    assert validated[0:2] == (SHA, TREE)
    assert len(validated[5]) == 12


def test_backup_uses_old_shadow_env_and_migration_uses_primary_target(
    tmp_path: Path,
) -> None:
    args, files = _fixture(tmp_path)
    plan, _receipt = builder.build(args)
    backup_phase = next(
        phase for phase in plan["phases"] if phase["id"] == "backup_restore_offhost"
    )
    for command in backup_phase["commands"][:2]:
        arguments = command["arguments"]
        assert _option(arguments, "--env-file") == args.remote_web_old_env
        assert "--bluegreen-source-env" in arguments
    migration_phase = next(
        phase for phase in plan["phases"] if phase["id"] == "migration"
    )
    arguments = migration_phase["commands"][0]["arguments"]
    assert _option(arguments, "--env-file") == args.remote_web_env
    assert _option(arguments, "--backup-env-file") == args.remote_web_old_env
    assert _digest(files["web_old_env"]) == plan["role_env_bindings"]["web"]["old_sha256"]


def test_every_release_tool_argument_vector_matches_its_parser(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    plan, _receipt = builder.build(args)
    dynamic: dict[tuple[str, str], list[str]] = {
        ("migrate_market_pipeline_archive.py", "execute"): ["--offhost-receipt-sha256", "8" * 64],
        ("upgrade_market_pipeline_bluegreen.py", "quiesce-database"): ["--expected-backup-receipt-sha256", "8" * 64, "--expected-offhost-backup-receipt-sha256", "9" * 64],
        ("upgrade_market_pipeline_bluegreen.py", "prepare-capture-authority"): ["--web-legacy-collector-receipt", "/root/secure/web.json", "--expected-web-legacy-collector-receipt-sha256", "8" * 64],
        ("upgrade_market_pipeline_bluegreen.py", "authorize-captures"): ["--web-legacy-collector-receipt", "/root/secure/web.json", "--expected-web-legacy-collector-receipt-sha256", "8" * 64, "--expected-bot-legacy-collector-receipt-sha256", "9" * 64],
        ("quiesce_production_legacy_market_collectors.py", "prepare-authority"): ["--expected-journal-sha256", "8" * 64, "--expected-bluegreen-journal-sha256", "9" * 64, "--marker-authority-sha256", "7" * 64],
        ("quiesce_production_legacy_market_collectors.py", "mark-authority-transferred"): ["--expected-journal-sha256", "8" * 64, "--expected-bluegreen-journal-sha256", "9" * 64, "--marker-authority-sha256", "7" * 64],
        ("quiesce_production_legacy_market_collectors.py", "commit"): ["--expected-primary-verification-sha256", "8" * 64],
        ("audit_production_market_catchup.py", "settle"): ["--previous-web-sha256", "8" * 64, "--previous-bot-sha256", "9" * 64],
        ("audit_production_market_catchup.py", "verify"): ["--web-sha256", "8" * 64, "--bot-sha256", "9" * 64, "--previous-web-sha256", "7" * 64, "--previous-bot-sha256", "6" * 64],
        ("run_release_bound_product_readiness.py", "execute"): ["--expected-snapshot-sha256", "8" * 64],
        ("reconcile_estimator_snapshot_publication_outbox.py", "apply"): ["--expected-plan-sha256", "8" * 64],
        ("verify_production_private_primary_promotion.py", "verify"): ["--expected-catchup-receipt-sha256", "8" * 64],
        ("promote_production_private_primary_product.py", "execute"): ["--expected-promotion-receipt-sha256", "8" * 64, "--expected-catchup-receipt-sha256", "9" * 64, "--expected-maintenance-journal-sha256", "7" * 64, "--expected-web-maintenance-journal-sha256", "6" * 64],
    }
    modules: dict[str, object] = {}
    for phase in plan["phases"]:
        for command in phase["commands"]:
            tool = command["tool"]
            if tool == "observe_production_private_primary.py":
                continue
            module = modules.setdefault(
                tool, importlib.import_module(f"scripts.{Path(tool).stem}")
            )
            parser_factory = getattr(module, "build_parser", None)
            if parser_factory is None:
                continue
            arguments = list(command["arguments"])
            signature = controller._signature(command)
            arguments.extend(dynamic.get((tool, signature[1]), []))
            parsed = parser_factory().parse_args(arguments)
            assert parsed is not None


def test_rejects_decoy_control_pair_even_with_same_bytes(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    decoy = _write(tmp_path / "decoy-pair.json", files["control_pair_receipt"].read_bytes())
    args.control_pair_receipt = str(decoy)
    args.expected_control_pair_receipt_sha256 = _digest(decoy)
    with pytest.raises(builder.PlanBuildError, match="control_pair_receipt_decoy_path"):
        builder.build(args)


def test_rejects_decoy_control_manifest_path_even_with_same_bytes(
    tmp_path: Path,
) -> None:
    args, _files = _fixture(tmp_path)
    control = Path(args.local_control_release_root)
    official = control / builder.CONTROL_MANIFEST_NAME
    decoy = _write(control / "decoy-control-payload.sha256", official.read_bytes())
    item = builder._read_bound(decoy, _digest(decoy), label="control_manifest")
    with pytest.raises(builder.PlanBuildError, match="control_manifest_decoy_path"):
        builder._control_manifest(control, item, TOOLS)


@pytest.mark.parametrize("missing", builder.TRANSITIVE_RUNTIME_PAYLOADS)
def test_rejects_control_manifest_missing_transitive_runtime_payload(
    tmp_path: Path, missing: str
) -> None:
    args, _files = _fixture(tmp_path)
    control = Path(args.local_control_release_root)
    manifest = control / builder.CONTROL_MANIFEST_NAME
    marker = f"  ./scripts/{missing}\n"
    lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    _write(manifest, "".join(line for line in lines if marker not in line))
    args.expected_control_manifest_sha256 = _digest(manifest)
    with pytest.raises(
        builder.PlanBuildError, match="control_manifest_required_file_missing"
    ):
        builder.build(args)


def test_rejects_decoy_remote_web_env_mapping(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    args.remote_web_env = (
        f"/srv/trading-bot/market-pipeline-releases/{SHA}/decoy-web.primary.env"
    )
    with pytest.raises(
        builder.PlanBuildError, match="remote_web_env_control_mapping_invalid"
    ):
        builder.build(args)


@pytest.mark.parametrize("mutation", ["target", "digest"])
def test_rejects_wrong_direct_ssh_binding(tmp_path: Path, mutation: str) -> None:
    args, _files = _fixture(tmp_path)
    if mutation == "target":
        args.expected_web_ssh_target = "root@203.0.113.9"
    else:
        args.expected_web_ssh_argv_sha256 = "0" * 64
    with pytest.raises(builder.PlanBuildError, match="web_ssh"):
        builder.build(args)


def test_rejects_digest_bound_to_path_selected_ssh_executable(
    tmp_path: Path,
) -> None:
    args, files = _fixture(tmp_path)
    deployment = builder._env(
        builder._read_bound(
            files["deployment_manifest"],
            _digest(files["deployment_manifest"]),
            label="deployment_manifest",
        )
    )
    hostile_argv = _ssh(deployment)
    hostile_argv[0] = "ssh"
    args.expected_web_ssh_argv_sha256 = _argv_digest(hostile_argv)
    with pytest.raises(builder.PlanBuildError, match="web_ssh_argv_digest_mismatch"):
        builder.build(args)


def test_rejects_wrong_product_web_compose_project(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    receipt = json.loads(files["product_web_image_receipt"].read_text(encoding="utf-8"))
    receipt["target"]["compose_project"] = "decoy"
    _json(files["product_web_image_receipt"], receipt)
    args.expected_product_web_image_receipt_sha256 = _digest(
        files["product_web_image_receipt"]
    )
    with pytest.raises(builder.PlanBuildError, match="product_web_image_receipt_invalid"):
        builder.build(args)


def test_rejects_symlinked_and_tampered_official_inputs(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    link = tmp_path / "runtime-link.env"
    link.symlink_to(files["runtime_source"])
    args.runtime_source = str(link)
    with pytest.raises(builder.PlanBuildError, match="runtime_source_unavailable"):
        builder.build(args)

    args, files = _fixture(tmp_path / "second")
    files["preflight_receipt"].write_text("{}\n", encoding="utf-8")
    files["preflight_receipt"].chmod(0o600)
    with pytest.raises(builder.PlanBuildError, match="preflight_receipt_digest_mismatch"):
        builder.build(args)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("secure_transaction_root", "relative/production"),
        ("remote_web_env", "/srv/trading-bot/releases/../decoy.env"),
        ("web_backup_root", "/"),
    ],
)
def test_rejects_unsafe_operational_paths(
    tmp_path: Path, field: str, value: str
) -> None:
    args, _files = _fixture(tmp_path)
    setattr(args, field, value)
    with pytest.raises(builder.PlanBuildError, match="path_invalid"):
        builder.build(args)


def test_rejects_output_collision_and_existing_output(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    args.receipt = args.output
    with pytest.raises(builder.PlanBuildError, match="output_scope_invalid"):
        builder.build(args)
    args, _files = _fixture(tmp_path / "second")
    Path(args.output).write_text("decoy", encoding="utf-8")
    Path(args.output).chmod(0o600)
    with pytest.raises(builder.PlanBuildError, match="plan_output_exists"):
        builder.build(args)


def test_official_approved_secure_root_is_accepted_without_production_token() -> None:
    source = Path(builder.__file__).read_text(encoding="utf-8")
    assert (
        'official_secure_root = Path("/root/secure-envs/trading-bot/release-control")'
        in source
    )
    assert "secure_root != official_secure_root" in source


def test_official_builder_receipt_is_accepted_by_controller_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _files = _fixture(tmp_path)
    plan, receipt = builder.build(args)
    monkeypatch.setattr(controller, "_assert_exact_release_checkout", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_assert_role_env_bindings", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_git_identity", lambda _root: (SHA, TREE))
    namespace = SimpleNamespace(
        command="validate",
        plan=args.output,
        expected_plan_sha256=receipt["plan_sha256"],
        plan_build_receipt=args.receipt,
        expected_plan_build_receipt_sha256=_digest(Path(args.receipt)),
        release_root=args.release_checkout,
        expected_source_manifest=args.runtime_source,
        expected_deployment_manifest=args.deployment_manifest,
        expected_deployment_manifest_sha256=_digest(Path(args.deployment_manifest)),
        expected_web_ssh_argv_sha256=receipt["web_ssh_argv_sha256"],
        expected_local_control_release_root=args.local_control_release_root,
        expected_remote_control_release_root=args.remote_control_release_root,
        expected_control_payload_manifest_sha256=args.expected_control_manifest_sha256,
    )
    result = controller.validate(namespace)
    assert result["status"] == "PLAN_PASS"
    assert result["runtime_or_database_mutated"] is False
    assert plan["web_ssh_argv"][0] == "/usr/bin/ssh"


def test_handmade_receipt_without_input_inventory_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _files = _fixture(tmp_path)
    plan, receipt = builder.build(args)
    receipt_path = Path(args.receipt)
    handmade = json.loads(receipt_path.read_text(encoding="utf-8"))
    handmade.pop("input_paths")
    handmade.pop("builder_script_sha256")
    receipt_path.write_text(
        json.dumps(handmade, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    receipt_path.chmod(0o600)
    monkeypatch.setattr(controller, "_assert_exact_release_checkout", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_assert_role_env_bindings", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_git_identity", lambda _root: (SHA, TREE))
    namespace = SimpleNamespace(
        command="validate",
        plan=args.output,
        expected_plan_sha256=receipt["plan_sha256"],
        plan_build_receipt=args.receipt,
        expected_plan_build_receipt_sha256=_digest(receipt_path),
        release_root=args.release_checkout,
        expected_source_manifest=args.runtime_source,
        expected_deployment_manifest=args.deployment_manifest,
        expected_deployment_manifest_sha256=_digest(Path(args.deployment_manifest)),
        expected_web_ssh_argv_sha256=receipt["web_ssh_argv_sha256"],
        expected_local_control_release_root=args.local_control_release_root,
        expected_remote_control_release_root=args.remote_control_release_root,
        expected_control_payload_manifest_sha256=args.expected_control_manifest_sha256,
    )
    with pytest.raises(
        controller.ChoreographyError,
        match="controller_plan_build_receipt_invalid",
    ):
        controller.validate(namespace)


def test_accepts_independent_host_market_image_ids(tmp_path: Path) -> None:
    web_market = "sha256:" + "1" * 64
    args, files = _fixture(tmp_path)
    web_text = files["web_env"].read_text(encoding="utf-8").replace(MARKET_IMAGE, web_market)
    files["web_env"].write_text(web_text, encoding="utf-8")
    os.chmod(files["web_env"], 0o600)
    pair = json.loads(files["primary_pair_receipt"].read_text(encoding="utf-8"))
    pair["schema"] = "market_pipeline_primary_release_pair/1.1"
    del pair["image_id"]
    pair["image_ids"] = {"bot": MARKET_IMAGE, "web": web_market}
    pair["roles"]["web"]["output_sha256"] = _digest(files["web_env"])
    _json(files["primary_pair_receipt"], pair)
    preflight = json.loads(files["preflight_receipt"].read_text(encoding="utf-8"))
    preflight["image_ids"] = {"bot": MARKET_IMAGE, "web": web_market}
    preflight["role_env_sha256"]["web"] = _digest(files["web_env"])
    _json(files["preflight_receipt"], preflight)
    args.expected_web_env_sha256 = _digest(files["web_env"])
    args.expected_primary_pair_receipt_sha256 = _digest(files["primary_pair_receipt"])
    args.expected_preflight_receipt_sha256 = _digest(files["preflight_receipt"])
    plan, _receipt = builder.build(args)
    assert plan["release_sha"] == SHA
    assert plan["release_tree"] == TREE


def test_rejects_preflight_without_host_local_web_image(tmp_path: Path) -> None:
    web_market = "sha256:" + "1" * 64
    args, files = _fixture(tmp_path)
    pair = json.loads(files["primary_pair_receipt"].read_text(encoding="utf-8"))
    pair["schema"] = "market_pipeline_primary_release_pair/1.1"
    del pair["image_id"]
    pair["image_ids"] = {"bot": MARKET_IMAGE, "web": web_market}
    _json(files["primary_pair_receipt"], pair)
    args.expected_primary_pair_receipt_sha256 = _digest(files["primary_pair_receipt"])
    with pytest.raises(builder.PlanBuildError, match="preflight_receipt_invalid"):
        builder.build(args)


def test_accepts_primary_schema_as_installed_control_pair(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    document = json.loads(files["primary_pair_receipt"].read_text(encoding="utf-8"))
    _json(files["control_pair_receipt"], document)
    args.expected_control_pair_receipt_sha256 = _digest(files["control_pair_receipt"])
    plan, _receipt = builder.build(args)
    assert plan["release_sha"] == SHA
    assert plan["release_tree"] == TREE


def test_rejects_primary_control_pair_that_changes_product_authority(
    tmp_path: Path,
) -> None:
    args, files = _fixture(tmp_path)
    document = json.loads(files["primary_pair_receipt"].read_text(encoding="utf-8"))
    document["product_authority_changed"] = True
    _json(files["control_pair_receipt"], document)
    args.expected_control_pair_receipt_sha256 = _digest(files["control_pair_receipt"])
    with pytest.raises(builder.PlanBuildError, match="control_pair_receipt_schema_invalid"):
        builder.build(args)


def test_rejects_missing_preflight_receipt(tmp_path: Path) -> None:
    args, files = _fixture(tmp_path)
    files["preflight_receipt"].unlink()
    with pytest.raises(builder.PlanBuildError, match="preflight_receipt_unavailable"):
        builder.build(args)


def test_rejects_missing_exact_control_release(tmp_path: Path) -> None:
    args, _files = _fixture(tmp_path)
    manifest = Path(args.local_control_release_root) / builder.CONTROL_MANIFEST_NAME
    manifest.unlink()
    with pytest.raises(builder.PlanBuildError, match="control_manifest"):
        builder.build(args)
