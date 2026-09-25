#!/usr/bin/env python3
"""Hermetic fault and characterization tests for cycle private json reader.

Covers _read_cycle_private_json inside _open_existing_cycle_run_directory
context, positive allowed basenames, negative matrix, and actual in-flight
mutation/swap tests verifying stat fingerprint comparisons.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    _read_cycle_private_json,
)

CYCLE_ID = "cycle-0001-20260925T120000Z-1"
ALLOWED_BASENAMES = (
    "preparation-startup.json",
    "broker-reservation.json",
)


def _setup_cycle_run(temp_dir: str, cycle_id: str = CYCLE_ID) -> tuple[Path, Path]:
    root = Path(temp_dir).resolve()
    os.chmod(root, 0o700)
    current = root
    for comp in (".local", "two-site-refactor", "autopilot", "runs", cycle_id):
        current = current / comp
        current.mkdir(mode=0o700, exist_ok=True)
        os.chmod(current, 0o700)
    return root, current


@pytest.mark.parametrize("filename,payload", [
    (
        "preparation-startup.json",
        {"controller_run_id": CYCLE_ID, "phase": "init", "seq": 1},
    ),
    (
        "broker-reservation.json",
        {"controller_run_id": CYCLE_ID, "request_id": "f" * 32, "role": "worker"},
    ),
])
def test_positive_cycle_private_reader(filename: str, payload: dict) -> None:
    compact_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    saved_fd = -1

    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target_file = run_path / filename
        target_file.write_bytes(compact_bytes)
        os.chmod(target_file, 0o600)

        initial_listing = sorted(os.listdir(run_path))
        assert initial_listing == [filename]

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, verify_identity):
            saved_fd = r_fd
            assert r_path == run_path
            read_payload, read_bytes = _read_cycle_private_json(r_path, r_fd, filename)
            verify_identity()

            assert read_payload == payload
            assert read_bytes == compact_bytes
            assert sorted(os.listdir(run_path)) == [filename]

        with pytest.raises(OSError) as exc_info:
            os.fstat(saved_fd)
        assert exc_info.value.errno == errno.EBADF


def test_negative_missing_file() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="cannot lstat cycle private file"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_symlink() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        backing = run_path / "other.json"
        backing.write_bytes(b'{"key":"val"}')
        os.chmod(backing, 0o600)
        sym = run_path / "preparation-startup.json"
        sym.symlink_to(backing)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="is not a regular file"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_hardlink() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(b'{"key":"val"}')
        os.chmod(target, 0o600)
        other = run_path / "other.json"
        os.link(target, other)
        assert target.stat().st_nlink == 2

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="has invalid link count"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o700, 0o400])
def test_negative_wrong_mode(mode: int) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(b'{"key":"val"}')
        os.chmod(target, mode)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="mode must be 0600"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_wrong_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(b'{"key":"val"}')
        os.chmod(target, 0o600)

        original_euid = os.geteuid()
        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            monkeypatch.setattr(os, "geteuid", lambda: original_euid + 1000)
            with pytest.raises(BrokerError, match="owner mismatch"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_oversized() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        raw = b'{"pad":"' + b'x' * (4097 - len(b'{"pad":"\"}')) + b'"}'
        assert len(raw) == 4097
        target.write_bytes(raw)
        os.chmod(target, 0o600)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="exceeds byte limit"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_invalid_utf8() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(b'{"bad": "\xff\xfe"}')
        os.chmod(target, 0o600)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="is not valid UTF-8"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


def test_negative_duplicate_json_keys() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(b'{"dup": 1, "dup": 2}')
        os.chmod(target, 0o600)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="is not valid JSON"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


@pytest.mark.parametrize("non_object_content", [
    b'[1, 2, 3]',
    b'"just a string"',
    b'12345',
    b'true',
])
def test_negative_non_object_json(non_object_content: bytes) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        target = run_path / "preparation-startup.json"
        target.write_bytes(non_object_content)
        os.chmod(target, 0o600)

        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="must be a JSON object|is not valid JSON"):
                _read_cycle_private_json(r_path, r_fd, "preparation-startup.json")


@pytest.mark.parametrize("forbidden_basename", [
    "../preparation-startup.json",
    "other.json",
    "status.json",
    "/tmp/evil.json",
    "broker-reservation.json.bak",
    "",
])
def test_negative_forbidden_and_traversal_basenames(forbidden_basename: str) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir)
        with _open_existing_cycle_run_directory(root, CYCLE_ID) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError, match="cycle private filename must be"):
                _read_cycle_private_json(r_path, r_fd, forbidden_basename)


def test_same_inode_same_size_mutation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    cycle_id = CYCLE_ID
    filename = "preparation-startup.json"
    orig_payload = {"status": "original_record"}
    orig_bytes = json.dumps(orig_payload, separators=(",", ":")).encode("utf-8")
    alt_payload = {"status": "tampered_record"}
    alt_bytes = json.dumps(alt_payload, separators=(",", ":")).encode("utf-8")
    assert len(orig_bytes) == len(alt_bytes)

    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir, cycle_id)
        target_path = run_path / filename
        target_path.write_bytes(orig_bytes)
        os.chmod(target_path, 0o600)

        initial_stat = os.lstat(target_path)
        target_dev = initial_stat.st_dev
        target_ino = initial_stat.st_ino

        real_read = os.read
        mutated = False

        def patched_read(fd: int, n: int) -> bytes:
            nonlocal mutated
            try:
                fd_stat = os.fstat(fd)
            except OSError:
                return real_read(fd, n)
            if (fd_stat.st_dev, fd_stat.st_ino) == (target_dev, target_ino):
                chunk = real_read(fd, n)
                if chunk and not mutated:
                    time.sleep(0.01)
                    sep_fd = os.open(target_path, os.O_WRONLY)
                    try:
                        sep_stat = os.fstat(sep_fd)
                        assert sep_stat.st_ino == target_ino, "mutation must target same inode"
                        written = os.write(sep_fd, alt_bytes)
                        assert written == len(alt_bytes)
                        os.fsync(sep_fd)
                    finally:
                        os.close(sep_fd)
                    mutated = True
                return chunk
            return real_read(fd, n)

        monkeypatch.setattr(os, "read", patched_read)

        with _open_existing_cycle_run_directory(root, cycle_id) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError) as exc_info:
                _read_cycle_private_json(r_path, r_fd, filename)

        assert mutated is True
        assert "metadata changed after read" in str(exc_info.value)
        final_stat = os.lstat(target_path)
        assert final_stat.st_ino == target_ino
        assert target_path.read_bytes() == alt_bytes


def test_pathname_swap_during_read_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    cycle_id = CYCLE_ID
    filename = "broker-reservation.json"
    orig_payload = {"reservation": "original_req"}
    orig_bytes = json.dumps(orig_payload, separators=(",", ":")).encode("utf-8")
    swap_payload = {"reservation": "swapped_req_"}
    swap_bytes = json.dumps(swap_payload, separators=(",", ":")).encode("utf-8")

    with tempfile.TemporaryDirectory() as temp_dir:
        root, run_path = _setup_cycle_run(temp_dir, cycle_id)
        target_path = run_path / filename
        target_path.write_bytes(orig_bytes)
        os.chmod(target_path, 0o600)

        swap_path = run_path / "swap.tmp"
        swap_path.write_bytes(swap_bytes)
        os.chmod(swap_path, 0o600)

        initial_stat = os.lstat(target_path)
        target_dev = initial_stat.st_dev
        target_ino = initial_stat.st_ino
        swap_stat = os.lstat(swap_path)
        assert swap_stat.st_ino != target_ino

        real_read = os.read
        swapped = False

        def patched_read(fd: int, n: int) -> bytes:
            nonlocal swapped
            try:
                fd_stat = os.fstat(fd)
            except OSError:
                return real_read(fd, n)
            if (fd_stat.st_dev, fd_stat.st_ino) == (target_dev, target_ino):
                chunk = real_read(fd, n)
                if chunk and not swapped:
                    os.replace(swap_path, target_path)
                    swapped = True
                return chunk
            return real_read(fd, n)

        monkeypatch.setattr(os, "read", patched_read)

        with _open_existing_cycle_run_directory(root, cycle_id) as (r_path, r_fd, _verify):
            with pytest.raises(BrokerError) as exc_info:
                _read_cycle_private_json(r_path, r_fd, filename)

        assert swapped is True
        assert (
            "has invalid link count" in str(exc_info.value)
            or "metadata changed after read" in str(exc_info.value)
        )
        final_stat = os.lstat(target_path)
        assert final_stat.st_ino != target_ino
        assert target_path.read_bytes() == swap_bytes
