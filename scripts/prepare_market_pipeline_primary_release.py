#!/usr/bin/env python3
"""Render a release-bound PRIVATE_PRIMARY environment pair.

This is deliberately separate from the immutable Shadow preparer.  It performs
no Docker, SSH, database, or product mutation.  Its only output is two
root-owned Compose env files and one secret-free receipt, all bound to one Git
commit and one content-addressed image.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
        ReleaseContractError,
        _validate_pair,
        parse_env,
        resolve_role_image_ids,
        validate_source,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.prepare_market_pipeline_release import (
        DYNAMIC_VALUES,
        IMAGE_ID,
        PROJECT_NAME,
        RELEASE_SHA,
        ReleaseContractError,
        _validate_pair,
        parse_env,
        resolve_role_image_ids,
        validate_source,
    )


CONFIRMATION = "render-market-pipeline-private-primary"
SCHEMA = "market_pipeline_primary_release_pair/1.0"
ROLE_IMAGE_SCHEMA = "market_pipeline_primary_release_pair/1.1"
AUTHORIZED_BACKFILL_NOT_BEFORE_UTC = "2026-08-25T09:33:00Z"
AUTHORIZED_BACKFILL_SOURCE_CODES = (
    "MELTED_PRIMARY_FLOW,GROUP_1,GROUP_2"
)


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
    if role == "web":
        if (
            values.get("MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC")
            != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
            or values.get("MARKET_CAPTURE_BACKFILL_SOURCE_CODES")
            != AUTHORIZED_BACKFILL_SOURCE_CODES
        ):
            raise PrimaryReleaseError(
                "primary_release_authorized_backfill_contract_invalid"
            )
        try:
            maximum = int(values.get("MARKET_CAPTURE_BACKFILL_MAX_MESSAGES", ""))
        except ValueError as exc:
            raise PrimaryReleaseError(
                "primary_release_authorized_backfill_contract_invalid"
            ) from exc
        if not 2_000 <= maximum <= 1_000_000:
            raise PrimaryReleaseError(
                "primary_release_authorized_backfill_contract_invalid"
            )
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


def derive_source(
    *,
    role: str,
    rendered_env: Path,
    source_env: Path,
    research_key_file: Path | None = None,
    capture_backfill_not_before_utc: str | None = None,
    capture_backfill_max_messages: int | None = None,
) -> dict[str, object]:
    """Create a topology source from an existing release-bound env.

    Only release-controlled values are removed.  The Web research key path may
    be added explicitly because older Shadow releases predate that required
    encrypted archive.  No secret value is read; only absolute file paths are
    retained in the topology source.
    """

    if role not in {"web", "bot"}:
        raise PrimaryReleaseError("primary_release_role_invalid")
    values = parse_env(rendered_env, secure_input=True)
    source = {key: value for key, value in values.items() if key not in DYNAMIC_VALUES}
    if role == "web":
        if research_key_file is None or not research_key_file.is_absolute():
            raise PrimaryReleaseError("primary_release_research_key_path_required")
        source["MARKET_RESEARCH_ENCRYPTION_KEY_FILE"] = str(research_key_file)
        if capture_backfill_not_before_utc is not None:
            try:
                cutoff = datetime.fromisoformat(
                    capture_backfill_not_before_utc.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise PrimaryReleaseError(
                    "primary_release_backfill_cutoff_invalid"
                ) from exc
            if (
                cutoff.tzinfo is None
                or cutoff.utcoffset() != timezone.utc.utcoffset(cutoff)
                or not capture_backfill_not_before_utc.endswith("Z")
            ):
                raise PrimaryReleaseError(
                    "primary_release_backfill_cutoff_invalid"
                )
            source["MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"] = (
                cutoff.astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            maximum = (
                100_000
                if capture_backfill_max_messages is None
                else int(capture_backfill_max_messages)
            )
            if not 2_000 <= maximum <= 1_000_000:
                raise PrimaryReleaseError(
                    "primary_release_backfill_max_messages_invalid"
                )
            source["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"] = str(maximum)
            source["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"] = (
                AUTHORIZED_BACKFILL_SOURCE_CODES
            )
        elif capture_backfill_max_messages is not None:
            raise PrimaryReleaseError("primary_release_backfill_cutoff_required")
    elif any(
        value is not None
        for value in (
            research_key_file,
            capture_backfill_not_before_utc,
            capture_backfill_max_messages,
        )
    ):
        raise PrimaryReleaseError("primary_release_web_only_option_forbidden")
    validate_source(role, source)
    _atomic_write(source_env, _env_bytes(source), exclusive=True)
    return {
        "schema": "market_pipeline_primary_topology_source/1.0",
        "role": role,
        "rendered_env_sha256": _digest(rendered_env),
        "source_env_sha256": _digest(source_env),
        "dynamic_values_removed": sorted(DYNAMIC_VALUES.intersection(values)),
        "secret_values_read": False,
        "capture_backfill_boundary_added": bool(
            role == "web" and capture_backfill_not_before_utc is not None
        ),
    }


def render_pair(
    *,
    web_source: Path,
    bot_source: Path,
    web_env: Path,
    bot_env: Path,
    receipt: Path,
    release_sha: str,
    release_tree: str,
    image_id: str | None,
    project_name: str,
    web_image_id: str | None = None,
    bot_image_id: str | None = None,
) -> dict[str, object]:
    if not RELEASE_SHA.fullmatch(release_sha) or not RELEASE_SHA.fullmatch(release_tree):
        raise PrimaryReleaseError("primary_release_git_identity_invalid")
    try:
        image_ids = resolve_role_image_ids(
            image_id=image_id,
            web_image_id=web_image_id,
            bot_image_id=bot_image_id,
        )
    except ReleaseContractError as exc:
        raise PrimaryReleaseError("primary_release_image_identity_invalid") from exc
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
                image_id=image_ids[role],
                project_name=project_name,
            )
        )
        _atomic_write(output, payload, exclusive=True)
        outputs[role] = {
            "source_sha256": _digest(web_source if role == "web" else bot_source),
            "output_sha256": sha256(payload).hexdigest(),
            "product_snapshot_root": sources[role][
                "MARKET_PRODUCT_SNAPSHOT_ROOT"
            ],
        }
    common_image = len(set(image_ids.values())) == 1
    document: dict[str, object] = {
        "schema": SCHEMA if common_image else ROLE_IMAGE_SCHEMA,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "project_name": project_name,
        "feed_mode": "PRIVATE_PRIMARY",
        "private_primary_allowed": True,
        "expected_snapshot_lane": "PRIVATE_PRIMARY",
        "product_authority_changed": False,
        "legacy_retirement_authorized": False,
        "capture_backfill": {
            "not_before_utc": sources["web"]["MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"],
            "source_codes": sources["web"]["MARKET_CAPTURE_BACKFILL_SOURCE_CODES"].split(","),
            "max_messages": int(sources["web"]["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"]),
        },
        "roles": outputs,
        "secrets_disclosed": False,
    }
    if common_image:
        document["image_id"] = image_ids["web"]
    else:
        document["image_ids"] = image_ids
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
    image_id: str | None,
    project_name: str,
    web_image_id: str | None = None,
    bot_image_id: str | None = None,
) -> dict[str, object]:
    try:
        image_ids = resolve_role_image_ids(
            image_id=image_id,
            web_image_id=web_image_id,
            bot_image_id=bot_image_id,
        )
    except ReleaseContractError as exc:
        raise PrimaryReleaseError("primary_release_image_identity_invalid") from exc
    document = json.loads(receipt.read_text(encoding="utf-8"))
    common_image = len(set(image_ids.values())) == 1
    expected_schema = SCHEMA if common_image else ROLE_IMAGE_SCHEMA
    expected_image_identity = (
        {"image_id": image_ids["web"]}
        if common_image
        else {"image_ids": image_ids}
    )
    if document.get("schema") != expected_schema or any(
        document.get(key) != expected
        for key, expected in {
            "release_sha": release_sha,
            "release_tree": release_tree,
            **expected_image_identity,
            "project_name": project_name,
            "feed_mode": "PRIVATE_PRIMARY",
            "private_primary_allowed": True,
            "expected_snapshot_lane": "PRIVATE_PRIMARY",
            "product_authority_changed": False,
            "legacy_retirement_authorized": False,
            "capture_backfill": {
                "not_before_utc": AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                "source_codes": AUTHORIZED_BACKFILL_SOURCE_CODES.split(","),
                "max_messages": int(
                    _source("web", web_source)[
                        "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"
                    ]
                ),
            },
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
            image_id=image_ids[role],
            project_name=project_name,
        )
        if values != expected_values:
            raise PrimaryReleaseError("primary_release_output_content_mismatch")
        role_receipt = roles.get(role)
        if not isinstance(role_receipt, Mapping) or role_receipt != {
            "source_sha256": _digest(source),
            "output_sha256": _digest(output),
            "product_snapshot_root": source_values[
                "MARKET_PRODUCT_SNAPSHOT_ROOT"
            ],
        }:
            raise PrimaryReleaseError("primary_release_output_digest_mismatch")
    _validate_pair(
        _source("web", web_source),
        _source("bot", bot_source),
    )
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    derive = commands.add_parser("derive-source")
    derive.add_argument("--role", choices=("web", "bot"), required=True)
    derive.add_argument("--rendered-env", type=Path, required=True)
    derive.add_argument("--source-env", type=Path, required=True)
    derive.add_argument("--research-key-file", type=Path)
    derive.add_argument("--capture-backfill-not-before-utc")
    derive.add_argument("--capture-backfill-max-messages", type=int)
    for name in ("render-pair", "verify-pair"):
        command = commands.add_parser(name)
        command.add_argument("--web-source", type=Path, required=True)
        command.add_argument("--bot-source", type=Path, required=True)
        command.add_argument("--web-env", type=Path, required=True)
        command.add_argument("--bot-env", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
        command.add_argument("--release-sha", required=True)
        command.add_argument("--release-tree", required=True)
        command.add_argument("--image-id")
        command.add_argument("--web-image-id")
        command.add_argument("--bot-image-id")
        command.add_argument("--project-name", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise PrimaryReleaseError("primary_release_confirmation_invalid")
        if args.command == "derive-source":
            result = derive_source(
                role=args.role,
                rendered_env=args.rendered_env,
                source_env=args.source_env,
                research_key_file=args.research_key_file,
                capture_backfill_not_before_utc=args.capture_backfill_not_before_utc,
                capture_backfill_max_messages=args.capture_backfill_max_messages,
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "schema": result["schema"],
                        "role": result["role"],
                        "secret_values_read": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
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
            web_image_id=args.web_image_id,
            bot_image_id=args.bot_image_id,
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
