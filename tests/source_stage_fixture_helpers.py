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


TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-config-v2"
BINDING_SCHEMA = "gold-trade-webapp-fi-source-campaign-binding-v1"


def canonical_json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def source_transport_fixture_roots(root: Path) -> tuple[Path, Path]:
    """Return the patched controller config and derived-workspace roots.

    Tests patch each dynamically loaded transport module to these paths; the
    production loader still accepts only its fixed `/etc` campaign layout.
    """

    return root / "controller-campaigns", root / "source-transport-workspaces"


def trusted_e53_s3_environment_path(root: Path) -> Path:
    """Return the test-only fixed path used for the trusted legacy input."""

    return root / "trusted-e53" / "wa-ir-object-storage-transport.env"


def make_trusted_e53_s3_environment(root: Path) -> Path:
    """Create an exact, root-only synthetic legacy e53 transport input.

    These are inert fixture values.  The source transport loader exercises its
    fixed-path and exact-field validation without reading any real credential.
    """

    return _write_private(
        trusted_e53_s3_environment_path(root),
        (
            b"ARVAN_S3_ACCESS_KEY=fixture-access-key\n"
            b"ARVAN_S3_SECRET_KEY=fixture-secret-key\n"
            b"ARVAN_S3_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir\n"
            b"ARVAN_S3_REGION=ir-thr-at1\n"
            b"WA_IR_OBJECT_STORAGE_BUCKET=three-site-private\n"
            b"WA_IR_OBJECT_STORAGE_PREFIX=campaign-current/artifacts\n"
            b"WA_IR_AGE_RECIPIENT_FILE=/root/fixture/wa-ir-recipient.txt\n"
            b"WA_IR_REMOTE_AGE_IDENTITY=/root/fixture/wa-ir.agekey\n"
        ),
    )


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
    campaigns_root, _workspace_root = source_transport_fixture_roots(root)
    campaign_directory = campaigns_root / campaign_id
    source_phase = campaign_directory / "webapp-fi-source"
    controller_directory = campaign_directory / "controller"
    source_phase.mkdir(mode=0o700, parents=True)
    controller_directory.mkdir(mode=0o700)
    os.chmod(campaign_directory.parent, 0o700)
    os.chmod(campaign_directory, 0o700)
    os.chmod(source_phase, 0o700)
    os.chmod(controller_directory, 0o700)
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

    credentials_path = make_trusted_e53_s3_environment(root)
    config: dict[str, object] = {
        "schema": TRANSPORT_SCHEMA,
        "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
        "bucket": "three-site-private",
        "prefix": "campaign-current/artifacts",
        "credentials_file": str(credentials_path),
        "controller_age_recipient": controller_recipient,
        "webapp_fi_age_recipient": webapp_fi_recipient,
        "webapp_ir_age_recipient": webapp_ir_recipient,
        "presign_expires_seconds": 300,
    }
    config_path = _write_private(controller_directory / "source-transport.json", canonical_json_bytes(config) + b"\n")
    return config_path, binding_path, "initial-static-20260730"


def make_expected_static_assets_manifest(
    *,
    root: Path,
    campaign_id: str,
    application_repository: Path,
    application_release_sha: str,
    expected_alembic_revision: str,
    control_commit: str,
    control_tree: str,
) -> Path:
    """Create a test-only exact-build manifest input for source-package tests.

    Production code receives this shape only from the controller's
    exact-release adapter.  These focused fixtures construct the same
    canonical public payload without calling a build tool or any transport.
    """

    static_root = application_repository / "mini_app_dist"
    files: list[dict[str, object]] = []
    for path in sorted(static_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(static_root).as_posix()
        payload = path.read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    if not files:
        raise ValueError("fixture static output must contain files")
    release_tree = subprocess.check_output(
        ["git", "-C", str(application_repository), "rev-parse", application_release_sha + "^{tree}"],
        text=True,
    ).strip()
    value: dict[str, object] = {
        "schema": "gold-trade-webapp-fi-expected-static-assets-v2",
        "status": "prepared",
        "campaign_id": campaign_id,
        "application": {
            "release_sha": application_release_sha,
            "release_tree": release_tree,
            "expected_alembic_revision": expected_alembic_revision,
        },
        "tooling": {"control_commit": control_commit, "control_tree": control_tree},
        "static_root": "mini_app_dist",
        "files": files,
        "files_sha256": hashlib.sha256(canonical_json_bytes(files)).hexdigest(),
    }
    return _write_private(
        root / "exact-build-manifests" / campaign_id / "expected-static-assets.json",
        canonical_json_bytes(value) + b"\n",
    )


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
