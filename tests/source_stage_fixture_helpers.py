"""Shared root-only fixtures for the WebApp-FI source-stage unit tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-config-v1"
BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"


def canonical_json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def make_initial_static_inputs(
    *,
    root: Path,
    campaign_id: str,
    application_repository: Path,
    application_release_sha: str,
    expected_alembic_revision: str,
    control_commit: str,
    control_tree: str,
    controller_recipient: str = "age1pppppppppppppppppppppppppppppppppppppppp",
    webapp_fi_recipient: str = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    webapp_ir_recipient: str = "age1rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
) -> tuple[Path, Path, str]:
    """Create only public controller inputs for a package fixture.

    The production helper revalidates both files; this fixture merely creates
    their canonical root-only forms without any S3 client or network action.
    """

    application_tree = subprocess.check_output(
        ["git", "-C", str(application_repository), "rev-parse", application_release_sha + "^{tree}"],
        text=True,
    ).strip()
    campaign_directory = root / "controller-campaigns" / campaign_id
    source_phase = campaign_directory / "webapp-fi-source"
    source_phase.mkdir(mode=0o700, parents=True)
    os.chmod(campaign_directory.parent, 0o700)
    os.chmod(campaign_directory, 0o700)
    os.chmod(source_phase, 0o700)
    unsigned_binding: dict[str, object] = {
        "schema": BINDING_SCHEMA,
        "status": "bound",
        "campaign_id": campaign_id,
        "application": {
            "release_sha": application_release_sha,
            "release_tree": application_tree,
            "expected_alembic_revision": expected_alembic_revision,
        },
        "tooling": {"control_commit": control_commit, "control_tree": control_tree},
    }
    binding = {
        **unsigned_binding,
        "binding_sha256": hashlib.sha256(canonical_json_bytes(unsigned_binding)).hexdigest(),
    }
    binding_path = _write_private(source_phase / "campaign-binding.json", canonical_json_bytes(binding) + b"\n")

    controller_root = root / "controller-inputs"
    controller_root.mkdir(mode=0o700)
    credentials_path = _write_private(controller_root / "credentials.json", b'{"access_key":"fixture","secret_key":"fixture"}\n')
    config: dict[str, object] = {
        "schema": TRANSPORT_SCHEMA,
        "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
        "region": "ir-thr-at1",
        "bucket": "three-site-private",
        "prefix": "campaign-current/artifacts",
        "credentials_file": str(credentials_path),
        "age_binary": "/usr/bin/age",
        "workspace": str(controller_root / "workspace"),
        "controller_age_recipient": controller_recipient,
        "webapp_fi_age_recipient": webapp_fi_recipient,
        "webapp_ir_age_recipient": webapp_ir_recipient,
        "maximum_plaintext_bytes": 24 * 1024 * 1024,
        "presign_expires_seconds": 300,
    }
    config_path = _write_private(controller_root / "source-transport.json", canonical_json_bytes(config) + b"\n")
    return config_path, binding_path, "initial-static-20260730"


def campaign_bound_controller_signer(*, campaign_binding_path: Path, private_key_raw: bytes):
    """Return a test double matching the fixed campaign-key loader contract.

    Production callers have no key-path override.  Focused callers patch only
    their loader seam with this in-memory authority; the dedicated signing-key
    tests exercise the real fixed-path loader independently.
    """

    value = json.loads(campaign_binding_path.read_text(encoding="ascii"))
    application = value["application"]
    tooling = value["tooling"]
    signer = Ed25519PrivateKey.from_private_bytes(private_key_raw)
    public = signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_base64 = base64.b64encode(public).decode("ascii")
    key_id = "ed25519-sha256:" + hashlib.sha256(public).hexdigest()
    receipt_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "campaign_id": value["campaign_id"],
                "campaign_binding_sha256": value["binding_sha256"],
                "public_key_base64": public_base64,
            }
        )
    ).hexdigest()
    return SimpleNamespace(
        signer=signer,
        signing_key=SimpleNamespace(
            public_key_base64=public_base64,
            key_id=key_id,
            receipt_sha256=receipt_sha256,
        ),
        campaign_binding=SimpleNamespace(
            campaign_id=value["campaign_id"],
            application_release_sha=application["release_sha"],
            application_release_tree=application["release_tree"],
            expected_alembic_revision=application["expected_alembic_revision"],
            control_commit=tooling["control_commit"],
            control_tree=tooling["control_tree"],
            binding_sha256=value["binding_sha256"],
        ),
    )
