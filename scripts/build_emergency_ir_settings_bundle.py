#!/usr/bin/env python3
"""Build one exact, root-only Emergency IR settings.tar without network I/O.

The default bundle contains only the public trading configuration and a
Telegram WebApp initData validation credential under its narrow Emergency
name.  It never accepts a BOT_TOKEN command-line argument, starts no bot, and
does not copy peer, sync, writer, or notification credentials.  The optional
SMS profile is explicit and has an exact five-member layout.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT_TEXT = str(REPO_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != _REPO_ROOT_TEXT]
sys.path.insert(0, _REPO_ROOT_TEXT)

from scripts import emergency_ir_standalone_activate as activation


MAX_SETTINGS_MEMBER_BYTES = activation.MAX_SETTINGS_MEMBER_BYTES
MAX_SECRET_BYTES = activation.MAX_SECRET_BYTES
SMS_PARAMETER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", re.ASCII)


class EmergencySettingsBundleError(RuntimeError):
    pass


def _fail(message: str) -> None:
    raise EmergencySettingsBundleError(message)


def _secure_output_directory(path: Path) -> None:
    if not path.is_absolute():
        _fail("settings bundle output directory is not owner-controlled")
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        try:
            state = current.lstat()
        except OSError as exc:
            raise EmergencySettingsBundleError("settings bundle output directory cannot be inspected") from exc
        sticky_tmp = (
            current == Path("/tmp")
            and state.st_uid == 0
            and bool(stat.S_IMODE(state.st_mode) & stat.S_ISVTX)
        )
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid not in {0, os.geteuid()}
            or (stat.S_IMODE(state.st_mode) & 0o022 and (current == path or not sticky_tmp))
        ):
            _fail("settings bundle output directory is not owner-controlled")
    try:
        final = path.lstat()
    except OSError as exc:
        raise EmergencySettingsBundleError("settings bundle output directory cannot be inspected") from exc
    if final.st_uid != os.geteuid():
        _fail("settings bundle output directory is not owner-controlled")


def _read_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    private: bool,
) -> bytes:
    if not path.is_absolute():
        _fail(f"{label} path must be absolute")
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencySettingsBundleError(f"{label} cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or (private and stat.S_IMODE(before.st_mode) & 0o077)
        or not 1 <= before.st_size <= maximum_bytes
    ):
        _fail(f"{label} must be one bounded owner-controlled regular file")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail(f"{label} changed while being opened")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) != opened.st_size or len(payload) > maximum_bytes or any(
            getattr(opened, field) != getattr(after, field) for field in fields
        ):
            _fail(f"{label} changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise EmergencySettingsBundleError(f"{label} cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _one_line_secret(path: Path, *, label: str) -> bytes:
    payload = _read_regular(path, label=label, maximum_bytes=MAX_SECRET_BYTES, private=True)
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    try:
        value = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EmergencySettingsBundleError(f"{label} is invalid") from exc
    if not value or any(ord(character) < 33 or ord(character) > 126 for character in value):
        _fail(f"{label} is invalid")
    return value.encode("ascii")


def _trading_settings(path: Path) -> bytes:
    payload = _read_regular(
        path,
        label="trading settings",
        maximum_bytes=MAX_SETTINGS_MEMBER_BYTES,
        private=False,
    )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmergencySettingsBundleError("trading settings must be strict JSON") from exc
    if not isinstance(value, dict) or not value:
        _fail("trading settings must be one non-empty JSON object")
    return payload


def _write_create_only(path: Path, payload: bytes) -> None:
    if not path.is_absolute():
        _fail("settings bundle output must be absolute")
    _secure_output_directory(path.parent)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise EmergencySettingsBundleError("refusing to overwrite an existing settings bundle") from exc
    except OSError as exc:
        raise EmergencySettingsBundleError("settings bundle cannot be written") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _tar_payload(members: Mapping[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name in sorted(members):
            payload = members[name]
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    payload = raw.getvalue()
    if not 1 <= len(payload) <= activation.MAX_SETTINGS_BYTES:
        _fail("settings bundle exceeds its fixed size bound")
    return payload


def build_settings_bundle(
    *,
    output: Path,
    trading_settings: Path,
    webapp_initdata_token: Path,
    profile: str = "telegram-only",
    smsir_api_key: Path | None = None,
    smsir_otp_template_id: Path | None = None,
    smsir_otp_template_parameter: Path | None = None,
) -> dict[str, object]:
    if profile not in {"telegram-only", "sms-otp"}:
        _fail("Emergency auth profile is invalid")
    members: dict[str, bytes] = {
        "trading_settings.json": _trading_settings(trading_settings),
        "webapp_initdata_token": _one_line_secret(
            webapp_initdata_token,
            label="WebApp initData token",
        ),
    }
    sms_inputs = (smsir_api_key, smsir_otp_template_id, smsir_otp_template_parameter)
    if profile == "telegram-only":
        if any(item is not None for item in sms_inputs):
            _fail("SMS material requires the explicit sms-otp profile")
    else:
        if any(item is None for item in sms_inputs):
            _fail("sms-otp profile requires the complete fixed SMS material")
        assert smsir_api_key is not None
        assert smsir_otp_template_id is not None
        assert smsir_otp_template_parameter is not None
        template_id = _one_line_secret(smsir_otp_template_id, label="SMS.ir template ID")
        template_id_text = template_id.decode("ascii")
        if not template_id_text.isdecimal() or not 0 < int(template_id_text) <= 2_147_483_647:
            _fail("SMS.ir template ID is invalid")
        parameter = _one_line_secret(smsir_otp_template_parameter, label="SMS.ir template parameter")
        if SMS_PARAMETER_RE.fullmatch(parameter.decode("ascii")) is None:
            _fail("SMS.ir template parameter is invalid")
        members.update(
            {
                "smsir_api_key": _one_line_secret(smsir_api_key, label="SMS.ir API key"),
                "smsir_otp_template_id": template_id,
                "smsir_otp_template_parameter": parameter,
            }
        )
    payload = _tar_payload(members)
    _write_create_only(output, payload)
    try:
        activation.read_settings_bundle(settings_tar=output, profile=profile)
    except Exception as exc:
        raise EmergencySettingsBundleError("written settings bundle failed its activation contract") from exc
    return {
        "status": "built-local-only",
        "profile": profile,
        "output": str(output),
        "bytes": len(payload),
        "member_names": sorted(members),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trading-settings", type=Path, required=True)
    parser.add_argument("--webapp-initdata-token-file", type=Path, required=True)
    parser.add_argument("--profile", choices=("telegram-only", "sms-otp"), default="telegram-only")
    parser.add_argument("--smsir-api-key-file", type=Path)
    parser.add_argument("--smsir-otp-template-id-file", type=Path)
    parser.add_argument("--smsir-otp-template-parameter-file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        if not sys.flags.isolated:
            _fail("Emergency settings builder must be launched with python3 -I -B")
        args = parse_args(argv)
        result = build_settings_bundle(
            output=args.output,
            trading_settings=args.trading_settings,
            webapp_initdata_token=args.webapp_initdata_token_file,
            profile=args.profile,
            smsir_api_key=args.smsir_api_key_file,
            smsir_otp_template_id=args.smsir_otp_template_id_file,
            smsir_otp_template_parameter=args.smsir_otp_template_parameter_file,
        )
    except EmergencySettingsBundleError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
