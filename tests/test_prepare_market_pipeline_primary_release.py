from __future__ import annotations

import json
import io
import os
import socket
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import prepare_market_pipeline_primary_release as primary
from scripts import backup_market_pipeline_archive as backup
from scripts.prepare_market_pipeline_release import parse_env
from tests.test_prepare_market_pipeline_release import (
    BOT_IMAGE_ID,
    IMAGE_ID,
    RELEASE_SHA,
    RELEASE_TREE,
    WEB_IMAGE_ID,
    _bot_values,
    _web_values,
    _write_source,
)


class PrepareMarketPipelinePrimaryReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-primary-pair-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.web_source = self.root / "web.source.env"
        self.bot_source = self.root / "bot.source.env"
        self.web_env = self.root / "web.primary.env"
        self.bot_env = self.root / "bot.primary.env"
        self.receipt = self.root / "receipt.json"
        web_values = _web_values()
        web_values.update(
            {
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": primary.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": primary.AUTHORIZED_BACKFILL_SOURCE_CODES,
                "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES": "250000",
            }
        )
        _write_source(self.web_source, web_values)
        _write_source(self.bot_source, _bot_values())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _arguments(self) -> dict[str, object]:
        return {
            "web_source": self.web_source,
            "bot_source": self.bot_source,
            "web_env": self.web_env,
            "bot_env": self.bot_env,
            "receipt": self.receipt,
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "project_name": "market-private-pipeline-stage13-shadow",
        }

    def test_pair_is_primary_bound_and_does_not_claim_product_cutover(self) -> None:
        document = primary.render_pair(**self._arguments())

        for output in (self.web_env, self.bot_env):
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            values = parse_env(output, secure_input=True)
            self.assertEqual(values["MARKET_PIPELINE_FEED_MODE"], "PRIVATE_PRIMARY")
            self.assertEqual(values["MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"], "1")
            self.assertEqual(
                values["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"],
                "PRIVATE_PRIMARY",
            )
        self.assertFalse(document["product_authority_changed"])
        self.assertFalse(document["legacy_retirement_authorized"])
        self.assertEqual(
            document["roles"]["bot"]["product_snapshot_root"],
            "/srv/trading-bot/production-data/market-pipeline/snapshots",
        )
        self.assertEqual(
            document["roles"]["web"]["product_snapshot_root"],
            "/srv/trading-bot/market-data-production/snapshots",
        )
        self.assertNotIn("/srv/trading-bot/secure/market", self.receipt.read_text())
        verified = primary.verify_pair(**self._arguments())
        self.assertEqual(verified, document)

    def test_primary_pair_accepts_role_local_content_ids(self) -> None:
        arguments = self._arguments()
        arguments.update(
            image_id=None,
            web_image_id=WEB_IMAGE_ID,
            bot_image_id=BOT_IMAGE_ID,
        )

        document = primary.render_pair(**arguments)

        self.assertEqual(document["schema"], primary.ROLE_IMAGE_SCHEMA)
        self.assertEqual(
            document["image_ids"], {"web": WEB_IMAGE_ID, "bot": BOT_IMAGE_ID}
        )
        self.assertEqual(
            parse_env(self.web_env, secure_input=True)["MARKET_PIPELINE_IMAGE"],
            WEB_IMAGE_ID,
        )
        self.assertEqual(
            parse_env(self.bot_env, secure_input=True)["MARKET_PIPELINE_IMAGE"],
            BOT_IMAGE_ID,
        )
        self.assertEqual(primary.verify_pair(**arguments), document)

    def test_derive_source_strips_release_values_and_adds_only_web_key_path(self) -> None:
        rendered = {
            **_web_values(),
            "MARKET_PIPELINE_IMAGE": IMAGE_ID,
            "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
            "MARKET_PIPELINE_MODE": "live",
            "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-stage13-shadow",
            "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
            "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
            "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        }
        rendered.pop("MARKET_RESEARCH_ENCRYPTION_KEY_FILE")
        old_env = self.root / "old-web.env"
        derived = self.root / "derived-web.source.env"
        _write_source(old_env, rendered)

        result = primary.derive_source(
            role="web",
            rendered_env=old_env,
            source_env=derived,
            research_key_file=Path(
                "/srv/trading-bot/secure/market/research-archive.key"
            ),
            capture_backfill_not_before_utc="2026-08-25T09:33:00Z",
            capture_backfill_max_messages=1_000_000,
        )

        values = parse_env(derived, secure_input=True)
        self.assertFalse(set(primary.DYNAMIC_VALUES).intersection(values))
        self.assertEqual(
            values["MARKET_RESEARCH_ENCRYPTION_KEY_FILE"],
            "/srv/trading-bot/secure/market/research-archive.key",
        )
        self.assertEqual(
            values["MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"],
            "2026-08-25T09:33:00Z",
        )
        self.assertEqual(values["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"], "1000000")
        self.assertEqual(
            values["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"],
            "MELTED_PRIMARY_FLOW,GROUP_1,GROUP_2",
        )
        self.assertTrue(result["capture_backfill_boundary_added"])
        self.assertFalse(result["secret_values_read"])

    def test_dynamic_authority_values_are_forbidden_in_source(self) -> None:
        values = parse_env(self.web_source, secure_input=True)
        values["MARKET_PIPELINE_FEED_MODE"] = "PRIVATE_PRIMARY"
        _write_source(self.web_source, values)

        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_source_contains_dynamic_values",
        ):
            primary.render_pair(**self._arguments())
        self.assertFalse(self.web_env.exists())

    def test_derive_source_rejects_zero_backfill_cap(self) -> None:
        rendered = _web_values()
        rendered.update(
            {
                "MARKET_PIPELINE_IMAGE": "market-pipeline@sha256:" + "a" * 64,
                "MARKET_PIPELINE_RELEASE_SHA": "b" * 40,
                "MARKET_PIPELINE_MODE": "live",
                "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-test",
                "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
                "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
                "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
            }
        )
        rendered.pop("MARKET_RESEARCH_ENCRYPTION_KEY_FILE")
        old_env = self.root / "old-web-zero.env"
        derived = self.root / "derived-web-zero.source.env"
        _write_source(old_env, rendered)
        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_backfill_max_messages_invalid",
        ):
            primary.derive_source(
                role="web",
                rendered_env=old_env,
                source_env=derived,
                research_key_file=Path(
                    "/srv/trading-bot/secure/market/research-archive.key"
                ),
                capture_backfill_not_before_utc="2026-08-25T09:33:00Z",
                capture_backfill_max_messages=0,
            )
        self.assertFalse(derived.exists())

    def test_cross_host_topology_drift_fails_before_output(self) -> None:
        values = _bot_values()
        values["MARKET_WEB_PRIVATE_IP"] = "10.240.1.21"
        _write_source(self.bot_source, values)

        with self.assertRaisesRegex(Exception, "cross_role_topology_mismatch"):
            primary.render_pair(**self._arguments())
        self.assertFalse(self.web_env.exists())
        self.assertFalse(self.bot_env.exists())

    def test_wrong_or_missing_owner_authorized_backfill_scope_fails_closed(self) -> None:
        values = parse_env(self.web_source, secure_input=True)
        values["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"] = "GROUP_1,GROUP_2"
        _write_source(self.web_source, values)
        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_authorized_backfill_contract_invalid",
        ):
            primary.render_pair(**self._arguments())
        self.assertFalse(self.web_env.exists())

    def test_receipt_or_output_tampering_is_rejected(self) -> None:
        primary.render_pair(**self._arguments())
        values = parse_env(self.bot_env, secure_input=True)
        values["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"] = "PRIVATE_SHADOW"
        _write_source(self.bot_env, values)

        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_output_content_mismatch",
        ):
            primary.verify_pair(**self._arguments())

        receipt = json.loads(self.receipt.read_text())
        receipt["legacy_retirement_authorized"] = True
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")
        self.receipt.chmod(0o600)
        with self.assertRaisesRegex(
            primary.PrimaryReleaseError,
            "primary_release_receipt_identity_invalid",
        ):
            primary.verify_pair(**self._arguments())



class ManagedWebMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="managed-web-metadata-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.original = self.root / "original-role.env"
        self.managed = self.root / "managed"
        self.managed.mkdir(mode=0o700)
        self.output = self.managed / "web-managed.env"
        self.receipt = self.managed / "web-managed-receipt.json"
        self.source_values = primary._rendered(
            {**_web_values(),
             "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": primary.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
             "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": primary.AUTHORIZED_BACKFILL_SOURCE_CODES,
             "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES": "250000",
             "MARKET_CUSTOM_RETAINED_SETTING": "retained"},
            release_sha=RELEASE_SHA, image_id="market_pipeline_release:legacy-source-tag",
            project_name="market-private-pipeline-primary",
        )
        _write_source(self.original, self.source_values)
        trusted = backup._trusted_env_parent
        ambient = Path(tempfile.gettempdir()).stat()
        def fixture_parent(info):
            return ((info.st_dev, info.st_ino) == (ambient.st_dev, ambient.st_ino)
                    or trusted(info))
        self.guards = [
            mock.patch.object(backup, "_trusted_env_parent", side_effect=fixture_parent),
            mock.patch.object(primary, "_digest", side_effect=AssertionError("env_digest_forbidden")),
            mock.patch.object(primary, "sha256", side_effect=AssertionError("payload_hash_forbidden")),
            mock.patch.object(backup, "file_digest", side_effect=AssertionError("env_hash_forbidden")),
            mock.patch.object(backup.subprocess, "run", side_effect=AssertionError("host_command_forbidden")),
            mock.patch.object(backup.subprocess, "Popen", side_effect=AssertionError("host_command_forbidden")),
            mock.patch.object(socket, "socket", side_effect=AssertionError("network_forbidden")),
            mock.patch.object(socket, "create_connection", side_effect=AssertionError("network_forbidden")),
        ]
        for guard in self.guards:
            guard.start()

    def tearDown(self) -> None:
        for guard in reversed(self.guards):
            guard.stop()
        self.temporary.cleanup()

    def arguments(self):
        return dict(
            rendered_env=self.original, web_env=self.output, receipt=self.receipt,
            config_release_sha=RELEASE_SHA, config_release_tree=RELEASE_TREE,
            source_image_id=WEB_IMAGE_ID, source_image_release_sha="e" * 40,
            source_image_release_tree="f" * 40, source_image_input_signature="d" * 64,
            source_evidence_sha256="9" * 64,
        )

    def test_managed_render_preserves_every_setting_except_source_image_and_original(self):
        before = self.original.stat()
        original = self.original.read_bytes()
        real_open = os.open
        opened = []
        def guarded_open(target, *args, **kwargs):
            # No configured secret path or session target may be opened.
            if str(target).startswith("/srv/trading-bot/"):
                raise AssertionError("secret_target_opened")
            opened.append(target)
            return real_open(target, *args, **kwargs)
        with mock.patch.object(primary.os, "open", side_effect=guarded_open), \
                mock.patch.object(primary.os, "chmod", side_effect=AssertionError("chmod_forbidden")), \
                mock.patch.object(Path, "mkdir", side_effect=AssertionError("mkdir_forbidden")):
            document = primary.render_web_metadata(**self.arguments())
            self.assertEqual(primary.verify_web_metadata(**self.arguments()), document)
        self.assertEqual(self.original.read_bytes(), original)
        self.assertEqual(backup._file_identity(self.original.stat()), backup._file_identity(before))
        values = parse_env(self.output, secure_input=True)
        self.assertEqual(values, {**self.source_values, "MARKET_PIPELINE_IMAGE": WEB_IMAGE_ID})
        self.assertEqual(document["changed_keys"], ["MARKET_PIPELINE_IMAGE"])
        self.assertTrue(document["original_retained"])
        self.assertFalse(document["runtime_verified_by_tool"])
        self.assertFalse(document["runtime_mutated"])
        self.assertFalse(document["deployment_eligible"])
        self.assertFalse(document["product_authority_changed"])
        self.assertEqual(document["caller_source_evidence_sha256"], "9" * 64)
        self.assertEqual(document['configuration_source'], {'release_sha': RELEASE_SHA, 'release_tree': RELEASE_TREE,
            'release_field_verified': True, 'historical_entire_env_contents_verified': False})
        self.assertEqual(document['source_image'], {'image_id': WEB_IMAGE_ID, 'oci_revision': 'e' * 40,
            'oci_tree': 'f' * 40, 'image_input_signature': 'd' * 64})
        self.assertNotEqual(document['configuration_source']['release_sha'], document['source_image']['oci_revision'])
        self.assertNotIn("/srv/trading-bot/secure/market", self.receipt.read_text())
        self.assertEqual(set(self.managed.iterdir()), {self.output, self.receipt})

    def test_normal_backup_validator_remains_strict_for_original_and_accepts_managed_source(self):
        primary.render_web_metadata(**self.arguments())
        with self.assertRaisesRegex(backup.BackupError, "source_image_identity_invalid"):
            backup.validate_release_env(self.original, release_sha="f" * 40,
                image_id="sha256:" + "1" * 64, allow_target_identity_mismatch=True)
        accepted = backup.validate_release_env(self.output, release_sha="f" * 40,
            image_id="sha256:" + "1" * 64, allow_target_identity_mismatch=True)
        self.assertEqual(accepted["MARKET_PIPELINE_IMAGE"], WEB_IMAGE_ID)
        self.assertEqual(accepted["MARKET_PIPELINE_RELEASE_SHA"], RELEASE_SHA)

    def test_source_baseline_mismatch_or_bad_public_identity_refuses_before_outputs(self):
        for override in (
            {"config_release_sha": "1" * 40}, {"source_image_id": "candidate:target-tag"},
            {"config_release_tree": "bad"}, {"source_image_release_sha": "bad"},
            {"source_image_release_tree": "bad"}, {"source_image_input_signature": "bad"},
            {"source_evidence_sha256": "bad"},
            {"config_release_sha": "e" * 40, "source_image_release_sha": RELEASE_SHA},
            {"config_release_sha": True}, {"config_release_tree": []},
            {"source_image_release_sha": False}, {"source_image_release_tree": {}},
        ):
            with self.subTest(override=override):
                with self.assertRaises(primary.PrimaryReleaseError):
                    primary.render_web_metadata(**{**self.arguments(), **override})
                self.assertFalse(self.output.exists())
                self.assertFalse(self.receipt.exists())

    def test_source_authority_drift_or_noop_does_not_expand_repair(self):
        for key, value in (
            ("MARKET_PIPELINE_FEED_MODE", "PRIVATE_SHADOW"),
            ("MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY", "0"),
            ("MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE", "PRIVATE_SHADOW"),
            ("MARKET_PIPELINE_MODE", "off"),
            ("MARKET_PIPELINE_PROJECT_NAME", "invalid.project"),
            ("MARKET_PIPELINE_IMAGE", WEB_IMAGE_ID),
        ):
            with self.subTest(key=key):
                _write_source(self.original, {**self.source_values, key: value})
                with self.assertRaises(primary.PrimaryReleaseError):
                    primary.render_web_metadata(**self.arguments())
                self.assertFalse(self.output.exists())
                self.assertFalse(self.receipt.exists())

    def test_existing_partial_output_or_receipt_is_never_adopted_or_overwritten(self):
        for path in (self.output, self.receipt):
            with self.subTest(path=path.name):
                path.write_bytes(b"owned-partial-output")
                path.chmod(0o600)
                with self.assertRaisesRegex(primary.PrimaryReleaseError, "output_exists"):
                    primary.render_web_metadata(**self.arguments())
                self.assertEqual(path.read_bytes(), b"owned-partial-output")
                path.unlink()

    def test_partial_stage_without_receipt_cannot_resume_or_verify(self):
        real_write = primary._atomic_write
        def fail_receipt(path, *args, **kwargs):
            if path == self.receipt:
                raise OSError("private write failure detail")
            return real_write(path, *args, **kwargs)
        with mock.patch.object(primary, "_atomic_write", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                primary.render_web_metadata(**self.arguments())
        self.assertTrue(self.output.exists())
        self.assertFalse(self.receipt.exists())
        with self.assertRaisesRegex(primary.PrimaryReleaseError, "output_exists"):
            primary.render_web_metadata(**self.arguments())
        with self.assertRaises(backup.BackupError):
            primary.verify_web_metadata(**self.arguments())

    def test_drift_before_receipt_publication_refuses_completed_receipt(self):
        real_write = primary._atomic_write
        for target in (self.original, self.output):
            with self.subTest(target=target.name):
                _write_source(self.original, self.source_values)
                def drift_before_receipt(path, *args, **kwargs):
                    if path == self.receipt:
                        target.write_bytes(target.read_bytes())
                    return real_write(path, *args, **kwargs)
                with mock.patch.object(primary, "_atomic_write", side_effect=drift_before_receipt):
                    with self.assertRaisesRegex(backup.BackupError, "role_env_drifted"):
                        primary.render_web_metadata(**self.arguments())
                self.assertTrue(self.output.exists())
                self.assertFalse(self.receipt.exists())
                self.output.unlink()

    def test_concurrent_output_publication_is_not_replaced(self):
        real_link = os.link
        def create_foreign_output(source, destination, **kwargs):
            if destination == self.output:
                destination.write_bytes(b"foreign-owned-output")
                destination.chmod(0o600)
            return real_link(source, destination, **kwargs)
        with mock.patch.object(primary.os, "link", side_effect=create_foreign_output):
            with self.assertRaises(FileExistsError):
                primary.render_web_metadata(**self.arguments())
        self.assertEqual(self.output.read_bytes(), b"foreign-owned-output")
        self.assertFalse(self.receipt.exists())
        self.assertEqual(set(self.managed.iterdir()), {self.output})

    def test_verify_refuses_output_or_source_rewrite_even_if_bytes_restored(self):
        for target in (self.original, self.output):
            with self.subTest(target=target.name):
                _write_source(self.original, self.source_values)
                primary.render_web_metadata(**self.arguments())
                target.write_bytes(target.read_bytes())
                with self.assertRaisesRegex(primary.PrimaryReleaseError, "receipt_identity_mismatch"):
                    primary.verify_web_metadata(**self.arguments())
                self.output.unlink()
                self.receipt.unlink()

    def test_verify_receipt_requires_exact_json_types_and_no_extra_fields(self):
        primary.render_web_metadata(**self.arguments())
        original = json.loads(self.receipt.read_text())
        reflected = json.loads(json.dumps(original))
        reflected['source_image']['oci_revision'] = RELEASE_SHA
        wrong_config = json.loads(json.dumps(original))
        wrong_config['configuration_source']['release_field_verified'] = 1
        for tampered in ({**original, "runtime_mutated": 0}, {**original, "extra": True},
                         {**original, 'schema': 'market_pipeline_primary_web_managed_metadata/1.0'},
                         reflected, wrong_config):
            with self.subTest(tampered=tampered.keys()):
                self.receipt.write_text(json.dumps(tampered))
                with self.assertRaisesRegex(primary.PrimaryReleaseError, "receipt_identity_mismatch"):
                    primary.verify_web_metadata(**self.arguments())

    def test_verify_stale_configuration_identity_cannot_be_reinterpreted_as_image_revision(self):
        primary.render_web_metadata(**self.arguments())
        with self.assertRaisesRegex(primary.PrimaryReleaseError, 'source_release_mismatch'):
            primary.verify_web_metadata(**{**self.arguments(), 'config_release_sha': 'e' * 40,
                                           'source_image_release_sha': RELEASE_SHA})
        with self.assertRaisesRegex(primary.PrimaryReleaseError, 'receipt_identity_mismatch'):
            primary.verify_web_metadata(**{**self.arguments(), 'config_release_tree': '1' * 40})

    def test_source_symlink_or_hardlink_and_unsafe_destination_refuse(self):
        linked = self.root / "linked-source.env"
        linked.symlink_to(self.original)
        with self.assertRaises(backup.BackupError):
            primary.render_web_metadata(**{**self.arguments(), "rendered_env": linked})
        linked.unlink()
        os.link(self.original, linked)
        with self.assertRaises(backup.BackupError):
            primary.render_web_metadata(**self.arguments())
        linked.unlink()
        self.managed.chmod(0o755)
        with self.assertRaisesRegex(primary.PrimaryReleaseError, "parent_invalid"):
            primary.render_web_metadata(**self.arguments())
        self.assertEqual(self.managed.stat().st_mode & 0o777, 0o755)
        self.assertFalse(self.output.exists())

    def test_source_output_alias_or_missing_managed_parent_refuses(self):
        with self.assertRaisesRegex(primary.PrimaryReleaseError, "paths_invalid"):
            primary.render_web_metadata(**{**self.arguments(), "web_env": self.original})
        self.managed.rmdir()
        with self.assertRaisesRegex(primary.PrimaryReleaseError, "parent_invalid"):
            primary.render_web_metadata(**self.arguments())
        self.assertFalse(self.managed.exists())

    def cli_args(self, command):
        args = ["--confirm", primary.CONFIRMATION, command]
        for key, value in self.arguments().items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        return args

    def test_metadata_cli_success_is_explicitly_staging_only(self):
        for command in ("render-web-metadata", "verify-web-metadata"):
            output = io.StringIO()
            with mock.patch.object(primary.sys, "stdout", output):
                self.assertEqual(primary.main(self.cli_args(command)), 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result["runtime_verified_by_tool"])
            self.assertFalse(result["runtime_mutated"])
            self.assertFalse(result["deployment_eligible"])
            self.assertNotIn(str(self.root), output.getvalue())

    def test_metadata_cli_refusals_never_expose_private_exception_text(self):
        for failure in (
            OSError("private detail /protected/path"), ValueError("private detail /protected/path"),
            primary.PrimaryReleaseError("private detail /protected/path"),
            backup.BackupError("backup_role_env_drifted"),
        ):
            output, diagnostics = io.StringIO(), io.StringIO()
            with mock.patch.object(primary, "render_web_metadata", side_effect=failure), \
                    mock.patch.object(primary.sys, "stdout", output), \
                    mock.patch.object(primary.sys, "stderr", diagnostics):
                self.assertEqual(primary.main(self.cli_args("render-web-metadata")), 1)
            self.assertEqual(output.getvalue(), "")
            result = json.loads(diagnostics.getvalue())
            self.assertNotIn("private", diagnostics.getvalue())
            self.assertNotIn("/protected/path", diagnostics.getvalue())
            self.assertIn(result["reason_code"], {"primary_managed_input_or_io_failure", "backup_role_env_drifted"})


if __name__ == "__main__":
    unittest.main()
