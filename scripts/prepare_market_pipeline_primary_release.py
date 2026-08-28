#!/usr/bin/env python3
"""Render a release-bound PRIVATE_PRIMARY environment pair.

This is deliberately separate from the immutable Shadow preparer.  It performs
no Docker, SSH, database, or product mutation.  Its only output is two
root-owned Compose env files and one secret-free receipt, all bound to one Git
commit and one content-addressed image.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
from typing import Mapping, Sequence

if __package__:
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        PROJECT_NAME,
        RELEASE_SHA,
        _validate_pair,
        parse_env,
        validate_source,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        PROJECT_NAME,
        RELEASE_SHA,
        _validate_pair,
        parse_env,
        validate_source,
    )


CONFIRMATION = "render-market-pipeline-private-primary"
SCHEMA = "market_pipeline_primary_release_pair/1.0"


class PrimaryReleaseError(RuntimeError):
    """Stable, secret-free rendering refusal."""


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _secure_parent(path: Path) -> None:
    parent = path.parent
    if not parent.is_absolute() or parent in {
        Path("/"),
        Path("/root"),
        Path("/srv"),
        Path("/tmp"),
        Path("/var/tmp"),
    }:
        raise PrimaryReleaseError("primary_release_parent_invalid")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PrimaryReleaseError("primary_release_parent_owner_mode_invalid")


def _atomic_write(path: Path, payload: bytes, *, exclusive: bool) -> None:
    _secure_parent(path)
    if path.exists() or path.is_symlink():
        if exclusive:
            raise PrimaryReleaseError("primary_release_output_exists")
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise PrimaryReleaseError("primary_release_output_invalid")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _source(role: str, path: Path) -> dict[str, str]:
    values = parse_env(path, secure_input=True)
    if DYNAMIC_VALUES.intersection(values):
        raise PrimaryReleaseError("primary_release_source_contains_dynamic_values")
    validate_source(role, values)
    return values


def _rendered(
    source: Mapping[str, str],
    *,
    release_sha: str,
    image_id: str,
    project_name: str,
) -> dict[str, str]:
    values = dict(source)
    values.update(
        {
            "MARKET_PIPELINE_IMAGE": image_id,
            "MARKET_PIPELINE_RELEASE_SHA": release_sha,
            "MARKET_PIPELINE_MODE": "live",
            "MARKET_PIPELINE_PROJECT_NAME": project_name,
            "MARKET_PIPELINE_FEED_MODE": "PRIVATE_PRIMARY",
            "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "1",
            "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_PRIMARY",
        }
    )
    return values


def _env_bytes(values: Mapping[str, str]) -> bytes:
    return "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode()


def render_pair(
    *,
    web_source: Path,
    bot_source: Path,
    web_env: Path,
    bot_env: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    project_name: str,
) -> dict[str, object]:
    if not RELEASE_SHA.fullmatch(release_sha) or not RELEASE_SHA.fullmatch(release_tree):
        raise PrimaryReleaseError("primary_release_git_identity_invalid")
    if not IMAGE_ID.fullmatch(image_id):
        raise PrimaryReleaseError("primary_release_image_identity_invalid")
    if not PROJECT_NAME.fullmatch(project_name):
        raise PrimaryReleaseError("primary_release_project_name_invalid")
    sources = {
        "web": _source("web", web_source),
        "bot": _source("bot", bot_source),
    }
    _validate_pair(sources["web"], sources["bot"])
    outputs: dict[str, dict[str, str]] = {}
    for role, output in (("web", web_env), ("bot", bot_env)):
        payload = _env_bytes(
            _rendered(
                sources[role],
                release_sha=release_sha,
                image_id=image_id,
                project_name=project_name,
            )
        )
        _atomic_write(output, payload, exclusive=True)
        outputs[role] = {
            "source_sha256": _digest(web_source if role == "web" else bot_source),
            "output_sha256": sha256(payload).hexdigest(),
        }
    document: dict[str, object] = {
        "schema": SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "image_id": image_id,
        "project_name": project_name,
        "feed_mode": "PRIVATE_PRIMARY",
        "private_primary_allowed": True,
        "expected_snapshot_lane": "PRIVATE_PRIMARY",
        "product_authority_changed": False,
        "legacy_retirement_authorized": False,
        "roles": outputs,
        "secrets_disclosed": False,
    }
    _atomic_write(
        receipt,
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        exclusive=True,
    )
    return document


def verify_pair(
    *,
    web_source: Path,
    bot_source: Path,
    web_env: Path,
    bot_env: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str,
    project_name: str,
) -> dict[str, object]:
    document = json.loads(receipt.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or any(
        document.get(key) != expected
        for key, expected in {
            "release_sha": release_sha,
            "release_tree": release_tree,
            "image_id": image_id,
            "project_name": project_name,
            "feed_mode": "PRIVATE_PRIMARY",
            "private_primary_allowed": True,
            "expected_snapshot_lane": "PRIVATE_PRIMARY",
            "product_authority_changed": False,
            "legacy_retirement_authorized": False,
            "secrets_disclosed": False,
        }.items()
    ):
        raise PrimaryReleaseError("primary_release_receipt_identity_invalid")
    roles = document.get("roles")
    if not isinstance(roles, Mapping) or set(roles) != {"web", "bot"}:
        raise PrimaryReleaseError("primary_release_receipt_roles_invalid")
    for role, source, output in (
        ("web", web_source, web_env),
        ("bot", bot_source, bot_env),
    ):
        source_values = _source(role, source)
        values = parse_env(output, secure_input=True)
        expected_values = _rendered(
            source_values,
            release_sha=release_sha,
            image_id=image_id,
            project_name=project_name,
        )
        if values != expected_values:
            raise PrimaryReleaseError("primary_release_output_content_mismatch")
        role_receipt = roles.get(role)
        if not isinstance(role_receipt, Mapping) or role_receipt != {
            "source_sha256": _digest(source),
            "output_sha256": _digest(output),
        }:
            raise PrimaryReleaseError("primary_release_output_digest_mismatch")
    _validate_pair(
        _source("web", web_source),
        _source("bot", bot_source),
    )
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render-pair", "verify-pair"))
    parser.add_argument("--web-source", type=Path, required=True)
    parser.add_argument("--bot-source", type=Path, required=True)
    parser.add_argument("--web-env", type=Path, required=True)
    parser.add_argument("--bot-env", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise PrimaryReleaseError("primary_release_confirmation_invalid")
        operation = render_pair if args.command == "render-pair" else verify_pair
        result = operation(
            web_source=args.web_source,
            bot_source=args.bot_source,
            web_env=args.web_env,
            bot_env=args.bot_env,
            receipt=args.receipt,
            release_sha=args.release_sha,
            release_tree=args.release_tree,
            image_id=args.image_id,
            project_name=args.project_name,
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "schema": result["schema"],
                    "release_sha": result["release_sha"],
                    "feed_mode": "PRIVATE_PRIMARY",
                    "product_authority_changed": False,
                    "legacy_retirement_authorized": False,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError, PrimaryReleaseError) as exc:
        print(
            json.dumps(
                {"status": "FAIL", "reason_code": str(exc), "secrets_disclosed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
