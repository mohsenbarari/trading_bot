"""Focused local tests for immutable controller source campaign bindings."""

from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


binding = _load("test_webapp_fi_source_campaign_binding", "webapp_fi_source_campaign_binding.py")
transport = _load("test_manage_webapp_fi_source_transport_binding", "manage_webapp_fi_source_transport.py")


def recipient(character: str) -> str:
    return "age1" + character * 40


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


def _new_detached_repository(path: Path, files: dict[str, str]) -> tuple[Path, str, str]:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "binding-test@example.invalid")
    _git(path, "config", "user.name", "Binding Test")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        target.write_text(content, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "fixture")
    commit = _git(path, "rev-parse", "HEAD", capture=True)
    tree = _git(path, "rev-parse", "HEAD^{tree}", capture=True)
    _git(path, "checkout", "-q", "--detach", commit)
    return path, commit, tree


class CampaignBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="source-campaign-binding-test-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.campaign_id = "campaign-binding-fixture-20260730"
        self.campaign_directory = self.root / self.campaign_id
        self.campaign_directory.mkdir(mode=0o700)
        self.campaign_directory.chmod(0o700)
        self.expected_revision = "a" * 12
        self.control, self.control_commit, self.control_tree = _new_detached_repository(
            self.root / "control",
            {"scripts/control.py": "print('control fixture')\n"},
        )
        self.application, self.application_commit, self.application_tree = _new_detached_repository(
            self.root / "application",
            {
                "app.py": "print('application fixture')\n",
                "migrations/versions/" + self.expected_revision + "_initial.py": (
                    "revision = '" + self.expected_revision + "'\n"
                    "down_revision = None\n"
                ),
                "migrations/versions/__init__.py": "",
            },
        )
        self.values = {
            "campaign_id": self.campaign_id,
            "application_source_repository": self.application,
            "application_release_sha": self.application_commit,
            "expected_alembic_revision": self.expected_revision,
            "control_source_repository": self.control,
            "control_commit": self.control_commit,
        }
        self.binding_path, self.bound = binding.create_campaign_binding(
            campaign_directory=self.campaign_directory,
            **self.values,
        )
        self.policy = transport.SourceTransportPolicy(
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-artifacts",
            prefix="campaigns/three-site",
            age_binary="/usr/bin/age",
            workspace=self.root / "workspace",
            controller_age_recipient=recipient("a"),
            webapp_fi_age_recipient=recipient("c"),
            webapp_ir_age_recipient=recipient("d"),
            maximum_plaintext_bytes=1024 * 1024,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_is_canonical_root_only_and_loads_exactly(self) -> None:
        self.assertEqual(
            self.campaign_directory / binding.SOURCE_PHASE_DIRECTORY / binding.CAMPAIGN_BINDING_FILENAME,
            self.binding_path,
        )
        self.assertEqual(0o700, stat.S_IMODE(self.binding_path.parent.lstat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.binding_path.lstat().st_mode))
        payload = self.binding_path.read_bytes()
        value = json.loads(payload)
        self.assertEqual(binding.CAMPAIGN_BINDING_SCHEMA, value["schema"])
        self.assertEqual("bound", value["status"])
        self.assertEqual(self.application_tree, value["application"]["release_tree"])
        self.assertEqual(self.values["expected_alembic_revision"], value["application"]["expected_alembic_revision"])
        self.assertEqual(self.control_tree, value["tooling"]["control_tree"])
        unsigned = {key: item for key, item in value.items() if key != "binding_sha256"}
        self.assertEqual(binding.sha256_bytes(binding.canonical_json_bytes(unsigned)), value["binding_sha256"])
        self.assertEqual(self.bound, binding.load_campaign_binding(self.binding_path))

    def test_create_is_create_only_and_preserves_existing_binding(self) -> None:
        original = self.binding_path.read_bytes()
        with self.assertRaisesRegex(binding.CampaignBindingError, "refusing to overwrite"):
            binding.create_campaign_binding(campaign_directory=self.campaign_directory, **self.values)
        self.assertEqual(original, self.binding_path.read_bytes())

    def test_load_rejects_checksum_tampering_and_cross_campaign_layout(self) -> None:
        original = self.binding_path.read_bytes()
        value = json.loads(original)
        value["application"]["release_sha"] = "f" * 40
        self.binding_path.write_bytes(binding.canonical_json_bytes(value) + b"\n")
        self.binding_path.chmod(0o600)
        with self.assertRaisesRegex(binding.CampaignBindingError, "checksum"):
            binding.load_campaign_binding(self.binding_path)

        other_campaign = self.root / "other-binding-fixture-20260730"
        other_campaign.mkdir(mode=0o700)
        other_campaign.chmod(0o700)
        other_source = other_campaign / binding.SOURCE_PHASE_DIRECTORY
        other_source.mkdir(mode=0o700)
        other_source.chmod(0o700)
        copied = other_source / binding.CAMPAIGN_BINDING_FILENAME
        copied.write_bytes(original)
        copied.chmod(0o600)
        with self.assertRaisesRegex(binding.CampaignBindingError, "does not match"):
            binding.load_campaign_binding(copied)

    def test_binding_path_requires_the_matching_existing_campaign_directory(self) -> None:
        self.assertEqual(
            self.binding_path,
            binding.campaign_binding_path(
                campaign_directory=self.campaign_directory,
                campaign_id=self.campaign_id,
            ),
        )
        with self.assertRaisesRegex(binding.CampaignBindingError, "does not match"):
            binding.campaign_binding_path(
                campaign_directory=self.campaign_directory,
                campaign_id="mismatch-binding-fixture-20260730",
            )

    def _new_campaign_directory(self, suffix: str) -> tuple[str, Path]:
        campaign_id = "campaign-binding-" + suffix + "-20260730"
        directory = self.root / campaign_id
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
        return campaign_id, directory

    def _create_for_new_campaign(self, suffix: str, **changes):
        campaign_id, directory = self._new_campaign_directory(suffix)
        values = {**self.values, "campaign_id": campaign_id, **changes}
        return binding.create_campaign_binding(campaign_directory=directory, **values)

    def test_create_derives_git_trees_and_rejects_revision_dirty_symlink_and_untrusted_sources(self) -> None:
        with self.assertRaisesRegex(binding.CampaignBindingError, "migration head does not match"):
            self._create_for_new_campaign("revision", expected_alembic_revision="b" * 12)

        dirty = self.application / "untracked.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(binding.CampaignBindingError, "application source repository must be clean"):
            self._create_for_new_campaign("dirty")
        dirty.unlink()

        application_link = self.root / "application-link"
        application_link.symlink_to(self.application, target_is_directory=True)
        with self.assertRaisesRegex(binding.CampaignBindingError, "application source repository"):
            self._create_for_new_campaign("symlink", application_source_repository=application_link)

        untrusted = self.root / "untrusted-application"
        untrusted.mkdir(mode=0o700)
        original_lstat = binding.os.lstat

        def lstat_with_untrusted_owner(path, *args, **kwargs):
            state = original_lstat(path, *args, **kwargs)
            if os.fspath(path) == os.fspath(untrusted):
                fields = list(state)
                fields[4] = 65534  # st_uid
                fields[5] = 65534  # st_gid
                return os.stat_result(fields)
            return state

        with mock.patch.object(binding.os, "lstat", side_effect=lstat_with_untrusted_owner):
            with self.assertRaisesRegex(binding.CampaignBindingError, "application source repository"):
                self._create_for_new_campaign("untrusted", application_source_repository=untrusted)

    def test_create_requires_detached_clean_checkouts_and_has_no_raw_tree_arguments(self) -> None:
        _git(self.control, "checkout", "-q", "-B", "fixture-branch", self.control_commit)
        with self.assertRaisesRegex(binding.CampaignBindingError, "control source repository must be detached"):
            self._create_for_new_campaign("branch")
        _git(self.control, "checkout", "-q", "--detach", self.control_commit)

        argv = [
            "create",
            "--campaign-directory",
            str(self.campaign_directory),
            "--campaign-id",
            self.campaign_id,
            "--application-source-repository",
            str(self.application),
            "--application-release-sha",
            self.application_commit,
            "--expected-alembic-revision",
            self.expected_revision,
            "--control-source-repository",
            str(self.control),
            "--control-commit",
            self.control_commit,
        ]
        parsed = binding._parser().parse_args(argv)
        self.assertFalse(hasattr(parsed, "application_release_tree"))
        self.assertFalse(hasattr(parsed, "control_tree"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            binding._parser().parse_args(argv + ["--application-release-tree", "f" * 40])

    def test_transport_request_derives_pins_and_recipients_from_binding(self) -> None:
        request = transport.request_from_campaign_binding(
            config=self.policy,
            campaign_binding_path=self.binding_path,
            source_site="bot_fi",
            destination_site="webapp_fi",
            object_kind=transport.BOOTSTRAP_OBJECT_KIND,
            object_id="bootstrap-source-fixture",
        )
        self.assertEqual(self.campaign_id, request.campaign_id)
        self.assertEqual(self.values["application_release_sha"], request.release_sha)
        self.assertEqual(self.values["control_commit"], request.control_commit)
        self.assertEqual(self.control_tree, request.control_tree)
        self.assertEqual(transport.SINGLE_MODE, request.mode)
        self.assertEqual((self.policy.webapp_fi_age_recipient,), tuple(request.recipients))

        with self.assertRaisesRegex(transport.SourceTransportError, "controller-local"):
            transport.request_from_campaign_binding(
                config=self.policy,
                campaign_binding_path=self.binding_path,
                source_site="webapp_fi",
                destination_site=transport.STATIC_DESTINATION_SITE,
                object_kind=transport.STATIC_OBJECT_KIND,
                object_id="static-source-fixture",
            )

    def test_publish_parser_rejects_raw_campaign_release_and_control_pins(self) -> None:
        argv = [
            "publish",
            "--config",
            "/tmp/controller-transport.json",
            "--campaign-binding",
            str(self.binding_path),
            "--source-site",
            "bot_fi",
            "--destination-site",
            "webapp_fi",
            "--object-kind",
            transport.BOOTSTRAP_OBJECT_KIND,
            "--object-id",
            "bootstrap-source-fixture",
            "--plaintext",
            "/tmp/plaintext",
            "--receipt",
            "/tmp/receipt.json",
        ]
        parsed = transport.parse_arguments(argv)
        self.assertEqual(self.binding_path, parsed.campaign_binding)
        self.assertFalse(hasattr(parsed, "campaign_id"))
        self.assertFalse(hasattr(parsed, "release_sha"))
        self.assertFalse(hasattr(parsed, "control_commit"))
        self.assertFalse(hasattr(parsed, "control_tree"))
        self.assertFalse(hasattr(parsed, "mode"))
        self.assertFalse(hasattr(parsed, "recipient"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            transport.parse_arguments(argv + ["--campaign-id", "arbitrary-campaign-20260730"])

    def test_publish_main_loads_binding_before_creating_the_object_storage_client(self) -> None:
        controller_config = transport.ControllerS3Config(
            policy=dataclasses.replace(
                self.policy,
                workspace=transport.contract.source_transport_workspace_for_campaign(self.campaign_id),
            ),
            credentials_file=self.root / "controller-s3-credentials.json",
            campaign_id=self.campaign_id,
        )
        argv = [
            "publish",
            "--config",
            str(self.root / "controller-transport.json"),
            "--campaign-binding",
            str(self.binding_path),
            "--source-site",
            "bot_fi",
            "--destination-site",
            "webapp_fi",
            "--object-kind",
            transport.BOOTSTRAP_OBJECT_KIND,
            "--object-id",
            "bootstrap-source-fixture",
            "--plaintext",
            str(self.root / "plain.bin"),
            "--receipt",
            str(self.root / "receipt.json"),
        ]
        with (
            mock.patch.object(transport, "load_controller_config", return_value=controller_config),
            mock.patch.object(transport, "create_s3_client", return_value=object()) as create_client,
            mock.patch.object(transport, "publish_controller_source_object", return_value={"status": "published"}) as publish,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, transport.main(argv))
        create_client.assert_called_once_with(controller_config)
        request = publish.call_args.kwargs["request"]
        self.assertEqual(self.campaign_id, request.campaign_id)
        self.assertEqual(self.values["application_release_sha"], request.release_sha)
        self.assertEqual(self.values["control_commit"], request.control_commit)
        self.assertEqual(self.control_tree, request.control_tree)

        invalid = json.loads(self.binding_path.read_bytes())
        invalid["tooling"]["control_tree"] = "f" * 40
        self.binding_path.write_bytes(binding.canonical_json_bytes(invalid) + b"\n")
        self.binding_path.chmod(0o600)
        with (
            mock.patch.object(transport, "load_controller_config", return_value=controller_config),
            mock.patch.object(transport, "create_s3_client") as blocked_client,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, transport.main(argv))
        blocked_client.assert_not_called()

    def test_publish_main_rejects_fi_originated_route_before_creating_client(self) -> None:
        controller_config = transport.ControllerS3Config(
            policy=dataclasses.replace(
                self.policy,
                workspace=transport.contract.source_transport_workspace_for_campaign(self.campaign_id),
            ),
            credentials_file=self.root / "controller-s3-credentials.json",
            campaign_id=self.campaign_id,
        )
        argv = [
            "publish",
            "--config",
            str(self.root / "controller-transport.json"),
            "--campaign-binding",
            str(self.binding_path),
            "--source-site",
            "webapp_fi",
            "--destination-site",
            transport.STATIC_DESTINATION_SITE,
            "--object-kind",
            transport.STATIC_OBJECT_KIND,
            "--object-id",
            "static-source-fixture",
            "--plaintext",
            str(self.root / "plain.bin"),
            "--receipt",
            str(self.root / "receipt.json"),
        ]
        with (
            mock.patch.object(transport, "load_controller_config", return_value=controller_config),
            mock.patch.object(transport, "create_s3_client") as blocked_client,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, transport.main(argv))
        blocked_client.assert_not_called()

    def test_publish_main_rejects_a_valid_other_campaign_config_before_client_creation(self) -> None:
        other_campaign = "campaign-binding-other-20260730"
        controller_config = transport.ControllerS3Config(
            policy=dataclasses.replace(
                self.policy,
                workspace=transport.contract.source_transport_workspace_for_campaign(other_campaign),
            ),
            credentials_file=self.root / "controller-s3-credentials.json",
            campaign_id=other_campaign,
        )
        argv = [
            "publish",
            "--config",
            str(self.root / "controller-transport.json"),
            "--campaign-binding",
            str(self.binding_path),
            "--source-site",
            "bot_fi",
            "--destination-site",
            "webapp_fi",
            "--object-kind",
            transport.BOOTSTRAP_OBJECT_KIND,
            "--object-id",
            "bootstrap-source-fixture",
            "--plaintext",
            str(self.root / "plain.bin"),
            "--receipt",
            str(self.root / "receipt.json"),
        ]
        with (
            mock.patch.object(transport, "load_controller_config", return_value=controller_config),
            mock.patch.object(transport, "create_s3_client") as blocked_client,
            mock.patch.object(transport, "publish_controller_source_object") as blocked_publish,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(2, transport.main(argv))
        blocked_client.assert_not_called()
        blocked_publish.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
