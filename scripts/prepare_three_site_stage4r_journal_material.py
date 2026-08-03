#!/usr/bin/env python3
"""Create one non-rotating Stage 4R environment with journal credentials.

The output is an owner-only full Compose environment.  It is intentionally
created once on the controller and then delivered to every role through the
private, versioned Object Storage transport; neither the generated secrets nor
the environment file are sent through SSH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text, write_secure_new_bytes
from scripts.render_three_site_staging_role_compose import (
    canonical_role_env_bytes,
    parse_env_values,
)


RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
SOURCE_ROOT_RE = re.compile(
    r"^/srv/trading-bot-three-site-staging-data/releases/([0-9a-f]{40})/source$"
)
JOURNAL_KEY_ID = "staging-fi-journal-v1"
JOURNAL_KEYS = frozenset(
    {
        "BOT_FI_JOURNAL_DB_PASSWORD",
        "BOT_FI_SAME_REGION_JOURNAL_KEYS_JSON",
        "WEBAPP_FI_SAME_REGION_JOURNAL_KEYS_JSON",
        "WEBAPP_FI_SAME_REGION_JOURNAL_ENCRYPTION_KEY_ID",
        "WEBAPP_FI_SAME_REGION_JOURNAL_ENCRYPTION_SECRET",
        "STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED",
        "STAGING_WEBAPP_FI_MAX_PREPARED_TRANSACTIONS",
    }
)


class JournalMaterialError(RuntimeError):
    """The staged journal environment cannot be constructed safely."""


def _token(factory: Callable[[int], str], length: int) -> str:
    value = str(factory(length))
    if len(value) < 32 or any(character.isspace() for character in value):
        raise JournalMaterialError("generated journal secret is unsafe")
    return value


def build_environment(
    source: str,
    *,
    release_sha: str,
    staging_source_root: str,
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> tuple[bytes, dict[str, str]]:
    """Return an exact release env and redacted identity metadata.

    Existing journal variables are rejected.  Re-running preparation against a
    partially created material file must never silently rotate an in-use key.
    The first deployment keeps two-phase coordination disabled; it is enabled
    only after the independent runtime drill in Stage 4R.
    """

    release = str(release_sha).lower()
    if RELEASE_RE.fullmatch(release) is None:
        raise JournalMaterialError("release SHA must be exactly 40 lowercase hex characters")
    source_root = str(staging_source_root)
    source_root_match = SOURCE_ROOT_RE.fullmatch(source_root)
    if source_root_match is None or source_root_match.group(1) != release:
        raise JournalMaterialError("staging source root must bind the exact release SHA")
    values = parse_env_values(source)
    missing = {"STAGING_RELEASE_SHA", "STAGING_SOURCE_ROOT"} - set(values)
    if missing:
        raise JournalMaterialError("source environment lacks required staging bindings")
    present = JOURNAL_KEYS & set(values)
    if present:
        raise JournalMaterialError("source environment already contains journal material")
    if any("change_me" in value.lower() for value in values.values()):
        raise JournalMaterialError("source environment contains unresolved placeholders")

    pairwise_secret = _token(token_factory, 48)
    encryption_secret = _token(token_factory, 48)
    database_password = _token(token_factory, 48)
    pairwise = json.dumps(
        [
            {
                "key_id": "fi-journal-to-bot",
                "source_site": "webapp_fi",
                "destination_site": "bot_fi",
                "secret": pairwise_secret,
            }
        ],
        separators=(",", ":"),
    )
    values.update(
        {
            "STAGING_RELEASE_SHA": release,
            "STAGING_SOURCE_ROOT": source_root,
            "BOT_FI_JOURNAL_DB_PASSWORD": database_password,
            "BOT_FI_SAME_REGION_JOURNAL_KEYS_JSON": pairwise,
            "WEBAPP_FI_SAME_REGION_JOURNAL_KEYS_JSON": pairwise,
            "WEBAPP_FI_SAME_REGION_JOURNAL_ENCRYPTION_KEY_ID": JOURNAL_KEY_ID,
            "WEBAPP_FI_SAME_REGION_JOURNAL_ENCRYPTION_SECRET": encryption_secret,
            "STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED": "false",
            "STAGING_WEBAPP_FI_MAX_PREPARED_TRANSACTIONS": "32",
        }
    )
    rendered = canonical_role_env_bytes(values, required_names=frozenset(values))
    metadata = {
        "release_sha": release,
        "journal_key_id": JOURNAL_KEY_ID,
        "two_phase_enabled": "false",
        "max_prepared_transactions": "32",
        "pairwise_secret_sha256": hashlib.sha256(pairwise_secret.encode()).hexdigest(),
        "encryption_secret_sha256": hashlib.sha256(encryption_secret.encode()).hexdigest(),
        "database_password_sha256": hashlib.sha256(database_password.encode()).hexdigest(),
        "environment_sha256": hashlib.sha256(rendered).hexdigest(),
    }
    return rendered, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--staging-source-root", required=True)
    parser.add_argument("--output-env", type=Path, required=True)
    parser.add_argument("--output-metadata", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output_env.resolve() == args.output_metadata.resolve():
            raise JournalMaterialError("environment and metadata outputs must differ")
        rendered, metadata = build_environment(
            read_secure_text(args.source_env, label="Stage 4R source environment"),
            release_sha=args.release_sha,
            staging_source_root=args.staging_source_root,
        )
        write_secure_new_bytes(
            args.output_env,
            rendered,
            label="Stage 4R journal environment",
            mode=0o600,
        )
        write_secure_new_bytes(
            args.output_metadata,
            (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode(),
            label="Stage 4R journal environment metadata",
            mode=0o600,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "journal-material-created",
                "release_sha": metadata["release_sha"],
                "environment_sha256": metadata["environment_sha256"],
                "two_phase_enabled": False,
                "secrets_printed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
