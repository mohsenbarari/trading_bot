"""Adversarial local tests for final immutable three-site campaign provenance."""

from __future__ import annotations

import ast
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import fenced_fi_release_identity as identity
from scripts import manage_three_site_campaign_provenance as subject
from scripts import prepare_writer_witness_immutable_release as witness_control
from scripts import verify_writer_witness_paired_attestation as witness_pair
from scripts import writer_witness_rotation_lifecycle as lifecycle


NOW = datetime(2026, 8, 2, 7, 0, tzinfo=timezone.utc)
NL = bytes((10,))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(mode)


def _git(repository: Path, *arguments: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout.strip() if capture else ""


class CampaignProvenanceFixture:
    def __init__(
        self,
        root: Path,
        *,
        witness_issued_at: datetime = NOW,
        witness_not_after: datetime = NOW + timedelta(hours=1),
    ) -> None:
        self.root = root
        self.root.chmod(0o700)
        self.campaign_id = "campaign-provenance-fixture-20260802"
        self.provenance_root = root / subject.PROVENANCE_DIRECTORY_NAME
        self.provenance_root.mkdir(mode=0o700)
        self.provenance_root.chmod(0o700)
        self.authority_private = Ed25519PrivateKey.generate()
        self.authority_public = self.authority_private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.authority_path = root / "candidate-authority.pub"
        _write(
            self.authority_path,
            base64.b64encode(self.authority_public) + NL,
        )
        self.control_root, self.control_sha, self.control_tree = self._make_control_release()
        self.application_sha = "a" * 40
        self.application_tree = "b" * 40
        self.candidate_path = root / "future-app-bot-candidate.json"
        self.write_candidate()
        self.witness_parent = root / "witness-state-parent"
        self.witness_parent.mkdir(mode=0o700)
        self.witness_parent.chmod(0o700)
        self.witness_state = self.witness_parent / lifecycle.STATE_DIRECTORY_NAME
        self.install_witness_policy(
            policy_id="witness-final-one",
            issued_at=witness_issued_at,
            not_after=witness_not_after,
        )

    def _make_control_release(self) -> tuple[Path, str, str]:
        work = self.root / "control-work"
        work.mkdir(mode=0o700)
        work.chmod(0o700)
        _git(work, "init", "-q")
        _git(work, "config", "user.email", "provenance-test@example.invalid")
        _git(work, "config", "user.name", "Provenance Test")
        _write(work / "scripts" / "control.py", b"print('control fixture')\n", mode=0o644)
        profile = json.loads(witness_control.DEFAULT_PROFILE_PATH.read_bytes())
        profile["release_id"] += "-control-fixture"
        _write(
            work / subject.WITNESS_PROFILE_RELATIVE_PATH,
            witness_control._canonical_json_bytes(profile) + NL,
            mode=0o644,
        )
        _git(work, "add", ".")
        _git(work, "commit", "-qm", "control fixture")
        sha = _git(work, "rev-parse", "HEAD", capture=True)
        tree = _git(work, "rev-parse", "HEAD^{tree}", capture=True)
        _git(work, "checkout", "-q", "--detach", sha)
        parent = self.root / "control-releases"
        parent.mkdir(mode=0o700)
        parent.chmod(0o700)
        release = parent / sha
        work.rename(release)
        release.chmod(0o700)
        return release, sha, tree

    def _candidate_document(
        self,
        *,
        application_sha: str | None = None,
        control_tree: str | None = None,
        compose_relative_path: str | None = None,
    ) -> bytes:
        application_sha = application_sha or self.application_sha
        unsigned = {
            "schema": identity.FENCED_FI_RELEASE_IDENTITY_SCHEMA,
            "release_sha": application_sha,
            "release_tree_sha": self.application_tree,
            "application_release_root": "/srv/trading-bot-three-site/releases/" + application_sha,
            "control_release_sha": self.control_sha,
            "control_release_tree_sha": control_tree or self.control_tree,
            "control_release_root": str(self.control_root),
            "compose_relative_path": (
                compose_relative_path
                if compose_relative_path is not None
                else "deploy/production/docker-compose.webapp-fi-writer-vnext.yml"
            ),
            "compose_sha256": _sha("compose"),
            "term_fenced_application_evidence_sha256": _sha("term-fenced-evidence"),
            "fenced_fi_build_input": {
                "build_input_manifest_sha256": _sha("sealed-build-input"),
                "mini_app_dist_manifest_sha256": _sha("mini-app-dist-manifest"),
                "mini_app_dist_files_sha256": _sha("mini-app-dist-files"),
                "mini_app_dist_file_count": 17,
                "mini_app_dist_total_bytes": 4096,
            },
            "services": {
                "app": {
                    "image_repo_digest": (
                        "registry.example.invalid/trading-bot-app@sha256:" + "c" * 64
                    ),
                    "image_id": "sha256:" + "d" * 64,
                },
                "bot": {
                    "image_repo_digest": (
                        "registry.example.invalid/trading-bot-bot@sha256:" + "e" * 64
                    ),
                    "image_id": "sha256:" + "f" * 64,
                },
            },
            "signer_key_id": "ed25519-sha256:" + hashlib.sha256(self.authority_public).hexdigest(),
        }
        signature = self.authority_private.sign(
            b"gold-trade-wa-fi-fenced-release-identity-v3\x00"
            + identity.canonical_fenced_fi_release_identity_json_bytes(unsigned)
        )
        return identity.canonical_fenced_fi_release_identity_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )

    def write_candidate(
        self,
        *,
        application_sha: str | None = None,
        control_tree: str | None = None,
        compose_relative_path: str | None = None,
    ) -> bytes:
        raw = self._candidate_document(
            application_sha=application_sha,
            control_tree=control_tree,
            compose_relative_path=compose_relative_path,
        )
        _write(self.candidate_path, raw)
        return raw

    def _policy_payload(self, *, policy_id: str, issued_at: datetime, not_after: datetime) -> bytes:
        profile = witness_control._load_profile(
            self.control_root / subject.WITNESS_PROFILE_RELATIVE_PATH
        )
        profile_sha256 = witness_pair._profile_sha256(profile)
        value = {
            "schema": lifecycle.POLICY_SCHEMA,
            "policy_id": policy_id,
            "issued_at": issued_at.isoformat(),
            "not_before": issued_at.isoformat(),
            "not_after": not_after.isoformat(),
            "profile": {
                "release_id": profile["release_id"],
                "source_commit": profile["source_commit"],
                "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
                "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
                "profile_sha256": profile_sha256,
            },
            "witness_trust": {
                "witness_endpoint_sha256": _sha("endpoint"),
                "ca_bundle_sha256": _sha("ca"),
                "witness_public_key_sha256": _sha("key"),
            },
            "clients": {
                "webapp_fi": {
                    "site": "webapp_fi",
                    "key_id_sha256": _sha("fi-key-" + policy_id),
                    "generation": "fi-" + policy_id,
                },
                "webapp_ir": {
                    "site": "webapp_ir",
                    "key_id_sha256": _sha("ir-key-" + policy_id),
                    "generation": "ir-" + policy_id,
                },
            },
        }
        return witness_control._canonical_json_bytes(value) + NL

    def install_witness_policy(
        self,
        *,
        policy_id: str,
        issued_at: datetime,
        not_after: datetime,
    ) -> lifecycle.CurrentPolicySnapshot:
        profile = witness_control._load_profile(
            self.control_root / subject.WITNESS_PROFILE_RELATIVE_PATH
        )
        return lifecycle.install_policy_and_activate(
            policy_id=policy_id,
            policy_raw=self._policy_payload(
                policy_id=policy_id,
                issued_at=issued_at,
                not_after=not_after,
            ),
            policy_profile_sha256=witness_pair._profile_sha256(profile),
            issued_at=issued_at.isoformat(),
            state_directory=self.witness_state,
        )

    def create(self, *, campaign_id: str | None = None, candidate: Path | None = None) -> dict:
        return subject.create_three_site_campaign_provenance(
            campaign_id=campaign_id or self.campaign_id,
            candidate_descriptor=candidate or self.candidate_path,
            _provenance_root_for_test=self.provenance_root,
            _candidate_authority_path_for_test=self.authority_path,
            _witness_state_directory_for_test=self.witness_state,
            _verification_time_for_test=NOW + timedelta(seconds=1),
        )


