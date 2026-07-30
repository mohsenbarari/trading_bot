#!/usr/bin/env python3
"""Compose controller-only, one-stage-at-a-time WebApp-FI source plans.

This is deliberately a coordinator *skeleton*, not an executor.  Each
subcommand validates one bounded stage and emits a URL-free plan.  There is no
``run-all`` operation, no persistent coordinator state, and no SSH, Object
Storage, Docker, service, or data-plane execution path.  A later, separately
authorised operator action must consume each rendered control command or
ephemeral Object Storage capability.

The initial wired stages are:

* ``bootstrap-plan``: prove the bootstrap package, immutable publish receipt,
  delivery envelope, source role config, and FI host pin agree.  It never
  accepts or prints a presigned URL.
* ``static-plan``: compose the existing fixed-runtime static-preparation
  renderer and return its URL-free pinned-SSH control command.
* ``packet-plan``: compose the static-provenance receive renderer through its
  pre-capability validation API.  It reports only immutable packet facts; a
  later, separate action supplies the ephemeral GET capability.
* ``image-plan`` and ``evidence-plan``: compose the strict post-packet FI
  upload renderer for their respective fixed artifact kinds.  They render
  only the URL-free FI preparation command.  A later, separate action must
  verify the prepared receipt before it obtains any ephemeral PUT capability.

There is deliberately no generic artifact-kind subcommand: the image and
evidence routes are distinct fixed operations.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence


SOURCE_STAGE_PLAN_SCHEMA = "gold-trade-webapp-fi-source-stage-plan-v1"
STAGES = frozenset({"bootstrap", "static", "packet", "image", "evidence"})
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FORBIDDEN_PLAN_KEY_FRAGMENTS = (
    "credential",
    "access_key",
    "secret",
    "private_key",
    "password",
    "session_token",
    "presigned",
    "url",
)
FORBIDDEN_PLAN_VALUE_MARKERS = (
    "://",
    "x-amz-signature",
    "x-amz-credential",
    "age-secret-key-",
)


class SourceStagePlanError(RuntimeError):
    """One controller-only source-stage plan is unsafe or incomplete."""


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - repository layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - repository layout invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(opened.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o022
        or opened.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise RuntimeError("required sibling filename is unsafe")
    source = _require_root_controlled_code_file(Path(__file__), field="source-stage coordinator source")
    path = _require_root_controlled_code_file(source.with_name(filename), field=f"required sibling {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded = getattr(module, "__file__", None)
        if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


bootstrap = _load_exact_sibling(
    "render_webapp_fi_source_bootstrap_receive.py", "_source_stage_plan_bootstrap"
)
static = _load_exact_sibling("render_webapp_fi_static_prepare.py", "_source_stage_plan_static")
packet = _load_exact_sibling(
    "render_webapp_fi_static_provenance_receive.py", "_source_stage_plan_packet"
)
post_packet = _load_exact_sibling(
    "render_webapp_fi_post_packet_upload.py", "_source_stage_plan_post_packet"
)
role_config = _load_exact_sibling(
    "render_webapp_fi_source_role_config.py", "_source_stage_plan_role_config"
)


@dataclasses.dataclass(frozen=True)
class PlanContext:
    campaign_binding_path: Path
    source_role_config_path: Path
    fi_known_hosts: Path
    campaign_id: str
    binding_sha256: str
    application: Mapping[str, str]
    tooling: Mapping[str, str]
    source_role_config_sha256: str


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceStagePlanError("controller source-stage planning must run as root")


def _require_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise SourceStagePlanError(f"{field} is invalid")
    return value


def _binding_matches(left: Any, right: Any) -> bool:
    return all(
        getattr(left, field, None) == getattr(right, field, None)
        for field in (
            "campaign_id",
            "application_release_sha",
            "application_release_tree",
            "expected_alembic_revision",
            "control_commit",
            "control_tree",
            "binding_sha256",
        )
    )


def _binding_matches_context(value: Any, context: PlanContext) -> bool:
    """Reject a renderer control that was built from a later binding read."""

    return (
        getattr(value, "campaign_id", None) == context.campaign_id
        and getattr(value, "application_release_sha", None) == context.application["release_sha"]
        and getattr(value, "application_release_tree", None) == context.application["release_tree"]
        and getattr(value, "expected_alembic_revision", None)
        == context.application["expected_alembic_revision"]
        and getattr(value, "control_commit", None) == context.tooling["control_commit"]
        and getattr(value, "control_tree", None) == context.tooling["control_tree"]
        and getattr(value, "binding_sha256", None) == context.binding_sha256
    )


def _load_context(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
) -> PlanContext:
    """Load only the canonical binding/config and the exact FI host pin."""

    _require_root_execution()
    binding_path = Path(campaign_binding)
    config_path = Path(source_role_config)
    if config_path != binding_path.with_name("source-role-config.json"):
        raise SourceStagePlanError("source role config is not at the fixed campaign-bound path")
    try:
        binding = role_config.binding.load_campaign_binding(binding_path)
        normalized_config = role_config.load_source_role_config(path=config_path, campaign_binding=binding)
        config_payload = role_config.canonical_json_bytes(normalized_config) + b"\n"
        actual_payload = role_config._read_private_file(config_path, field="WebApp-FI source role config")
        pinned_hosts = static.initial._require_pinned_known_hosts(Path(fi_known_hosts))
    except Exception as exc:
        raise SourceStagePlanError("canonical source-stage binding, role config, or FI host pin is invalid") from exc
    if config_payload != actual_payload:
        raise SourceStagePlanError("source role config changed while being validated")
    return PlanContext(
        campaign_binding_path=binding_path,
        source_role_config_path=config_path,
        fi_known_hosts=pinned_hosts,
        campaign_id=binding.campaign_id,
        binding_sha256=binding.binding_sha256,
        application={
            "release_sha": binding.application_release_sha,
            "release_tree": binding.application_release_tree,
            "expected_alembic_revision": binding.expected_alembic_revision,
        },
        tooling={"control_commit": binding.control_commit, "control_tree": binding.control_tree},
        source_role_config_sha256=hashlib.sha256(config_payload).hexdigest(),
    )


def _base_plan(*, context: PlanContext, stage: str, identifiers: Mapping[str, str]) -> dict[str, Any]:
    if stage not in STAGES:
        raise SourceStagePlanError("source-stage plan stage is unsupported")
    return {
        "schema": SOURCE_STAGE_PLAN_SCHEMA,
        "status": "planned",
        "stage": stage,
        "campaign": {
            "campaign_id": context.campaign_id,
            "campaign_binding_sha256": context.binding_sha256,
            "application": dict(context.application),
            "tooling": dict(context.tooling),
            "source_role_config_sha256": context.source_role_config_sha256,
        },
        "identifiers": dict(identifiers),
        "fi_host": {"host": static.initial.REMOTE_HOSTNAME, "pinned": True},
        "object_storage_changed": False,
        "ssh_changed": False,
        "docker_changed": False,
        "service_changed": False,
        "current_changed": False,
        "container_changed": False,
        "volume_changed": False,
        "application_data_changed": False,
    }


def _assert_url_free_nonsecret_plan(value: object) -> None:
    """Keep generated plans safe to print or retain as controller evidence."""

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or any(fragment in key.lower() for fragment in FORBIDDEN_PLAN_KEY_FRAGMENTS):
                    raise SourceStagePlanError("source-stage plan contains a forbidden control field")
                visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
            return
        if isinstance(item, str):
            lowered = item.lower()
            if any(marker in lowered for marker in FORBIDDEN_PLAN_VALUE_MARKERS):
                raise SourceStagePlanError("source-stage plan persists a URL or secret marker")
        elif isinstance(item, (bytes, bytearray, memoryview)):
            raise SourceStagePlanError("source-stage plan contains binary payload material")

    visit(value)


def _finish_plan(value: dict[str, Any]) -> dict[str, Any]:
    _assert_url_free_nonsecret_plan(value)
    return value


def plan_bootstrap(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    transport_publish_receipt: Path,
    delivery_envelope: Path,
    pinned_controller_public_key_base64: str,
) -> dict[str, Any]:
    """Validate the bootstrap route without accepting an ephemeral GET capability."""

    context = _load_context(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
    )
    try:
        policy = bootstrap._load_transport_policy(Path(source_transport_config))
        prepared, _preparation_value, _preparation_raw = bootstrap._verify_prepared_package(
            package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
        )
        publish_raw = bootstrap._read_root_only_file(
            Path(transport_publish_receipt), field="generic source transport publish receipt"
        )
        published = bootstrap._verify_generic_transport_receipt(
            payload=publish_raw, policy=policy, prepared=prepared
        )
        envelope_raw = bootstrap._read_root_only_file(
            Path(delivery_envelope), field="controller-signed delivery envelope"
        )
        bootstrap._verify_delivery_envelope(
            payload=envelope_raw,
            pinned_controller_public_key_base64=pinned_controller_public_key_base64,
            prepared=prepared,
            published=published,
            policy=policy,
        )
    except Exception as exc:
        raise SourceStagePlanError("bootstrap package, publish receipt, or delivery envelope is invalid") from exc
    if (
        prepared.get("application", {}).get("release_sha") != context.application["release_sha"]
        or prepared.get("application", {}).get("expected_alembic_revision")
        != context.application["expected_alembic_revision"]
        or prepared.get("tooling") != context.tooling
        or published.get("campaign_id") != context.campaign_id
    ):
        raise SourceStagePlanError("bootstrap controls do not match the canonical campaign binding")
    object_value = published["object"]
    plan = _base_plan(
        context=context,
        stage="bootstrap",
        identifiers={"package_id": prepared["package_id"], "bootstrap_object_id": published["object_id"]},
    )
    plan["bootstrap"] = {
        "object_key": object_value["object_key"],
        "version_id": object_value["version_id"],
        "ciphertext_sha256": object_value["ciphertext_sha256"],
        "ciphertext_bytes": object_value["ciphertext_bytes"],
        "plaintext_sha256": object_value["plaintext_sha256"],
        "plaintext_bytes": object_value["plaintext_bytes"],
        "controller_key_sha256": hashlib.sha256(
            pinned_controller_public_key_base64.encode("ascii")
        ).hexdigest(),
        "next_control": "ephemeral-object-get-capability-required",
        "renderer_adapter": "render_webapp_fi_source_bootstrap_receive",
    }
    return _finish_plan(plan)


def plan_static(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    fi_install_receipt: Path,
    static_output_id: str,
) -> dict[str, Any]:
    """Compose the existing fixed-runtime static preparation renderer only."""

    context = _load_context(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
    )
    try:
        control = static.build_static_preparation_control(
            source_transport_config=Path(source_transport_config),
            campaign_binding=Path(campaign_binding),
            source_adoption_package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
            fi_install_receipt=Path(fi_install_receipt),
            source_role_config=Path(source_role_config),
            static_output_id=static_output_id,
        )
        if (
            not _binding_matches(
                control.initial_control.campaign_binding,
                role_config.binding.load_campaign_binding(Path(campaign_binding)),
            )
            or not _binding_matches_context(control.initial_control.campaign_binding, context)
        ):
            raise SourceStagePlanError("static renderer binding differs from canonical campaign binding")
        command = static.render_prepare_command(control=control, fi_known_hosts=context.fi_known_hosts)
    except SourceStagePlanError:
        raise
    except Exception as exc:
        raise SourceStagePlanError("static preparation renderer controls are invalid") from exc
    plan = _base_plan(
        context=context,
        stage="static",
        identifiers={"static_output_id": control.static_output_id, "package_id": control.initial_control.package_id},
    )
    plan["static"] = {
        "runtime_source_root": str(control.runtime_source_root),
        "output_directory": str(control.static_output_directory),
        "archive_name": static.static_preparer.STATIC_ARCHIVE_NAME,
        "rendered_control_command": command,
        "receipt_verifier": "render_webapp_fi_static_prepare.verify-receipt",
    }
    return _finish_plan(plan)


def _packet_object_for_plan(control: Any) -> dict[str, Any]:
    """Copy only the immutable descriptor already checked by the packet renderer."""

    receipt = getattr(control, "transport_receipt", None)
    if not isinstance(receipt, Mapping):
        raise SourceStagePlanError("packet renderer did not return a transport receipt")
    descriptor = receipt.get("object")
    fields = (
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
    )
    if not isinstance(descriptor, Mapping) or set(descriptor) != set(fields):
        raise SourceStagePlanError("packet renderer returned an unsupported immutable object descriptor")
    return {field: descriptor[field] for field in fields}


def plan_packet(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    fi_install_receipt: Path,
    packet_id: str,
    transport_publish_receipt: Path,
) -> dict[str, Any]:
    """Validate one immutable static-provenance packet without a GET capability."""

    context = _load_context(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
    )
    try:
        control = packet.build_static_provenance_receive_control(
            source_transport_config=Path(source_transport_config),
            campaign_binding=Path(campaign_binding),
            source_adoption_package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
            fi_install_receipt=Path(fi_install_receipt),
            packet_id=packet_id,
            transport_publish_receipt=Path(transport_publish_receipt),
        )
        canonical_binding = role_config.binding.load_campaign_binding(Path(campaign_binding))
        if (
            not _binding_matches(control.campaign_binding, canonical_binding)
            or not _binding_matches_context(control.campaign_binding, context)
        ):
            raise SourceStagePlanError("packet renderer binding differs from canonical campaign binding")
        normalized_packet_id = _require_identifier(control.packet_id, field="packet_id")
        packet_payload = control.packet_payload
        if not isinstance(packet_payload, bytes) or not packet_payload:
            raise SourceStagePlanError("packet renderer did not return a packet payload")
        packet_path = Path(control.packet_path)
        candidate_directory = Path(control.candidate_directory)
        received_directory = Path(control.received_directory)
        fi_install_receipt_sha256 = control.fi_install_receipt_sha256
        if (
            not packet_path.is_absolute()
            or not candidate_directory.is_absolute()
            or not received_directory.is_absolute()
            or not isinstance(fi_install_receipt_sha256, str)
        ):
            raise SourceStagePlanError("packet renderer returned unsafe fixed paths or receipt data")
        published_object = _packet_object_for_plan(control)
    except SourceStagePlanError:
        raise
    except Exception as exc:
        raise SourceStagePlanError("static-provenance packet renderer controls are invalid") from exc
    plan = _base_plan(
        context=context,
        stage="packet",
        identifiers={"packet_id": normalized_packet_id},
    )
    plan["packet"] = {
        "fixed_packet_path": str(packet_path),
        "packet_sha256": hashlib.sha256(packet_payload).hexdigest(),
        "packet_bytes": len(packet_payload),
        "candidate_directory": str(candidate_directory),
        "received_directory": str(received_directory),
        "fi_install_receipt_sha256": fi_install_receipt_sha256,
        "published_object": published_object,
        "next_control": "ephemeral-object-get-capability-required",
        "renderer_adapter": "render_webapp_fi_static_provenance_receive",
        "receipt_verifier": "render_webapp_fi_static_provenance_receive.validate-install",
    }
    return _finish_plan(plan)


def _require_derived_absolute_path(value: object, *, field: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise SourceStagePlanError(f"{field} is invalid") from exc
    if (
        not path.is_absolute()
        or "\x00" in str(path)
        or str(path) != os.path.normpath(str(path))
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise SourceStagePlanError(f"{field} is not a canonical absolute path")
    return path


def _post_packet_plan(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    fi_static_packet_install_receipt: Path,
    packet_id: str,
    artifact_kind: str,
    artifact_id: str,
    stage: str,
    identifier_name: str,
) -> dict[str, Any]:
    """Compose one fixed post-packet FI preparation plan without a PUT capability."""

    fixed_routes = {
        "image": (post_packet.RAW_APP_IMAGE, "image_export_id"),
        "evidence": (post_packet.SOURCE_EVIDENCE, "evidence_id"),
    }
    if fixed_routes.get(stage) != (artifact_kind, identifier_name):
        raise SourceStagePlanError("post-packet plan route is not one of the fixed artifact stages")
    context = _load_context(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
    )
    expected_packet_id = _require_identifier(packet_id, field="packet_id")
    expected_artifact_id = _require_identifier(artifact_id, field=identifier_name)
    try:
        control = post_packet.build_post_packet_upload_control(
            source_transport_config=Path(source_transport_config),
            campaign_binding=Path(campaign_binding),
            source_role_config=Path(source_role_config),
            fi_static_packet_install_receipt=Path(fi_static_packet_install_receipt),
            packet_id=expected_packet_id,
            artifact_kind=artifact_kind,
            artifact_id=expected_artifact_id,
        )
        canonical_binding = role_config.binding.load_campaign_binding(Path(campaign_binding))
        if (
            not _binding_matches(control.campaign_binding, canonical_binding)
            or not _binding_matches_context(control.campaign_binding, context)
        ):
            raise SourceStagePlanError("post-packet renderer binding differs from canonical campaign binding")
        if (
            control.packet_id != expected_packet_id
            or control.artifact_kind != artifact_kind
            or control.artifact_id != expected_artifact_id
        ):
            raise SourceStagePlanError("post-packet renderer returned a different fixed artifact route")
        request = control.request
        recipient = getattr(control.policy, "controller_age_recipient", None)
        recipients = tuple(getattr(request, "recipients", ()))
        expected_request = {
            "campaign_id": context.campaign_id,
            "release_sha": context.application["release_sha"],
            "control_commit": context.tooling["control_commit"],
            "control_tree": context.tooling["control_tree"],
            "source_site": "webapp_fi",
            "destination_site": "controller",
            "object_kind": artifact_kind,
            "object_id": expected_artifact_id,
            "mode": post_packet.initial.transport.SINGLE_MODE,
        }
        if (
            not isinstance(recipient, str)
            or not recipient
            or not recipient.isascii()
            or recipients != (recipient,)
            or any(getattr(request, field, None) != value for field, value in expected_request.items())
        ):
            raise SourceStagePlanError("post-packet renderer request is not the exact controller-recipient route")
        packet_sha256 = getattr(control, "control_packet_sha256", None)
        static_receipt_sha256 = getattr(control, "static_packet_receipt_sha256", None)
        role_sha256 = getattr(control, "source_role_config_sha256", None)
        for field, value in (
            ("post-packet control packet checksum", packet_sha256),
            ("post-packet static receipt checksum", static_receipt_sha256),
            ("post-packet role config checksum", role_sha256),
        ):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise SourceStagePlanError(f"{field} is invalid")
        fi_candidate_directory = _require_derived_absolute_path(
            getattr(control, "fi_candidate_directory", None), field="post-packet FI candidate directory"
        )
        fi_packet_directory = _require_derived_absolute_path(
            getattr(control, "fi_packet_directory", None), field="post-packet FI packet directory"
        )
        prepared_directory = _require_derived_absolute_path(
            getattr(control, "prepared_directory", None), field="post-packet prepared directory"
        )
        object_key = post_packet.initial.transport.source_object_key(control.policy, request)
        if not isinstance(object_key, str) or not object_key:
            raise SourceStagePlanError("post-packet renderer object key is invalid")
        command = post_packet.render_prepare_command(control=control, fi_known_hosts=context.fi_known_hosts)
    except SourceStagePlanError:
        raise
    except Exception as exc:
        raise SourceStagePlanError("post-packet FI upload renderer controls are invalid") from exc
    plan = _base_plan(
        context=context,
        stage=stage,
        identifiers={"packet_id": expected_packet_id, identifier_name: expected_artifact_id},
    )
    plan[stage] = {
        "artifact_kind": artifact_kind,
        "object_key": object_key,
        "recipient_mode": request.mode,
        "controller_recipient_sha256": hashlib.sha256(recipient.encode("ascii")).hexdigest(),
        "fi_candidate_directory": str(fi_candidate_directory),
        "fi_packet_directory": str(fi_packet_directory),
        "prepared_directory": str(prepared_directory),
        "control_packet_sha256": packet_sha256,
        "static_packet_receipt_sha256": static_receipt_sha256,
        "source_role_config_sha256": role_sha256,
        "rendered_prepare_command": command,
        "next_control": "fi-post-packet-prepared-receipt-required",
        "renderer_adapter": "render_webapp_fi_post_packet_upload",
        "prepared_receipt_verifier": "render_webapp_fi_post_packet_upload.validate-prepared",
        "upload_report_verifier": "render_webapp_fi_post_packet_upload.validate-upload",
    }
    return _finish_plan(plan)


def plan_image(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    fi_static_packet_install_receipt: Path,
    packet_id: str,
    image_export_id: str,
) -> dict[str, Any]:
    """Plan only the fixed raw-app-image post-packet preparation route."""

    return _post_packet_plan(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
        source_transport_config=source_transport_config,
        fi_static_packet_install_receipt=fi_static_packet_install_receipt,
        packet_id=packet_id,
        artifact_kind=post_packet.RAW_APP_IMAGE,
        artifact_id=image_export_id,
        stage="image",
        identifier_name="image_export_id",
    )


def plan_evidence(
    *,
    campaign_binding: Path,
    source_role_config: Path,
    fi_known_hosts: Path,
    source_transport_config: Path,
    fi_static_packet_install_receipt: Path,
    packet_id: str,
    evidence_id: str,
) -> dict[str, Any]:
    """Plan only the fixed source-evidence post-packet preparation route."""

    return _post_packet_plan(
        campaign_binding=campaign_binding,
        source_role_config=source_role_config,
        fi_known_hosts=fi_known_hosts,
        source_transport_config=source_transport_config,
        fi_static_packet_install_receipt=fi_static_packet_install_receipt,
        packet_id=packet_id,
        artifact_kind=post_packet.SOURCE_EVIDENCE,
        artifact_id=evidence_id,
        stage="evidence",
        identifier_name="evidence_id",
    )


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--campaign-binding", required=True, type=Path)
    parser.add_argument("--source-role-config", required=True, type=Path)
    parser.add_argument("--fi-known-hosts", required=True, type=Path)


def _source_package_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)


def _post_packet_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--fi-static-packet-install-receipt", required=True, type=Path)
    parser.add_argument("--packet-id", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    bootstrap_plan = actions.add_parser("bootstrap-plan", help="validate one bootstrap stage without a GET capability")
    _common_arguments(bootstrap_plan)
    _source_package_arguments(bootstrap_plan)
    bootstrap_plan.add_argument("--transport-publish-receipt", required=True, type=Path)
    bootstrap_plan.add_argument("--delivery-envelope", required=True, type=Path)
    bootstrap_plan.add_argument("--pinned-controller-public-key-base64", required=True)

    static_plan = actions.add_parser("static-plan", help="render one fixed FI static-preparation control command")
    _common_arguments(static_plan)
    _source_package_arguments(static_plan)
    static_plan.add_argument("--fi-install-receipt", required=True, type=Path)
    static_plan.add_argument("--static-output-id", required=True)

    packet_plan = actions.add_parser(
        "packet-plan", help="validate one fixed static-provenance packet without a GET capability"
    )
    _common_arguments(packet_plan)
    _source_package_arguments(packet_plan)
    packet_plan.add_argument("--fi-install-receipt", required=True, type=Path)
    packet_plan.add_argument("--packet-id", required=True)
    packet_plan.add_argument("--transport-publish-receipt", required=True, type=Path)
    image_plan = actions.add_parser(
        "image-plan", help="render one fixed raw-app-image FI preparation command without a PUT capability"
    )
    _common_arguments(image_plan)
    _post_packet_arguments(image_plan)
    image_plan.add_argument("--image-export-id", required=True)
    evidence_plan = actions.add_parser(
        "evidence-plan", help="render one fixed source-evidence FI preparation command without a PUT capability"
    )
    _common_arguments(evidence_plan)
    _post_packet_arguments(evidence_plan)
    evidence_plan.add_argument("--evidence-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "bootstrap-plan":
            result = plan_bootstrap(
                campaign_binding=args.campaign_binding,
                source_role_config=args.source_role_config,
                fi_known_hosts=args.fi_known_hosts,
                source_transport_config=args.source_transport_config,
                source_adoption_package_directory=args.source_adoption_package_directory,
                preparation_receipt=args.preparation_receipt,
                transport_publish_receipt=args.transport_publish_receipt,
                delivery_envelope=args.delivery_envelope,
                pinned_controller_public_key_base64=args.pinned_controller_public_key_base64,
            )
        elif args.action == "static-plan":
            result = plan_static(
                campaign_binding=args.campaign_binding,
                source_role_config=args.source_role_config,
                fi_known_hosts=args.fi_known_hosts,
                source_transport_config=args.source_transport_config,
                source_adoption_package_directory=args.source_adoption_package_directory,
                preparation_receipt=args.preparation_receipt,
                fi_install_receipt=args.fi_install_receipt,
                static_output_id=args.static_output_id,
            )
        elif args.action == "packet-plan":
            result = plan_packet(
                campaign_binding=args.campaign_binding,
                source_role_config=args.source_role_config,
                fi_known_hosts=args.fi_known_hosts,
                source_transport_config=args.source_transport_config,
                source_adoption_package_directory=args.source_adoption_package_directory,
                preparation_receipt=args.preparation_receipt,
                fi_install_receipt=args.fi_install_receipt,
                packet_id=args.packet_id,
                transport_publish_receipt=args.transport_publish_receipt,
            )
        elif args.action == "image-plan":
            result = plan_image(
                campaign_binding=args.campaign_binding,
                source_role_config=args.source_role_config,
                fi_known_hosts=args.fi_known_hosts,
                source_transport_config=args.source_transport_config,
                fi_static_packet_install_receipt=args.fi_static_packet_install_receipt,
                packet_id=args.packet_id,
                image_export_id=args.image_export_id,
            )
        elif args.action == "evidence-plan":
            result = plan_evidence(
                campaign_binding=args.campaign_binding,
                source_role_config=args.source_role_config,
                fi_known_hosts=args.fi_known_hosts,
                source_transport_config=args.source_transport_config,
                fi_static_packet_install_receipt=args.fi_static_packet_install_receipt,
                packet_id=args.packet_id,
                evidence_id=args.evidence_id,
            )
        else:  # pragma: no cover - argparse dispatch invariant.
            raise SourceStagePlanError("unsupported source-stage plan action")
    except SourceStagePlanError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
