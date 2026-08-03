from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from scripts.transfer_three_site_stage4r_bundle import (
    ROLE_HOSTS,
    Stage4RBundleTransferError,
    confirmation_phrase,
    destination_for,
    execute,
)


RELEASE = "a" * 40
RECIPIENT = "age1" + "a" * 58


class _Args:
    def __init__(self, **values):
        self.__dict__.update(values)


class Stage4RBundleTransferTests(unittest.TestCase):
    def _args(self, root: Path, *, role: str = "webapp-fi", host: str | None = None):
        source = root / f"stage4r-{role}-{RELEASE[:8]}.tar"
        source.write_bytes(b"stage4r-role-bundle")
        source.chmod(0o600)
        return _Args(
            role=role,
            host=host or ROLE_HOSTS[role],
            release_sha=RELEASE,
            source=source,
            recipient=RECIPIENT,
            credentials=root / "credentials.env",
            bucket="gold-trade-staging-three-site-dr",
            prefix="staging/fd34231d-f52e-498a-aab4-438c99d88fc5/stage4r/",
            ssh_identity=root / "identity",
            known_hosts=root / "known_hosts",
            proxy_host=None,
            proxy_known_hosts=None,
            evidence=root / "evidence.json",
            apply=False,
            confirm=None,
        )

    def test_plans_exact_role_bound_object_storage_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self._args(Path(directory))
            result = execute(args)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["role"], "webapp-fi")
        self.assertEqual(result["host"], ROLE_HOSTS["webapp-fi"])
        self.assertEqual(result["payload_transport"], "private-versioned-object-storage-cse")
        self.assertFalse(result["ssh_payload_transfer"])
        self.assertTrue(result["destination"].endswith(f"webapp-fi-{RELEASE[:8]}.tar"))

    def test_rejects_a_target_host_that_is_not_the_fixed_staging_role(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self._args(Path(directory), host="65.109.220.59")
            with self.assertRaisesRegex(Stage4RBundleTransferError, "target host"):
                execute(args)

    def test_webapp_ir_requires_the_fixed_relay(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self._args(Path(directory), role="webapp-ir")
            with self.assertRaisesRegex(Stage4RBundleTransferError, "fixed relay"):
                execute(args)

    def test_destination_and_confirmation_bind_role_sha_and_digest(self):
        destination = destination_for(role="bot-fi", release_sha=RELEASE)
        self.assertEqual(
            str(destination),
            f"/var/tmp/three-site-stage4r/bot-fi/{RELEASE}/stage4r-bot-fi-{RELEASE[:8]}.tar",
        )
        digest = "b" * 64
        self.assertEqual(
            confirmation_phrase(role="bot-fi", release_sha=RELEASE, digest=digest),
            f"transfer-stage4r-bundle:bot-fi:{RELEASE}:{digest}",
        )