@unittest.skipUnless(os.geteuid() == 0, "three-site campaign provenance is root-only")
class ThreeSiteCampaignProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="three-site-campaign-provenance-")
        self.fixture = CampaignProvenanceFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_binds_exact_control_candidate_and_final_witness_without_authorization(self) -> None:
        created = self.fixture.create()
        loaded = subject.load_three_site_campaign_provenance(
            campaign_id=self.fixture.campaign_id,
            _provenance_root_for_test=self.fixture.provenance_root,
        )
        profile = witness_control._load_profile(
            self.fixture.control_root / subject.WITNESS_PROFILE_RELATIVE_PATH
        )
        current = lifecycle.resolve_current_policy(
            profile_sha256=witness_pair._profile_sha256(profile),
            state_directory=self.fixture.witness_state,
        )
        self.assertEqual(created["status"], "created-non-authorizing")
        self.assertEqual(loaded["control"]["release_sha"], self.fixture.control_sha)
        self.assertEqual(loaded["control"]["release_tree_sha"], self.fixture.control_tree)
        self.assertEqual(
            loaded["candidate"]["application_release_sha"], self.fixture.application_sha
        )
        self.assertEqual(
            loaded["candidate"]["identity_sha256"],
            created["candidate_identity_sha256"],
        )
        self.assertEqual(loaded["witness"]["policy_id"], current.policy_id)
        self.assertEqual(
            loaded["witness"]["profile_relative_path"],
            subject.WITNESS_PROFILE_RELATIVE_PATH.as_posix(),
        )
        self.assertEqual(loaded["witness"]["policy_sha256"], current.policy_sha256)
        self.assertEqual(loaded["witness"]["selector_sha256"], current.selector_sha256)
        self.assertEqual(loaded["witness"]["activation_sha256"], current.activation_sha256)
        self.assertEqual(loaded["witness"]["ledger_sha256"], current.ledger_sha256)
        self.assertEqual(loaded["witness"]["ledger_entries"], current.ledger_entries)
        self.assertEqual(loaded["witness"]["sequence"], current.sequence)
        for field in (
            "writer_authorized",
            "promotion_authorized",
            "deployment_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        ):
            self.assertIs(loaded[field], False)
            self.assertIs(created[field], False)
        document = (
            self.fixture.provenance_root
            / subject.CAMPAIGNS_DIRECTORY_NAME
            / self.fixture.campaign_id
            / subject.PROVENANCE_FILENAME
        )
        for directory in (
            self.fixture.provenance_root,
            self.fixture.provenance_root / subject.CAMPAIGNS_DIRECTORY_NAME,
            self.fixture.provenance_root / subject.CANDIDATE_CLAIMS_DIRECTORY_NAME,
            self.fixture.provenance_root / subject.CAMPAIGN_CLAIMS_DIRECTORY_NAME,
            document.parent,
        ):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(document.stat().st_mode), 0o400)
        self.assertEqual(
            stat.S_IMODE(
                (
                    self.fixture.provenance_root
                    / subject.CANDIDATE_CLAIMS_DIRECTORY_NAME
                    / ("candidate-" + created["candidate_identity_sha256"] + ".json")
                ).stat().st_mode
            ),
            0o400,
        )
        encoded = document.read_bytes().lower()
        self.assertNotIn(b"://", encoded)
        self.assertNotIn(b"secret", encoded)
        self.assertNotIn(str(self.fixture.control_root).encode("utf-8"), encoded)
        self.assertNotIn(b"/srv/trading-bot-three-site/releases/", encoded)
        self.assertNotIn(self.fixture.authority_private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ), encoded)

    def test_legacy_v2_campaign_pin_remains_read_only_loadable(self) -> None:
        """A v2 audit pin cannot become a v3 creation input, but stays legible."""

        self.fixture.create()
        document = (
            self.fixture.provenance_root
            / subject.CAMPAIGNS_DIRECTORY_NAME
            / self.fixture.campaign_id
            / subject.PROVENANCE_FILENAME
        )
        value = json.loads(document.read_bytes())
        value["candidate"]["schema"] = (
            identity.FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_LEGACY_SCHEMA
        )
        unsigned = dict(value)
        del unsigned["provenance_sha256"]
        value["provenance_sha256"] = hashlib.sha256(
            subject._canonical_json_bytes(unsigned)
        ).hexdigest()
        _write(document, subject._canonical_json_bytes(value) + NL, mode=0o400)

        loaded = subject.load_three_site_campaign_provenance(
            campaign_id=self.fixture.campaign_id,
            _provenance_root_for_test=self.fixture.provenance_root,
        )

        self.assertEqual(
            identity.FENCED_FI_RELEASE_IDENTITY_TERM_FENCED_LEGACY_SCHEMA,
            loaded["candidate"]["schema"],
        )
        self.assertIs(loaded["writer_authorized"], False)
        self.assertIs(loaded["full_matrix_executed"], False)

        value["candidate"]["schema"] = {"unhashable": True}
        unsigned = dict(value)
        del unsigned["provenance_sha256"]
        value["provenance_sha256"] = hashlib.sha256(
            subject._canonical_json_bytes(unsigned)
        ).hexdigest()
        _write(document, subject._canonical_json_bytes(value) + NL, mode=0o400)
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CANDIDATE_INVALID",
        ):
            subject.load_three_site_campaign_provenance(
                campaign_id=self.fixture.campaign_id,
                _provenance_root_for_test=self.fixture.provenance_root,
            )

    def test_rejects_missing_legacy_mismatched_control_and_expired_witness_before_claims(
        self,
    ) -> None:
        missing = self.fixture.root / "missing-candidate.json"
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CANDIDATE_DESCRIPTOR_UNAVAILABLE",
        ):
            self.fixture.create(candidate=missing)
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

        self.fixture.write_candidate(
            application_sha=subject.LEGACY_UNFENCED_APPLICATION_RELEASE_SHA
        )
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "LEGACY_2C08_CANDIDATE_BLOCKED",
        ):
            self.fixture.create()
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

        self.fixture.write_candidate(
            compose_relative_path=subject.LEGACY_UNFENCED_COMPOSE_RELATIVE_PATH
        )
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "LEGACY_2C08_CANDIDATE_BLOCKED",
        ):
            self.fixture.create()
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

        self.fixture.write_candidate(control_tree="0" * 40)
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CONTROL_RELEASE_TREE_MISMATCH",
        ):
            self.fixture.create()
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory(prefix="three-site-provenance-expired-")
        expired = CampaignProvenanceFixture(
            Path(self.temporary.name),
            witness_issued_at=NOW - timedelta(hours=2),
            witness_not_after=NOW - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "WITNESS_POLICY_STALE",
        ):
            expired.create()
        self.assertEqual(list(expired.provenance_root.iterdir()), [])

    def test_rejects_tampered_candidate_and_rolled_back_witness(self) -> None:
        original = self.fixture.candidate_path.read_bytes()
        tampered = bytearray(original)
        tampered[-1] = ord("A") if tampered[-1] != ord("A") else ord("B")
        _write(self.fixture.candidate_path, bytes(tampered))
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CANDIDATE_DESCRIPTOR_INVALID",
        ):
            self.fixture.create()
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

        _write(self.fixture.candidate_path, original)
        paths = lifecycle._state_paths(self.fixture.witness_state, create=False)
        old_current = paths.current_selector.read_bytes()
        self.fixture.install_witness_policy(
            policy_id="witness-final-two",
            issued_at=NOW + timedelta(seconds=3),
            not_after=NOW + timedelta(hours=1),
        )
        _write(paths.current_selector, old_current)
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "WITNESS_LIFECYCLE_INVALID",
        ):
            self.fixture.create()
        self.assertEqual(list(self.fixture.provenance_root.iterdir()), [])

    def test_candidate_replay_and_campaign_replacement_are_claim_blocked(self) -> None:
        first = self.fixture.create()
        replay_campaign = "campaign-provenance-replay-20260802"
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CANDIDATE_CLAIM_EXISTS",
        ):
            self.fixture.create(campaign_id=replay_campaign)
        self.assertFalse(
            (
                self.fixture.provenance_root
                / subject.CAMPAIGN_CLAIMS_DIRECTORY_NAME
                / ("campaign-" + replay_campaign + ".json")
            ).exists()
        )
        self.assertFalse(
            (
                self.fixture.provenance_root
                / subject.CAMPAIGNS_DIRECTORY_NAME
                / replay_campaign
            ).exists()
        )

        # A distinct signed future candidate cannot replace an already bound
        # campaign either; the candidate has not been consumed by that failed
        # replacement attempt.
        self.fixture.write_candidate(application_sha="9" * 40)
        fresh_raw = self.fixture.candidate_path.read_bytes()
        fresh_sha = hashlib.sha256(fresh_raw).hexdigest()
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CAMPAIGN_CLAIM_EXISTS",
        ):
            self.fixture.create()
        self.assertFalse(
            (
                self.fixture.provenance_root
                / subject.CANDIDATE_CLAIMS_DIRECTORY_NAME
                / ("candidate-" + fresh_sha + ".json")
            ).exists()
        )
        self.assertEqual(first["provenance_sha256"], subject.load_three_site_campaign_provenance(
            campaign_id=self.fixture.campaign_id,
            _provenance_root_for_test=self.fixture.provenance_root,
        )["provenance_sha256"])

    def test_provenance_checksum_root_gate_cli_and_no_direct_network_or_docker_import(self) -> None:
        self.fixture.create()
        document = (
            self.fixture.provenance_root
            / subject.CAMPAIGNS_DIRECTORY_NAME
            / self.fixture.campaign_id
            / subject.PROVENANCE_FILENAME
        )
        value = json.loads(document.read_bytes())
        value["candidate"]["compose_sha256"] = "0" * 64
        _write(document, subject._canonical_json_bytes(value) + NL)
        document.chmod(0o400)
        with self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "CHECKSUM_INVALID",
        ):
            subject.load_three_site_campaign_provenance(
                campaign_id=self.fixture.campaign_id,
                _provenance_root_for_test=self.fixture.provenance_root,
            )
        with mock.patch.object(subject.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            subject.ThreeSiteCampaignProvenanceError,
            "ROOT_REQUIRED",
        ):
            self.fixture.create()
        parser = subject._parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([
                "create",
                "--campaign-id",
                self.fixture.campaign_id,
                "--candidate-descriptor",
                str(self.fixture.candidate_path),
                "--witness-state",
                "/tmp/override",
            ])
        tree = ast.parse(Path(subject.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        prohibited = {
            "boto3",
            "botocore",
            "docker",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(prohibited & imported)


if __name__ == "__main__":
    unittest.main()
