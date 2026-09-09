"""Regression: a working --version/login is insufficient without runtime siblings."""
import os
from unittest.mock import patch

import pytest

from scripts import two_site_codex_autopilot as ap


def bundle(tmp_path, monkeypatch):
    host = tmp_path / "codex-code-mode-host"
    host.write_bytes(b"offline host fixture")
    host.chmod(0o555)
    manifest = tmp_path / "codex-package.json"
    manifest.write_text('{"version":"fixture"}')
    manifest.chmod(0o444)
    pins = {host.name: ap.file_sha256(host), manifest.name: ap.file_sha256(manifest)}
    monkeypatch.setattr(ap, "CODEX_RUNTIME_FILES", pins)
    return host, manifest, pins


def test_complete_bundle_returns_exact_hashes(tmp_path, monkeypatch):
    _, _, pins = bundle(tmp_path, monkeypatch)
    assert ap.codex_runtime_identity(tmp_path / "codex") == pins


@pytest.mark.parametrize("mutation", ["missing", "hash", "symlink", "hardlink", "writable", "not_executable", "fifo", "oversized"])
def test_runtime_files_fail_closed(tmp_path, monkeypatch, mutation):
    host, _, _ = bundle(tmp_path, monkeypatch)
    if mutation == "missing": host.unlink()
    if mutation == "hash":
        host.chmod(0o755)
        host.write_bytes(b"different fixture")
        host.chmod(0o555)
    if mutation == "symlink":
        destination = tmp_path / "other-host"
        host.rename(destination)
        host.symlink_to(destination)
    if mutation == "hardlink": os.link(host, tmp_path / "alias")
    if mutation == "writable": host.chmod(0o755)
    if mutation == "not_executable": host.chmod(0o444)
    if mutation == "fifo":
        host.unlink()
        os.mkfifo(host)
    if mutation == "oversized":
        host.chmod(0o755)
        with host.open("wb") as stream:
            stream.truncate(100_000_001)  # Sparse, no large test allocation.
        host.chmod(0o555)
    with pytest.raises(ap.AutopilotError):
        ap.codex_runtime_identity(tmp_path / "codex")


def test_missing_companion_rejected_before_cli_or_provider(tmp_path, monkeypatch):
    cli = tmp_path / "codex"
    cli.write_bytes(b"offline main executable fixture")
    cli.chmod(0o555)
    monkeypatch.setattr(ap, "EXPECTED_CODEX_CLI_SHA256", ap.file_sha256(cli))
    with patch.object(ap.subprocess, "Popen") as process:
        with pytest.raises(ap.AutopilotError, match="cannot open Codex runtime file"):
            ap.codex_identity(cli, tmp_path)
    process.assert_not_called()


def test_runtime_identity_checked_again_after_readiness(tmp_path, monkeypatch):
    cli = tmp_path / "codex"
    cli.write_text(f"#!/usr/bin/env python3\nprint('{ap.EXPECTED_CODEX_CLI_VERSION}')\n")
    cli.chmod(0o555)
    monkeypatch.setattr(ap, "EXPECTED_CODEX_CLI_SHA256", ap.file_sha256(cli))
    with patch.object(ap, "codex_runtime_identity", side_effect=[{"host": "before"}, {"host": "after"}]):
        with pytest.raises(ap.AutopilotError, match="runtime changed during readiness"):
            ap.codex_identity(cli, tmp_path)
