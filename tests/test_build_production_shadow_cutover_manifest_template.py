from __future__ import annotations

import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
from core.docker_image_identity import (
    image_content_descriptor_from_archive_config,
)
from scripts import (
    build_production_shadow_cutover_manifest_template as MODULE,
)
from scripts import production_shadow_nginx_generation as NGINX
from scripts import produce_production_shadow_prepare_material as PREPARE
from scripts import render_three_site_production_shadow_role_compose as ROLE
from scripts.production_shadow_cutover_controller import (
    EXPECTED_TOPOLOGY,
    POLICY_FIELDS,
    validate_manifest,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
LEGACY_RELEASE_SHA = "b" * 40
CREATED_AT = "2026-07-28T01:02:03Z"
GIT = "/usr/bin/git"

BOT_COIN = b"""server {
    listen 80;
    server_name coin.362514.ir;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name coin.362514.ir;
    ssl_certificate /etc/letsencrypt/live/coin/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/coin/privkey.pem;
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
    }
}
"""
BOT_MINI = b"""server {
    listen 80;
    server_name mini-app.362514.ir;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name mini-app.362514.ir;
    ssl_certificate /etc/letsencrypt/live/mini/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mini/privkey.pem;
    root /srv/legacy-mini;
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
    }
    location / {
        try_files $uri /index.html;
    }
}
"""
WEBAPP = b"""upstream trading_bot_api {
    server 127.0.0.1:8000;
}
server {
    listen 80;
    server_name coin.gold-trade.ir;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name coin.gold-trade.ir;
    ssl_certificate /etc/letsencrypt/live/gold/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gold/privkey.pem;
    root /srv/trading-bot/current/mini_app_dist;
    location ~ ^/api/invitations/(lookup|validate)/ {
        proxy_pass http://trading_bot_api;
    }
    location /api/ {
        proxy_pass http://trading_bot_api;
    }
    location /api/ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Upgrade $http_upgrade;
    }
    location / {
        try_files $uri /index.html;
    }
}
"""


def secure_file(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def deterministic_tar(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.USTAR_FORMAT,
    ) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o600
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def image_archive(
    kind: str,
    release_sha: str,
) -> tuple[bytes, dict]:
    layer = f"layer:{kind}".encode("ascii")
    labels: dict[str, str] = {}
    if kind in {"app", "postgres"}:
        labels["org.opencontainers.image.revision"] = release_sha
    if kind == "postgres":
        labels["trading-bot.postgres.runtime-uid"] = "70"
        labels["trading-bot.postgres.runtime-gid"] = "70"
    config = {
        "architecture": "amd64",
        "os": "linux",
        "created": f"2026-07-28T00:00:0{len(kind)}Z",
        "rootfs": {
            "type": "layers",
            "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
        },
        "config": {
            "Labels": labels,
            "fixture-kind": kind,
        },
    }
    config_raw = canonical_json_bytes(config)
    config_digest = "sha256:" + hashlib.sha256(config_raw).hexdigest()
    config_name = config_digest.removeprefix("sha256:") + ".json"
    manifest = canonical_json_bytes(
        [
            {
                "Config": config_name,
                "RepoTags": [],
                "Layers": [f"{kind}/layer.tar"],
            }
        ]
    )
    archive = deterministic_tar(
        {
            "manifest.json": manifest,
            config_name: config_raw,
            f"{kind}/layer.tar": layer,
        }
    )
    descriptor, content_identity = (
        image_content_descriptor_from_archive_config(config)
    )
    return archive, {
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "archive_bytes": len(archive),
        "config_digest": config_digest,
        "content_descriptor": descriptor,
        "content_identity": content_identity,
    }


class CutoverFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.shadow_base = root / "shadow"
        self.control = root / "control"
        self.output = root / "output"
        self.artifacts = root / "release-artifacts"
        self.prepare = root / "prepare"
        self.rollback = root / "rollback"
        self.nginx = root / "nginx"
        for directory in (
            self.control,
            self.output,
            self.artifacts,
            self.prepare,
            self.rollback,
        ):
            directory.mkdir(mode=0o700, parents=True)
            directory.chmod(0o700)

        staging_release = root / "release-staging"
        staging_release.mkdir(mode=0o700)
        self._git("init", "--quiet", str(staging_release))
        self._git("-C", str(staging_release), "config", "user.name", "Test")
        self._git(
            "-C",
            str(staging_release),
            "config",
            "user.email",
            "test@example.invalid",
        )
        scripts = staging_release / "scripts"
        scripts.mkdir()
        for relative in (
            MODULE.HOST_AGENT_RELATIVE_PATH,
            MODULE.PHASE_VERIFIER_RELATIVE_PATH,
        ):
            source = MODULE.REPO_ROOT / relative
            destination = staging_release / relative
            shutil.copyfile(source, destination)
            destination.chmod(0o644)
        self.executor = (
            staging_release / MODULE.POSTCOMMIT_EXECUTOR_RELATIVE_PATH
        )
        secure_file(
            self.executor,
            b"#!/usr/bin/env python3\nraise SystemExit('fixture-only')\n",
            mode=0o644,
        )
        compose = (
            staging_release / MODULE.CANONICAL_COMPOSE_RELATIVE_PATH
        )
        compose.parent.mkdir(parents=True)
        shutil.copyfile(
            MODULE.REPO_ROOT / MODULE.CANONICAL_COMPOSE_RELATIVE_PATH,
            compose,
        )
        compose.chmod(0o644)
        self._git("-C", str(staging_release), "add", ".")
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-07-28T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-07-28T00:00:00Z",
        }
        self._git(
            "-C",
            str(staging_release),
            "commit",
            "--quiet",
            "-m",
            "fixture release",
            env=commit_env,
        )
        self.release_sha = self._git(
            "-C",
            str(staging_release),
            "rev-parse",
            "HEAD",
            capture=True,
        )
        self.release_tree_sha = self._git(
            "-C",
            str(staging_release),
            "rev-parse",
            "HEAD^{tree}",
            capture=True,
        )
        self.release_root = (
            self.shadow_base
            / OPERATION_ID
            / "releases"
            / self.release_sha
        )
        self.release_root.parent.mkdir(mode=0o700, parents=True)
        staging_release.rename(self.release_root)
        self.release_root.chmod(0o700)
        self._git(
            "-C",
            str(self.release_root),
            "checkout",
            "--quiet",
            "--detach",
            self.release_sha,
        )
        self.compose = (
            self.release_root / MODULE.CANONICAL_COMPOSE_RELATIVE_PATH
        )
        self.executor = (
            self.release_root / MODULE.POSTCOMMIT_EXECUTOR_RELATIVE_PATH
        )

        self.bundle = self.artifacts / "release.bundle"
        self._git(
            "-C",
            str(self.release_root),
            "bundle",
            "create",
            str(self.bundle),
            "HEAD",
        )
        self.bundle.chmod(0o600)
        self.images: dict[str, dict] = {}
        for kind in ("app", "postgres", "redis", "nginx"):
            archive, row = image_archive(kind, self.release_sha)
            secure_file(
                self.artifacts / f"{kind}-image.tar",
                archive,
            )
            self.images[kind] = row
        self.closure = {
            "schema": "production-shadow-release-artifact-closure-v2",
            "operation_id": OPERATION_ID,
            "release": {
                "commit_sha": self.release_sha,
                "tree_sha": self.release_tree_sha,
                "bundle": {
                    "filename": "release.bundle",
                    "sha256": hashlib.sha256(
                        self.bundle.read_bytes()
                    ).hexdigest(),
                    "bytes": self.bundle.stat().st_size,
                },
            },
            "images": self.images,
            "source_engine_observations": {
                kind: {
                    "image_id": self.images[kind]["config_digest"],
                    "informational_only": True,
                }
                for kind in self.images
            },
            "verified_image_contracts": {
                kind: {
                    "os": "linux",
                    "architecture": "amd64",
                    "repo_tags": [],
                    "oci_revision": (
                        self.release_sha
                        if kind in {"app", "postgres"}
                        else None
                    ),
                    **(
                        {
                            "runtime_user": {
                                "uid": 70,
                                "gid": 70,
                                "uid_label": (
                                    "trading-bot.postgres.runtime-uid"
                                ),
                                "gid_label": (
                                    "trading-bot.postgres.runtime-gid"
                                ),
                            }
                        }
                        if kind == "postgres"
                        else {}
                    ),
                }
                for kind in self.images
            },
            "constraints": {
                "source_backup_included": False,
                "role_material_included": False,
                "secrets_included": False,
                "network_transfer_performed": False,
                "container_runtime_changed": False,
            },
        }
        self.closure_path = self.artifacts / "closure-manifest.json"
        secure_file(
            self.closure_path,
            canonical_json_bytes(self.closure),
        )

        self.runtime_ids = {
            kind: self.images[kind]["config_digest"]
            for kind in self.images
        }
        self.prepare_metadata = self._prepare_materials()
        self.bot_rollback = self._rollback_attestation(
            "bot_fi",
            "1",
        )
        self.webapp_rollback = self._rollback_attestation(
            "webapp_fi",
            "6",
        )
        self._nginx_material()
        self.policy = self.control / "human-approval-policy.json"
        policy = {
            "schema": "three-site-human-approval-policy-v1",
            "policy_id": "33333333-3333-4333-8333-333333333333",
            "issuer": {
                "issuer_id": "witness-production-owner77",
                "key_id": "key-20260728",
                "operator": "owner77",
                "authenticator_id": "totp-owner77",
                "public_key": base64.b64encode(b"\x44" * 32).decode(),
            },
            "actions": [
                {
                    "action": "deploy_three_site_production",
                    "environments": ["production"],
                    "max_ttl_seconds": 86400,
                }
            ],
        }
        secure_file(self.policy, canonical_json_bytes(policy))
        self.postcommit_contract = (
            self.control / "postcommit-executor-contract.json"
        )
        postcommit = {
            "schema": MODULE.POSTCOMMIT_CONTRACT_SCHEMA,
            "release_sha": self.release_sha,
            "executor_path": (
                MODULE.POSTCOMMIT_EXECUTOR_RELATIVE_PATH.as_posix()
            ),
            "executor_sha256": hashlib.sha256(
                self.executor.read_bytes()
            ).hexdigest(),
            "required_journal_status": "forward-only-committed",
            "rollback_allowed": False,
            "operations": MODULE._expected_postcommit_operations(),
        }
        secure_file(
            self.postcommit_contract,
            canonical_json_bytes(postcommit),
        )

    @staticmethod
    def _git(
        *arguments: str,
        env: dict[str, str] | None = None,
        capture: bool = False,
    ) -> str:
        result = subprocess.run(
            [GIT, *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
            text=True,
        )
        return result.stdout.strip() if capture else ""

    def _prepare_materials(self) -> Path:
        roles: dict[str, dict] = {}
        controller_materials: dict[str, dict] = {}
        for index, role in enumerate(PREPARE.ALL_ROLES, 1):
            operation_manifest = f"{index:x}" * 64
            stage_attestation = f"{index + 4:x}" * 64
            if role == "witness":
                attestation = canonical_json_bytes(
                    {
                        "schema": "fixture-witness-public-v1",
                        "role": "witness",
                        "operation_id": OPERATION_ID,
                        "release_sha": self.release_sha,
                    }
                )
                entries = [
                    {
                        "archive_path": PREPARE.WITNESS_ATTESTATION_NAME,
                        "destination": (
                            "attestations/witness-public-prepare.json"
                        ),
                        "sha256": hashlib.sha256(attestation).hexdigest(),
                        "bytes": len(attestation),
                        "mode": "0600",
                    }
                ]
                internal = {
                    "schema": PREPARE.WITNESS_PREPARE_SCHEMA,
                    "operation_id": OPERATION_ID,
                    "release_sha": self.release_sha,
                    "operation_manifest_sha256": operation_manifest,
                    "stage_attestation_sha256": stage_attestation,
                    "role": role,
                    "runtime_image_ids": {},
                    "entries": entries,
                    "required_env_keys": [],
                }
                internal_raw = canonical_json_bytes(internal)
                files = {
                    PREPARE.WITNESS_MANIFEST_NAME: internal_raw,
                    PREPARE.WITNESS_ATTESTATION_NAME: attestation,
                }
            else:
                compose_raw = ROLE.canonical_role_compose_bytes(
                    {
                        "name": f"fixture-{role}",
                        "services": {},
                    }
                )
                values = {
                    PREPARE.IMAGE_ENV_BY_KIND[kind]: self.runtime_ids[
                        kind
                    ]
                    for kind in PREPARE.IMAGE_KINDS
                }
                env_raw = ROLE.canonical_role_env_bytes(
                    values,
                    required_names=frozenset(values),
                )
                ca_raw = (
                    b"-----BEGIN CERTIFICATE-----\n"
                    + base64.b64encode(f"ca:{role}".encode("ascii"))
                    + b"\n-----END CERTIFICATE-----\n"
                )
                payloads = {
                    "role-compose.yml": compose_raw,
                    "runtime.env.role": env_raw,
                    "ca.crt": ca_raw,
                }
                internal = {
                    "schema": (
                        PREPARE.WA_IR_FINAL_PREPARE_SCHEMA
                        if role == "webapp_ir"
                        else PREPARE.FI_FINAL_PREPARE_SCHEMA
                    ),
                    "operation_id": OPERATION_ID,
                    "release_sha": self.release_sha,
                    "operation_manifest_sha256": operation_manifest,
                    "stage_attestation_sha256": stage_attestation,
                    "role": role,
                    "runtime_image_ids": self.runtime_ids,
                    "entries": PREPARE._manifest_entries(
                        payloads,
                        destinations=PREPARE._role_destinations(role),
                    ),
                    "required_env_keys": sorted(values),
                }
                internal_raw = canonical_json_bytes(internal)
                files = {
                    PREPARE.FINAL_PREPARE_MANIFEST_NAME: internal_raw,
                    **payloads,
                }
            archive = PREPARE._tar_bytes(files)
            filename = PREPARE.ROLE_ARCHIVE_NAMES[role]
            secure_file(self.prepare / filename, archive)
            row = {
                "filename": filename,
                "sha256": hashlib.sha256(archive).hexdigest(),
                "bytes": len(archive),
                "format": PREPARE.ROLE_FORMATS[role],
                "transport": PREPARE.ROLE_TRANSPORTS[role],
                "internal_manifest_sha256": hashlib.sha256(
                    internal_raw
                ).hexdigest(),
                "stage_operation_manifest_sha256": operation_manifest,
                "stage_attestation_sha256": stage_attestation,
            }
            roles[role] = row
            controller_materials[role] = {
                "sha256": row["sha256"],
                "bytes": row["bytes"],
                "format": row["format"],
                "transport": row["transport"],
            }
        metadata = {
            "schema": PREPARE.SET_SCHEMA,
            "operation_id": OPERATION_ID,
            "release_sha": self.release_sha,
            "canonical_compose_sha256": hashlib.sha256(
                self.compose.read_bytes()
            ).hexdigest(),
            "dr_ca_sha256": "a" * 64,
            "dr_tls_attestation_sha256": "b" * 64,
            "dr_tls_attested_at_epoch": 1_784_000_000,
            "roles": roles,
            "controller_bindings": {
                "role_materials": controller_materials,
                "role_runtime_image_ids": {
                    role: self.runtime_ids
                    for role in PREPARE.DOCKER_ROLES
                },
            },
            "activation_secrets_included": False,
            "precommit_manifest_bound": False,
        }
        path = self.prepare / "prepare-metadata.json"
        secure_file(path, canonical_json_bytes(metadata))
        return path

    def _rollback_attestation(self, role: str, seed: str) -> Path:
        fields = {
            "schema": "production-shadow-legacy-rollback-attestation-v1",
            "status": "verified",
            "operation_id": OPERATION_ID,
            "release_sha": self.release_sha,
            "legacy_release_sha": LEGACY_RELEASE_SHA,
            "role": role,
            "rollback_closure_sha256": seed * 64,
            "legacy_redis_rollback_sha256": (
                f"{int(seed, 16) + 1:x}" * 64
            )[:64],
            "sha256sums_sha256": (
                f"{int(seed, 16) + 2:x}" * 64
            )[:64],
            "backup_manifest_sha256": (
                f"{int(seed, 16) + 3:x}" * 64
            )[:64],
            "backup_artifact_set_sha256": (
                f"{int(seed, 16) + 4:x}" * 64
            )[:64],
            "backup_stamp": "20260728T000000Z",
            "database_restore_smoke_passed": True,
            "database_restore_smoke_table_count": 12,
            "sealed_file_count": (
                len(MODULE.rollback_attestation.ROLE_SEALED_FILES[role])
                + 1
            ),
            "backup_artifact_count": 4,
            "source_mutated": False,
            "production_contacted": True,
        }
        path = self.rollback / f"{role}.json"
        secure_file(path, canonical_json_bytes(fields))
        return path

    def _nginx_material(self) -> None:
        sources = self.root / "nginx-sources"
        sources.mkdir(mode=0o700)
        bot_coin = sources / "bot-coin.conf"
        bot_mini = sources / "bot-mini.conf"
        webapp = sources / "webapp.conf"
        secure_file(bot_coin, BOT_COIN)
        secure_file(bot_mini, BOT_MINI)
        secure_file(webapp, WEBAPP)
        source_rows = NGINX.default_sources(
            bot_coin_source=bot_coin,
            bot_mini_source=bot_mini,
            bot_mini_legacy_root="/srv/legacy-mini",
            webapp_source=webapp,
        )
        NGINX.produce_generations(
            operation_id=OPERATION_ID,
            release_sha=self.release_sha,
            release_tree_sha=self.release_tree_sha,
            shadow_release_root=self.release_root,
            role_api_ports={"bot_fi": 18001, "webapp_fi": 18002},
            sources=source_rows,
            output_root=self.nginx,
            owner_uid=0,
        )
        self.nginx_aggregate = (
            self.nginx / "nginx-generation-aggregate.json"
        )

    def args(self) -> list[str]:
        return [
            "--campaign-id",
            CAMPAIGN_ID,
            "--operation-id",
            OPERATION_ID,
            "--created-at",
            CREATED_AT,
            "--legacy-release-sha",
            LEGACY_RELEASE_SHA,
            "--release-closure",
            str(self.closure_path),
            "--release-root",
            str(self.release_root),
            "--prepare-metadata",
            str(self.prepare_metadata),
            "--canonical-compose",
            str(self.compose),
            "--bot-rollback-attestation",
            str(self.bot_rollback),
            "--webapp-rollback-attestation",
            str(self.webapp_rollback),
            "--nginx-aggregate",
            str(self.nginx_aggregate),
            "--human-approval-policy",
            str(self.policy),
            "--postcommit-executor-contract",
            str(self.postcommit_contract),
            "--output-directory",
            str(self.output),
        ]


@unittest.skipUnless(os.geteuid() == 0, "root-only builder tests")
class CutoverManifestTemplateBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.shadow_patch = mock.patch.object(
            MODULE,
            "SHADOW_ROOT_BASE",
            self.root / "shadow",
        )
        self.shadow_patch.start()

        def accept_fixture_release(
            operation_id: str,
            release_sha: str,
            value: Path,
        ) -> Path:
            expected = (
                self.root
                / "shadow"
                / operation_id
                / "releases"
                / release_sha
            )
            if value != expected:
                raise NGINX.NginxGenerationError(
                    "fixture release root differs"
                )
            return value

        self.nginx_patch = mock.patch.object(
            NGINX,
            "_shadow_release_root",
            side_effect=accept_fixture_release,
        )
        self.nginx_patch.start()
        self.fixture = CutoverFixture(self.root)

    def tearDown(self) -> None:
        self.nginx_patch.stop()
        self.shadow_patch.stop()
        self.temporary.cleanup()

    def run_main(self, arguments: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = MODULE.main(arguments)
        return status, json.loads(output.getvalue())

    def test_plan_apply_and_exact_retry_are_fully_bound(self) -> None:
        status, first = self.run_main(self.fixture.args())
        second_status, second = self.run_main(self.fixture.args())
        self.assertEqual((status, second_status), (0, 0))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "planned")
        self.assertFalse(first["output_mutated"])
        self.assertFalse(first["network_io"])
        self.assertFalse(first["docker_contacted"])
        output = self.fixture.output / MODULE.OUTPUT_FILENAME
        self.assertFalse(output.exists())

        apply_args = self.fixture.args() + [
            "--apply",
            "--confirm",
            first["required_confirmation"],
        ]
        status, result = self.run_main(apply_args)
        self.assertEqual(status, 0, result)
        self.assertEqual(result["publication"], "created")
        self.assertTrue(result["output_mutated"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(output.read_bytes(), canonical_json_bytes(manifest))
        self.assertEqual(
            manifest["artifacts"]["cutover_approval_sha256"],
            "0" * 64,
        )
        provisional = json.loads(canonical_json_bytes(manifest))
        provisional["artifacts"]["cutover_approval_sha256"] = "1" * 64
        validate_manifest(provisional)
        self.assertEqual(manifest["topology"], EXPECTED_TOPOLOGY)
        self.assertEqual(
            manifest["policy"],
            {field: True for field in POLICY_FIELDS},
        )
        self.assertEqual(
            manifest["artifacts"]["host_agent_contract_sha256"],
            MODULE.HOST_AGENT_CONTRACT_SHA256,
        )
        self.assertEqual(
            manifest["artifacts"][
                "postcommit_executor_contract_sha256"
            ],
            hashlib.sha256(
                self.fixture.postcommit_contract.read_bytes()
            ).hexdigest(),
        )

        retry_status, retry = self.run_main(apply_args)
        self.assertEqual(retry_status, 0)
        self.assertEqual(retry["publication"], "reused")
        self.assertFalse(retry["output_mutated"])

    def test_tampered_actual_image_and_nginx_archive_fail_closed(self) -> None:
        image = self.fixture.artifacts / "redis-image.tar"
        original = image.read_bytes()
        image.write_bytes(original + b"tamper")
        image.chmod(0o600)
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("release bundle or image", result["error"])
        image.write_bytes(original)
        image.chmod(0o600)

        nginx_archive = (
            self.fixture.nginx
            / "webapp_fi"
            / "nginx-generations.tar"
        )
        nginx_archive.write_bytes(
            nginx_archive.read_bytes() + b"tamper"
        )
        nginx_archive.chmod(0o600)
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("Nginx role material", result["error"])

    def test_prepare_rollback_policy_and_postcommit_drift_fail_closed(
        self,
    ) -> None:
        metadata = json.loads(
            self.fixture.prepare_metadata.read_text(encoding="utf-8")
        )
        metadata["controller_bindings"]["role_runtime_image_ids"][
            "bot_fi"
        ]["app"] = "sha256:" + "f" * 64
        secure_file(
            self.fixture.prepare_metadata,
            canonical_json_bytes(metadata),
        )
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("runtime image inventory", result["error"])

        self.fixture.prepare_metadata = self.fixture._prepare_materials()
        rollback = json.loads(
            self.fixture.bot_rollback.read_text(encoding="utf-8")
        )
        rollback["role"] = "webapp_fi"
        secure_file(
            self.fixture.bot_rollback,
            canonical_json_bytes(rollback),
        )
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("rollback attestation differs", result["error"])

        self.fixture.bot_rollback = self.fixture._rollback_attestation(
            "bot_fi",
            "1",
        )
        policy = json.loads(
            self.fixture.policy.read_text(encoding="utf-8")
        )
        policy["private_key"] = "forbidden"
        secure_file(self.fixture.policy, canonical_json_bytes(policy))
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("public policy is invalid", result["error"])

        del policy["private_key"]
        secure_file(self.fixture.policy, canonical_json_bytes(policy))
        contract = json.loads(
            self.fixture.postcommit_contract.read_text(encoding="utf-8")
        )
        contract["operations"][0]["roles"].reverse()
        secure_file(
            self.fixture.postcommit_contract,
            canonical_json_bytes(contract),
        )
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("POSTCOMMIT_SPECS", result["error"])

    def test_symlink_hardlink_mode_and_read_drift_are_rejected(self) -> None:
        target = self.fixture.control / "policy-target.json"
        self.fixture.policy.rename(target)
        self.fixture.policy.symlink_to(target)
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("symlink", result["error"])
        self.fixture.policy.unlink()
        target.rename(self.fixture.policy)

        hardlink = self.fixture.control / "policy-hardlink.json"
        os.link(self.fixture.policy, hardlink)
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("link", result["error"])
        hardlink.unlink()

        self.fixture.output.chmod(0o755)
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("mode 0700", result["error"])
        self.fixture.output.chmod(0o700)

        drift = self.fixture.control / "drift.json"
        secure_file(drift, b'{"value":1}')
        real_read = os.read
        changed = False

        def mutate_after_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            content = real_read(descriptor, size)
            if content and not changed:
                changed = True
                os.utime(drift, ns=(2_000_000_000, 2_000_000_000))
            return content

        with mock.patch.object(MODULE.os, "read", mutate_after_read):
            with self.assertRaisesRegex(
                MODULE.CutoverManifestTemplateError,
                "changed while being read",
            ):
                MODULE._read_stable_file(
                    drift,
                    label="drift fixture",
                    owner_uid=0,
                    allowed_modes=frozenset({0o600}),
                    maximum=1024,
                )

    def test_confirmation_root_and_existing_conflict_fail_closed(self) -> None:
        status, plan = self.run_main(self.fixture.args())
        self.assertEqual(status, 0)
        status, result = self.run_main(
            self.fixture.args()
            + ["--apply", "--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("apply requires", result["error"])
        status, result = self.run_main(
            self.fixture.args() + ["--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("valid only", result["error"])

        output = self.fixture.output / MODULE.OUTPUT_FILENAME
        secure_file(output, b"{}")
        status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("overwrite", result["error"])
        self.assertEqual(output.read_bytes(), b"{}")

        output.unlink()
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000):
            status, result = self.run_main(self.fixture.args())
        self.assertEqual(status, 1)
        self.assertIn("must run as root", result["error"])
        self.assertEqual(
            plan["cutover_approval_sha256"],
            "0" * 64,
        )


if __name__ == "__main__":
    unittest.main()
