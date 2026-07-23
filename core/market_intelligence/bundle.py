"""Fail-closed verification and loading of a Shadow runtime bundle."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from core.market_intelligence.contracts import SHADOW_BUNDLE_STATUS
from core.market_intelligence.ranker import RankerPolicy


SUPPORTED_CODE_VERSION = (
    "COIN_INTELLIGENCE_SHADOW_V4_COIN_ANCHOR_TRANSFER"
)
DEFAULT_BUNDLE_DIRECTORY = (
    Path(__file__).resolve().parent
    / "bundles"
    / "coin-inference-shadow-20260723-v8-anchor-transfer"
)
MAXIMUM_BUNDLE_BYTES = 2 * 1024 * 1024
REQUIRED_RUNTIME_FILES = frozenset(
    {
        "coin_range_model.json",
        "commodity_ranker_policy.json",
        "feature_schema.json",
        "interval_policy.json",
        "pipeline_policy.json",
        "commodity_catalog.json",
        "ranker_validation_summary.json",
        "coin_anchor_transfer_policy.json",
        "coin_anchor_transfer_validation_summary.json",
    }
)


class BundleValidationError(RuntimeError):
    """Raised when an artifact cannot be trusted by the runtime."""


@dataclass(frozen=True, slots=True)
class LoadedRuntimeBundle:
    root: Path
    manifest: Mapping[str, Any]
    ranker_policy: RankerPolicy
    feature_schema: Mapping[str, Any]
    commodity_catalog: Mapping[str, Any]

    @property
    def bundle_version(self) -> str:
        return str(self.manifest["bundle_version"])

    @property
    def model_sha256(self) -> str:
        return str(self.manifest["source_model"]["sha256"])

    @property
    def feature_schema_version(self) -> str:
        return str(self.manifest["feature_schema_version"])


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"bundle_json_invalid:{path.name}") from exc
    if not isinstance(value, dict):
        raise BundleValidationError(f"bundle_json_not_object:{path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BundleValidationError(f"bundle_file_unreadable:{path.name}") from exc
    return digest.hexdigest()


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    return isinstance(value, str) and value.startswith("/")


def _validated_member_name(raw_name: object) -> str:
    name = str(raw_name)
    candidate = Path(name)
    if (
        not name
        or candidate.name != name
        or candidate.is_absolute()
        or name in {".", ".."}
    ):
        raise BundleValidationError("bundle_member_name_invalid")
    return name


def load_runtime_bundle(
    path: str | Path | None = None,
) -> LoadedRuntimeBundle:
    root = Path(path) if path is not None else DEFAULT_BUNDLE_DIRECTORY
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BundleValidationError("bundle_directory_unavailable") from exc
    if not resolved.is_dir():
        raise BundleValidationError("bundle_path_is_not_directory")

    manifest_path = resolved / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BundleValidationError("bundle_manifest_unavailable")
    manifest = _read_json_object(manifest_path)
    if int(manifest.get("schema_version") or 0) != 1:
        raise BundleValidationError("bundle_manifest_schema_unsupported")
    if manifest.get("status") != SHADOW_BUNDLE_STATUS:
        raise BundleValidationError("bundle_is_not_shadow")
    if manifest.get("minimum_compatible_code_version") != SUPPORTED_CODE_VERSION:
        raise BundleValidationError("bundle_code_version_mismatch")
    if manifest.get("training_payload_embedded") is not False:
        raise BundleValidationError("bundle_training_payload_not_prohibited")
    if manifest.get("absolute_machine_paths_embedded") is not False:
        raise BundleValidationError("bundle_absolute_paths_not_prohibited")
    source_model = manifest.get("source_model")
    if not isinstance(source_model, dict) or source_model.get("is_llm") is not False:
        raise BundleValidationError("bundle_source_model_invalid")

    files = manifest.get("files")
    if not isinstance(files, dict):
        raise BundleValidationError("bundle_files_manifest_invalid")
    member_names = {_validated_member_name(name) for name in files}
    if member_names != REQUIRED_RUNTIME_FILES:
        raise BundleValidationError("bundle_file_set_mismatch")
    try:
        actual_members = {
            child.name for child in resolved.iterdir()
        }
    except OSError as exc:
        raise BundleValidationError("bundle_directory_unreadable") from exc
    if actual_members != member_names | {"manifest.json"}:
        raise BundleValidationError("bundle_unexpected_member")

    total_size = 0
    payloads: dict[str, dict[str, Any]] = {}
    for name in sorted(member_names):
        metadata = files.get(name)
        if not isinstance(metadata, dict):
            raise BundleValidationError(f"bundle_metadata_invalid:{name}")
        member = resolved / name
        if not member.is_file() or member.is_symlink():
            raise BundleValidationError(f"bundle_member_unavailable:{name}")
        try:
            expected_size = int(metadata.get("size_bytes") or -1)
            actual_size = member.stat().st_size
        except (OSError, TypeError, ValueError) as exc:
            raise BundleValidationError(
                f"bundle_metadata_invalid:{name}"
            ) from exc
        if expected_size != actual_size:
            raise BundleValidationError(f"bundle_size_mismatch:{name}")
        total_size += actual_size
        if total_size > MAXIMUM_BUNDLE_BYTES:
            raise BundleValidationError("bundle_total_size_exceeded")
        if _sha256(member) != str(metadata.get("sha256") or ""):
            raise BundleValidationError(f"bundle_checksum_mismatch:{name}")
        payload = _read_json_object(member)
        if _contains_absolute_path(payload):
            raise BundleValidationError(f"bundle_absolute_path_detected:{name}")
        payloads[name] = payload

    runtime_model = payloads["coin_range_model.json"]
    if "training_examples" in runtime_model:
        raise BundleValidationError("bundle_contains_training_examples")
    if (
        int(runtime_model.get("schema_version") or 0) < 5
        or runtime_model.get("is_llm") is not False
    ):
        raise BundleValidationError("bundle_runtime_model_invalid")
    feature_schema = payloads["feature_schema.json"]
    if (
        str(feature_schema.get("feature_schema_version"))
        != str(manifest.get("feature_schema_version"))
    ):
        raise BundleValidationError("bundle_feature_schema_mismatch")
    if feature_schema.get("status") != "SHADOW_NOT_PRODUCTION":
        raise BundleValidationError("bundle_feature_schema_not_shadow")
    ranker_payload = payloads["commodity_ranker_policy.json"]
    if ranker_payload.get("status") != "SHADOW_RESEARCH_NOT_PROMOTED":
        raise BundleValidationError("bundle_ranker_policy_not_shadow")
    promotion_policy = ranker_payload.get("promotion_policy")
    if (
        not isinstance(promotion_policy, dict)
        or promotion_policy.get("status") != "NOT_EVALUATED_NOT_PROMOTED"
    ):
        raise BundleValidationError("bundle_ranker_promotion_policy_invalid")
    if (
        payloads["ranker_validation_summary.json"].get("promotion_status")
        != "SHADOW_TEST_PASSED_NOT_PROMOTED"
    ):
        raise BundleValidationError("bundle_ranker_evidence_not_shadow")
    if (
        payloads[
            "coin_anchor_transfer_validation_summary.json"
        ].get("promotion_status")
        != "SHADOW_NOT_PROMOTED"
    ):
        raise BundleValidationError("bundle_anchor_evidence_not_shadow")
    try:
        policy = RankerPolicy.from_mapping(ranker_payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleValidationError("bundle_ranker_policy_invalid") from exc
    if policy.policy_version != manifest.get("ranker_policy_version"):
        raise BundleValidationError("bundle_ranker_policy_mismatch")
    return LoadedRuntimeBundle(
        root=resolved,
        manifest=manifest,
        ranker_policy=policy,
        feature_schema=feature_schema,
        commodity_catalog=payloads["commodity_catalog.json"],
    )
