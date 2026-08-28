from __future__ import annotations

from contextlib import redirect_stdout
import fcntl
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from scripts import prepare_production_private_primary_manifest as preparer


class ProductionPrivatePrimaryManifestPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root ownership contract requires a root test process")
        self.temporary = tempfile.TemporaryDirectory(
            prefix="private-primary-manifest-"
        )
        self.root = Path(self.temporary.name) / "release-control"
        self.root.mkdir(mode=0o700)
        self.patch = mock.patch.object(preparer, "APPROVED_ROOT", self.root)
        self.patch.start()
        self.source = self.root / "online.source.env"
        self.output = self.root / "online.private-primary.env"
        self.receipt = self.root / "online.private-primary.receipt.json"
        self.secret_marker = "sensitive-value-must-not-leak"
        self.source.write_text(self._source_text(), encoding="utf-8")
        self.source.chmod(0o600)

    def tearDown(self) -> None:
        if hasattr(self, "patch"):
            self.patch.stop()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def _source_text(self) -> str:
        lines = [
            "# retained comment",
            "LOCAL_PROJECT_DIR=/root/trading-bot/trading_bot",
            "LOCAL_FRONTEND_DIR=/root/trading-bot/trading_bot/frontend",
            "LOCAL_DIST_DIR=/root/trading-bot/trading_bot/mini_app_dist",
            "FOREIGN_PUBLIC_IP=65.109.216.187",
            "FOREIGN_PUBLIC_DOMAIN=coin.362514.ir",
            "FOREIGN_COMPOSE_PROJECT_NAME=trading_bot",
            "IRAN_HOST=65.109.220.59",
            "IRAN_SSH_USER=root",
            "IRAN_SSH_PORT=37067",
            "IRAN_PROJECT_DIR=/srv/trading-bot/current",
            "IRAN_DEPLOY_BASE_DIR=/srv/trading-bot",
            "IRAN_PUBLIC_IP=65.109.220.59",
            "IRAN_APP_DOMAIN=coin.gold-trade.ir",
            "IRAN_PUBLIC_DOMAIN=coin.gold-trade.ir",
            "IRAN_CERTBOT_EMAIL=ops@example.ir",
            "RUNTIME_ENV_SOURCE_PATH=/root/secure-envs/trading-bot/.env.foreign.production",
            "FOREIGN_RUNTIME_ENV_PATH=/root/secure-envs/trading-bot/runtime/.env.foreign.production",
            "IRAN_RUNTIME_ENV_PATH=/root/secure-envs/trading-bot/runtime/.env.iran.production",
            "ALLOW_PROJECT_ENV_SOURCE=0",
            "IRAN_ALLOW_DIRTY_RELEASE=0",
            "IRAN_ALLOW_NON_MAIN_RELEASE=0",
            "IRAN_ALLOW_RELEASE_BRANCH_DRIFT=0",
            "IRAN_SKIP_FOREIGN_DEPLOY=0",
            "PRODUCTION_RELEASE_BRANCH=main",
            f"IRAN_SSH_PRIVATE_KEY_PATH=/root/{self.secret_marker}",
            "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1",
            "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot",
            "PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=",
            "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=1",
            "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM=prepare-production-market-pipeline-shadow-evidence",
            "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1",
            "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM=load-and-preflight-production-market-pipeline-shadow-hosts",
            "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED=1",
            "PRODUCTION_MARKET_PIPELINE_MIGRATION_CONFIRM=backup-and-migrate-production-market-pipeline-shadow",
            "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED=1",
            "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_CONFIRM=rollout-production-market-pipeline-private-shadow",
            "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=0",
        ]
        return "\n".join(lines) + "\n"

    def _arguments(self, *, expected_digest: str | None = None) -> list[str]:
        return [
            "--source",
            str(self.source),
            "--expected-source-sha256",
            expected_digest or sha256(self.source.read_bytes()).hexdigest(),
            "--output",
            str(self.output),
            "--receipt",
            str(self.receipt),
            "--confirm",
            preparer.CONFIRMATION,
        ]

    def _run(self, arguments: list[str] | None = None) -> tuple[int, dict[str, object], str]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = preparer.main(arguments or self._arguments())
        text = stream.getvalue()
        return status, json.loads(text), text

    def test_prepares_exact_relay_disabled_manifest_without_disclosing_values(self) -> None:
        source_before = self.source.read_bytes()
        status, result, stdout = self._run()

        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn(self.secret_marker, stdout)
        self.assertEqual(self.source.read_bytes(), source_before)
        rendered = self.output.read_text(encoding="utf-8")
        for key, value in preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES.items():
            self.assertIn(f"{key}={value}\n", rendered)
        self.assertIn(f"IRAN_SSH_PRIVATE_KEY_PATH=/root/{self.secret_marker}\n", rendered)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertNotIn(self.secret_marker, self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(receipt["source_sha256"], sha256(source_before).hexdigest())
        self.assertEqual(receipt["output_sha256"], sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(
            set(receipt["changed_keys"]),
            {
                key
                for key, value in preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES.items()
                if f"{key}={value}\n" not in source_before.decode("utf-8")
            },
        )
        for path in (self.source, self.output, self.receipt):
            info = path.stat()
            self.assertEqual(info.st_uid, 0)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)

    def test_same_source_output_and_receipt_are_idempotent(self) -> None:
        first_status, first, _ = self._run()
        first_output = self.output.read_bytes()
        first_receipt = self.receipt.read_bytes()

        second_status, second, _ = self._run()

        self.assertEqual((first_status, second_status), (0, 0))
        self.assertEqual(first["output_state"], "CREATED")
        self.assertEqual(first["receipt_state"], "CREATED")
        self.assertEqual(second["output_state"], "ALREADY_CURRENT")
        self.assertEqual(second["receipt_state"], "ALREADY_CURRENT")
        self.assertEqual(self.output.read_bytes(), first_output)
        self.assertEqual(self.receipt.read_bytes(), first_receipt)

    def test_concurrent_identical_publish_is_idempotent_and_no_clobber(self) -> None:
        payload = b"deterministic-derived-manifest\n"

        def publish_first(
            _temporary: Path, target: Path, *, follow_symlinks: bool
        ) -> None:
            self.assertFalse(follow_symlinks)
            Path(target).write_bytes(payload)
            Path(target).chmod(0o600)
            raise FileExistsError

        with mock.patch.object(preparer.os, "link", side_effect=publish_first):
            state = preparer._write_atomic_or_verify(
                self.output, payload, label="output_manifest"
            )

        self.assertEqual(state, "ALREADY_CURRENT")
        self.assertEqual(self.output.read_bytes(), payload)
        self.assertEqual(self.output.stat().st_nlink, 1)
        self.assertEqual(list(self.root.glob(f".{self.output.name}.*.tmp")), [])

    def test_source_race_leaves_no_pass_receipt_and_retry_recovers(self) -> None:
        original_reader = preparer._read_secure_file
        source_reads = 0

        def changed_after_output(path: Path, *, label: str) -> bytes:
            nonlocal source_reads
            payload = original_reader(path, label=label)
            if Path(path) == self.source:
                source_reads += 1
                if source_reads == 3:
                    return payload + b"# external-race\n"
            return payload

        with mock.patch.object(
            preparer, "_read_secure_file", side_effect=changed_after_output
        ):
            status, result, _ = self._run()

        self.assertEqual(status, 2)
        self.assertEqual(
            result["reason_code"], "source_manifest_changed_after_output"
        )
        self.assertTrue(self.output.exists())
        self.assertFalse(self.receipt.exists())

        retry_status, retry, _ = self._run()
        self.assertEqual(retry_status, 0)
        self.assertEqual(retry["output_state"], "ALREADY_CURRENT")
        self.assertEqual(retry["receipt_state"], "CREATED")

    def test_insecure_global_lock_is_rejected_before_output(self) -> None:
        lock = self.root / preparer.LOCK_FILE_NAME
        lock.write_text("", encoding="utf-8")
        lock.chmod(0o644)

        status, result, _ = self._run()

        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "preparation_lock_security_invalid")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_busy_global_lock_and_non_root_execution_fail_closed(self) -> None:
        lock = self.root / preparer.LOCK_FILE_NAME
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, result, _ = self._run()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "preparation_lock_busy")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

        with mock.patch.object(preparer.os, "geteuid", return_value=1000):
            status, result, _ = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "root_execution_required")
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_last_line_without_newline_stays_without_newline(self) -> None:
        self.source.write_text(self._source_text().rstrip("\n"), encoding="utf-8")
        self.source.chmod(0o600)

        status, _result, _ = self._run()

        self.assertEqual(status, 0)
        self.assertFalse(self.output.read_bytes().endswith(b"\n"))

    def test_missing_reviewed_pipeline_controls_are_appended_only(self) -> None:
        omitted = {
            "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED",
            "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM",
            "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED",
            "PRODUCTION_MARKET_PIPELINE_MIGRATION_CONFIRM",
        }
        retained_lines = [
            line
            for line in self._source_text().splitlines()
            if line.split("=", 1)[0] not in omitted
        ]
        source_text = "\n".join(retained_lines) + "\n"
        self.source.write_text(source_text, encoding="utf-8")
        self.source.chmod(0o600)

        status, receipt, _ = self._run()

        self.assertEqual(status, 0)
        rendered = self.output.read_text(encoding="utf-8")
        for key in omitted:
            self.assertEqual(rendered.count(f"{key}="), 1)
            self.assertIn(
                f"{key}={preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES[key]}\n",
                rendered,
            )
        self.assertEqual(set(receipt["changed_keys"]).intersection(omitted), omitted)

    def test_checked_in_manifest_schema_is_transform_compatible(self) -> None:
        source = preparer.MANIFEST_SCHEMA_SOURCE.read_bytes()

        rendered, changed = preparer._parse_and_render(source)

        self.assertGreater(len(rendered), 0)
        self.assertTrue(set(changed).issubset(preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES))

    def test_schema_duplicate_and_oversized_render_fail_closed(self) -> None:
        duplicate_schema = self.root / "duplicate-schema.env"
        duplicate_schema.write_bytes(
            preparer.MANIFEST_SCHEMA_SOURCE.read_bytes()
            + b"\nLOCAL_PROJECT_DIR=/duplicate\n"
        )
        with mock.patch.object(
            preparer, "MANIFEST_SCHEMA_SOURCE", duplicate_schema
        ):
            with self.assertRaisesRegex(
                preparer.ManifestPreparationError,
                "manifest_schema_duplicate_key",
            ):
                preparer._manifest_schema_contract()

        without_updates = "\n".join(
            line
            for line in self._source_text().splitlines()
            if line.split("=", 1)[0]
            not in preparer.PRIVATE_PRIMARY_MANIFEST_UPDATES
        ) + "\n"
        payload = without_updates.encode("utf-8")
        with mock.patch.object(
            preparer, "MAXIMUM_MANIFEST_BYTES", len(payload) + 8
        ):
            with self.assertRaisesRegex(
                preparer.ManifestPreparationError,
                "rendered_manifest_too_large",
            ):
                preparer._parse_and_render(payload)

    def test_non_lf_line_separators_and_incomplete_identity_fail_closed(self) -> None:
        self.source.write_bytes(self._source_text().replace("\n", "\r\n").encode())
        self.source.chmod(0o600)
        status, result, _ = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(
            result["reason_code"], "source_manifest_line_separator_invalid"
        )

        incomplete = "\n".join(
            line
            for line in self._source_text().splitlines()
            if not line.startswith("IRAN_PUBLIC_DOMAIN=")
        ) + "\n"
        self.source.write_text(incomplete, encoding="utf-8")
        self.source.chmod(0o600)
        status, result, _ = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(
            result["reason_code"], "source_manifest_identity_incomplete"
        )

    def test_cas_tamper_fails_without_outputs(self) -> None:
        status, result, stdout = self._run(
            self._arguments(expected_digest="0" * 64)
        )

        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "source_manifest_cas_mismatch")
        self.assertNotIn(self.secret_marker, stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_existing_output_tamper_is_never_overwritten(self) -> None:
        self.output.write_text("tampered=true\n", encoding="utf-8")
        self.output.chmod(0o600)
        before = self.output.read_bytes()

        status, result, _ = self._run()

        self.assertEqual(status, 2)
        self.assertEqual(
            result["reason_code"], "output_manifest_exists_with_different_bytes"
        )
        self.assertEqual(self.output.read_bytes(), before)
        self.assertFalse(self.receipt.exists())

    def test_unknown_and_duplicate_keys_fail_closed(self) -> None:
        for suffix, expected in (
            ("UNREVIEWED_DEPLOY_KEY=1\n", "source_manifest_unknown_key"),
            ("IRAN_SKIP_FOREIGN_DEPLOY=0\n", "source_manifest_duplicate_key"),
        ):
            with self.subTest(expected=expected):
                self.source.write_text(self._source_text() + suffix, encoding="utf-8")
                self.source.chmod(0o600)
                status, result, _ = self._run()
                self.assertEqual(status, 2)
                self.assertEqual(result["reason_code"], expected)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.receipt.exists())

    def test_path_escape_symlink_hardlink_and_permissions_fail_closed(self) -> None:
        cases: list[tuple[str, callable]] = []

        outside = Path(self.temporary.name) / "outside.env"
        outside.write_text(self._source_text(), encoding="utf-8")
        outside.chmod(0o600)
        cases.append(("scope", lambda: setattr(self, "source", outside)))

        real = self.root / "real.env"
        real.write_text(self._source_text(), encoding="utf-8")
        real.chmod(0o600)
        alias = self.root / "alias.env"
        alias.symlink_to(real)
        cases.append(("symlink", lambda: setattr(self, "source", alias)))

        hardlink = self.root / "hardlink.env"
        os.link(real, hardlink)
        cases.append(("hardlink", lambda: setattr(self, "source", hardlink)))

        insecure = self.root / "insecure.env"
        insecure.write_text(self._source_text(), encoding="utf-8")
        insecure.chmod(0o640)
        cases.append(("permission", lambda: setattr(self, "source", insecure)))

        original = self.source
        for label, configure in cases:
            with self.subTest(label=label):
                self.source = original
                configure()
                status, _result, _ = self._run()
                self.assertEqual(status, 2)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.receipt.exists())

    def test_descriptor_reader_rejects_path_inode_swap_during_read(self) -> None:
        replacement = self.root / "replacement.env"
        replacement.write_text(self._source_text(), encoding="utf-8")
        replacement.chmod(0o600)
        displaced = self.root / "displaced.env"
        original_read = os.read
        swapped = False

        def swapping_read(descriptor: int, maximum: int) -> bytes:
            nonlocal swapped
            payload = original_read(descriptor, maximum)
            if payload and not swapped:
                swapped = True
                self.source.rename(displaced)
                replacement.rename(self.source)
            return payload

        with mock.patch.object(preparer.os, "read", side_effect=swapping_read):
            with self.assertRaisesRegex(
                preparer.ManifestPreparationError,
                "source_manifest_changed_during_read",
            ):
                preparer._read_secure_file(
                    self.source, label="source_manifest"
                )

    def test_output_alias_and_insecure_existing_receipt_are_rejected(self) -> None:
        alias_arguments = self._arguments()
        alias_arguments[alias_arguments.index("--output") + 1] = str(self.source)
        status, result, _ = self._run(alias_arguments)
        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "manifest_output_alias")

        self.receipt.write_text("{}\n", encoding="utf-8")
        self.receipt.chmod(0o644)
        status, result, _ = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(result["reason_code"], "receipt_security_invalid")

    def test_non_target_value_bytes_are_preserved_but_release_escape_is_rejected(self) -> None:
        special_password = "p@ss!word%=[]{}#"
        source_with_special_value = (
            self._source_text() + f"IRAN_SSH_PASSWORD={special_password}\n"
        )
        self.source.write_text(source_with_special_value, encoding="utf-8")
        self.source.chmod(0o600)
        status, _result, stdout = self._run()
        self.assertEqual(status, 0)
        self.assertIn(
            f"IRAN_SSH_PASSWORD={special_password}\n",
            self.output.read_text(encoding="utf-8"),
        )
        self.assertNotIn(special_password, stdout)

        self.output.unlink()
        self.receipt.unlink()

        self.source.write_text(
            self._source_text().replace("IRAN_ALLOW_DIRTY_RELEASE=0", "IRAN_ALLOW_DIRTY_RELEASE=1"),
            encoding="utf-8",
        )
        self.source.chmod(0o600)
        status, result, _ = self._run()
        self.assertEqual(status, 2)
        self.assertEqual(
            result["reason_code"], "source_manifest_release_safety_invalid"
        )


if __name__ == "__main__":
    unittest.main()
