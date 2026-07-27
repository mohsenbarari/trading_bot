from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_webapp_ir_dark_standby_env",
    ROOT / "scripts" / "render_webapp_ir_dark_standby_env.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RenderDarkStandbyEnvTests(unittest.TestCase):
    OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
    IMAGE_ID = "sha256:" + "d" * 64

    def render(self, source: Path, output: Path) -> None:
        MODULE.render(
            source,
            output,
            "a" * 40,
            self.OPERATION_ID,
            self.IMAGE_ID,
        )

    def test_render_emits_only_closed_database_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            output = root / "target.env"
            source.write_text(
                "POSTGRES_DB=app\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=db-secret\n"
                "POSTGRES_MAX_CONNECTIONS=200\n"
                "BOT_TOKEN=bot-secret\nJWT_SECRET_KEY=jwt-secret\n"
                "SMSIR_API_KEY=sms-secret\nWEB_PUSH_VAPID_PRIVATE_KEY=vapid-secret\n"
                "SYNC_API_KEY=sync-secret\nARVAN_S3_SECRET_KEY=s3-secret\n"
                "WRITER_WITNESS_CLIENT_SECRET=witness-secret\n",
                encoding="utf-8",
            )
            source.chmod(0o600)
            self.render(source, output)
            values = MODULE.parse(output)
            self.assertEqual(values["DARK_STANDBY_MODE"], "1")
            self.assertNotIn("COMPOSE_PROJECT_NAME", values)
            self.assertEqual(values["RELEASE_SHA"], "a" * 40)
            self.assertEqual(values["DARK_STANDBY_OPERATION_ID"], self.OPERATION_ID)
            self.assertEqual(values["DARK_STANDBY_DB_IMAGE_ID"], self.IMAGE_ID)
            self.assertEqual(values["POSTGRES_PASSWORD"], "db-secret")
            self.assertEqual(values["POSTGRES_MAX_CONNECTIONS"], "200")
            for forbidden in (
                "BOT_TOKEN",
                "JWT_SECRET_KEY",
                "SMSIR_API_KEY",
                "WEB_PUSH_VAPID_PRIVATE_KEY",
                "SYNC_API_KEY",
                "ARVAN_S3_SECRET_KEY",
                "WRITER_WITNESS_CLIENT_SECRET",
            ):
                self.assertNotIn(forbidden, values)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_missing_database_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            source.write_text("POSTGRES_DB=app\nPOSTGRES_USER=app\n", encoding="utf-8")
            source.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "POSTGRES_PASSWORD"):
                self.render(source, root / "target.env")

    def test_source_symlink_and_hardlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            source.write_text(
                "POSTGRES_DB=app\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=secret\n",
                encoding="utf-8",
            )
            source.chmod(0o600)
            symlink = root / "symlink.env"
            symlink.symlink_to(source)
            with self.assertRaises(Exception):
                self.render(symlink, root / "target.env")
            hardlink = root / "hardlink.env"
            hardlink.hardlink_to(source)
            with self.assertRaises(Exception):
                self.render(source, root / "target.env")

    def test_output_symlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            source.write_text(
                "POSTGRES_DB=app\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=secret\n",
                encoding="utf-8",
            )
            source.chmod(0o600)
            unrelated = root / "unrelated.env"
            unrelated.write_text("do-not-change\n", encoding="utf-8")
            unrelated.chmod(0o600)
            output = root / "target.env"
            output.symlink_to(unrelated)
            with self.assertRaises(Exception):
                self.render(source, output)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "do-not-change\n")

    def test_nonimmutable_image_or_invalid_operation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            source.write_text(
                "POSTGRES_DB=app\nPOSTGRES_USER=app\nPOSTGRES_PASSWORD=secret\n",
                encoding="utf-8",
            )
            source.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "operation id"):
                MODULE.render(
                    source,
                    root / "target.env",
                    "a" * 40,
                    "shared",
                    self.IMAGE_ID,
                )
            with self.assertRaisesRegex(ValueError, "image ID"):
                MODULE.render(
                    source,
                    root / "target.env",
                    "a" * 40,
                    self.OPERATION_ID,
                    "trading_bot_postgres_boottime:latest",
                )


if __name__ == "__main__":
    unittest.main()
