#!/usr/bin/env python3
"""Build one validated, unsealed Emergency IR Docker image archive locally.

This tool is deliberately outside the Object Storage publisher, sealed
manifest, receiver, and standalone activator workflows.  It invokes no Object
Storage, SSH, DNS, remote-host, or service-management client; it does not
encrypt, decrypt, sign, load, or start a container.  Its only mutable runtime
operations are local Docker ``build``, ``tag``, and ``save`` commands plus
create-only creation of the requested archive.

The archive contains exactly the temporary standalone application's immutable
Emergency tag and emergency-namespaced PostgreSQL/Redis tags.  It is checked
by the existing standalone activator *before* the archive is made available
for a separately reviewed sealing step.  A failure leaves Docker evidence in
place and never overwrites or deletes an existing output/tag.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import emergency_ir_standalone_activate as activator  # noqa: E402
from scripts import verify_emergency_ir_image_provenance as image_provenance  # noqa: E402


GIT_BINARY = "/usr/bin/git"
DOCKER_BINARY = "/usr/bin/docker"
SOURCE_RELEASE_SHA = activator.SOURCE_RELEASE_SHA
APP_IMAGE_REPOSITORY = "trading_bot_emergency_ir_app"
POSTGRES_SOURCE_IMAGE = "postgres:15-alpine"
REDIS_SOURCE_IMAGE = "redis:7-alpine"
POSTGRES_EMERGENCY_IMAGE = "trading_bot_emergency_ir_postgres:15-alpine"
REDIS_EMERGENCY_IMAGE = "trading_bot_emergency_ir_redis:7-alpine"
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$", re.ASCII)
SHA_RE = re.compile(r"^[a-f0-9]{40}$", re.ASCII)


class EmergencyImageBundleError(RuntimeError):
    """The local source, Docker evidence, or output archive is unsafe."""


@dataclasses.dataclass(frozen=True)
class EmergencyImageBundle:
    """Evidence for an archive that is validated but deliberately unsealed."""

    output: Path
    source_release_sha: str
    emergency_patch_sha: str
    sha256: str
    bytes: int
    images: tuple[activator.ImageEntry, ...]


def _fail(message: str) -> None:
    raise EmergencyImageBundleError(message)


def expected_app_image(emergency_patch_sha: str) -> str:
    if SHA_RE.fullmatch(emergency_patch_sha) is None:
        _fail("Emergency patch SHA is invalid")
    return f"{APP_IMAGE_REPOSITORY}:{emergency_patch_sha}"


def _tool_environment() -> dict[str, str]:
    """Do not inherit proxy, SSH, Object Storage, or Git configuration."""

    return {
        "HOME": "/root",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _text(value: object) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EmergencyImageBundleError("local command output is not UTF-8") from exc
    if isinstance(value, str):
        return value
    return ""


def _run_capture(
    command: Sequence[str],
    *,
    label: str,
    runner: Callable[..., Any],
    allow_nonzero: bool = False,
    timeout: int = 60,
) -> Any:
    try:
        result = runner(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_tool_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EmergencyImageBundleError(f"{label} could not run") from exc
    if getattr(result, "returncode", 1) == 0:
        return result
    if allow_nonzero:
        return result
    _fail(f"{label} failed")


def _git_text(repo: Path, *arguments: str, label: str, runner: Callable[..., Any]) -> str:
    result = _run_capture(
        (GIT_BINARY, "-C", str(repo), *arguments),
        label=label,
        runner=runner,
    )
    return _text(getattr(result, "stdout", ""))


def _resolve_clean_source(
    *, repo: Path, emergency_patch_sha: str, runner: Callable[..., Any]
) -> Path:
    if SHA_RE.fullmatch(emergency_patch_sha) is None:
        _fail("Emergency patch SHA is invalid")
    try:
        root = repo.resolve(strict=True)
        metadata = root.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency source repository cannot be inspected") from exc
    if repo.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        _fail("Emergency source repository must be one real directory")
    top_level = _git_text(
        root,
        "rev-parse",
        "--show-toplevel",
        label="Emergency source Git top-level check",
        runner=runner,
    ).strip()
    try:
        top_level_path = Path(top_level).resolve(strict=True)
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency source Git top-level is invalid") from exc
    if top_level_path != root:
        _fail("Emergency source repository is not its Git top-level")
    head = _git_text(
        root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        label="Emergency source Git HEAD check",
        runner=runner,
    ).strip()
    if SHA_RE.fullmatch(head) is None or head != emergency_patch_sha:
        _fail("Emergency patch SHA does not equal the clean source Git HEAD")
    ancestry = _run_capture(
        (GIT_BINARY, "-C", str(root), "merge-base", "--is-ancestor", SOURCE_RELEASE_SHA, emergency_patch_sha),
        label="Emergency source Git base ancestry check",
        runner=runner,
        allow_nonzero=True,
    )
    if getattr(ancestry, "returncode", 1) != 0:
        _fail("Emergency patch SHA does not descend from the declared source release")
    dirty = _git_text(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        label="Emergency source Git clean check",
        runner=runner,
    )
    if dirty:
        _fail("Emergency source Git worktree is not clean")
    dockerfile = root / "Dockerfile"
    try:
        dockerfile_state = dockerfile.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency source Dockerfile is missing") from exc
    if stat.S_ISLNK(dockerfile_state.st_mode) or not stat.S_ISREG(dockerfile_state.st_mode):
        _fail("Emergency source Dockerfile is unsafe")
    return root


def _image_id(
    reference: str,
    *,
    label: str,
    runner: Callable[..., Any],
    allow_missing: bool = False,
) -> str | None:
    result = _run_capture(
        (DOCKER_BINARY, "image", "inspect", reference, "--format", "{{.Id}}"),
        label=label,
        runner=runner,
        allow_nonzero=allow_missing,
        timeout=30,
    )
    if getattr(result, "returncode", 1) != 0:
        stderr = _text(getattr(result, "stderr", ""))
        if allow_missing and getattr(result, "returncode", 1) == 1 and (
            "No such image:" in stderr or "No such object:" in stderr
        ):
            return None
        _fail(f"{label} failed")
    image_id = _text(getattr(result, "stdout", "")).strip()
    if IMAGE_ID_RE.fullmatch(image_id) is None:
        _fail(f"{label} returned an invalid image ID")
    return image_id


def _require_absent_tag(reference: str, *, runner: Callable[..., Any]) -> None:
    if _image_id(
        reference,
        label=f"Emergency target image tag {reference} preflight",
        runner=runner,
        allow_missing=True,
    ) is not None:
        _fail(f"refusing to overwrite existing Emergency target tag: {reference}")


def _inspect_app_provenance(
    *,
    app_image: str,
    emergency_patch_sha: str,
    runner: Callable[..., Any],
) -> str:
    result = _run_capture(
        (DOCKER_BINARY, "image", "inspect", app_image, "--format", "{{json .}}"),
        label="Emergency application image provenance inspection",
        runner=runner,
        timeout=30,
    )
    try:
        payload = json.loads(_text(getattr(result, "stdout", "")).strip())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmergencyImageBundleError("Emergency application image inspection is not JSON") from exc
    failures = image_provenance.verify_payload(
        payload=payload,
        source_release_sha=SOURCE_RELEASE_SHA,
        emergency_patch_sha=emergency_patch_sha,
    )
    if failures:
        _fail("Emergency application image provenance failed: " + "; ".join(failures))
    image_id = payload.get("Id") if isinstance(payload, Mapping) else None
    if not isinstance(image_id, str) or IMAGE_ID_RE.fullmatch(image_id) is None:
        _fail("Emergency application image provenance has an invalid image ID")
    return image_id


def _docker_success(
    command: Sequence[str],
    *,
    label: str,
    runner: Callable[..., Any],
    timeout: int,
) -> None:
    _run_capture(command, label=label, runner=runner, timeout=timeout)


def _secure_output_parent(output: Path) -> Path:
    if not output.is_absolute() or output.name in {"", ".", ".."} or output.parent == Path("/"):
        _fail("Emergency image archive output must be an absolute file path")
    parent = output.parent
    try:
        canonical_parent = parent.resolve(strict=True)
        metadata = parent.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency image archive output directory cannot be inspected") from exc
    if (
        canonical_parent != parent
        or ".." in parent.parts
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        _fail("Emergency image archive output directory is not owner-controlled")
    try:
        existing = output.lstat()
    except FileNotFoundError:
        return parent
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency image archive output cannot be inspected") from exc
    del existing
    _fail("refusing to overwrite an existing Emergency image archive")


def _hash_archive(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency image archive cannot be inspected") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or not 1 <= before.st_size <= activator.MAX_IMAGE_BYTES
        or stat.S_IMODE(before.st_mode) & 0o077
    ):
        _fail("Emergency image archive is not one private owner-controlled file")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(opened, field) for field in fields):
            _fail("Emergency image archive changed while being opened")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > activator.MAX_IMAGE_BYTES:
                _fail("Emergency image archive exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if size != opened.st_size or any(getattr(opened, field) != getattr(after, field) for field in fields):
            _fail("Emergency image archive changed while being read")
        return digest.hexdigest(), size
    except OSError as exc:
        raise EmergencyImageBundleError("Emergency image archive cannot be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _save_validated_archive(
    *,
    output: Path,
    tags: tuple[str, str, str],
    source_release_sha: str,
    emergency_patch_sha: str,
    expected_ids: Mapping[str, str],
    runner: Callable[..., Any],
) -> tuple[str, int, tuple[activator.ImageEntry, ...]]:
    parent = _secure_output_parent(output)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".emergency-ir-images-", suffix=".tar", dir=parent)
    temporary = Path(temporary_name)
    completed = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            try:
                result = runner(
                    [DOCKER_BINARY, "save", *tags],
                    check=False,
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    timeout=7200,
                    env=_tool_environment(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise EmergencyImageBundleError("Emergency Docker image archive save could not run") from exc
            stream.flush()
            os.fsync(stream.fileno())
        if getattr(result, "returncode", 1) != 0:
            _fail("Emergency Docker image archive save failed")
        entries = tuple(
            activator.inspect_image_bundle(
                image_tar=temporary,
                source_sha=source_release_sha,
                patch_sha=emergency_patch_sha,
                profile="telegram-only",
            )
        )
        actual_ids = {entry.tag: entry.config_id for entry in entries}
        if actual_ids != dict(expected_ids):
            _fail("Emergency Docker image archive IDs differ from the inspected local tags")
        digest, size = _hash_archive(temporary)
        try:
            os.link(temporary, output, follow_symlinks=False)
        except FileExistsError as exc:
            raise EmergencyImageBundleError("refusing to overwrite an existing Emergency image archive") from exc
        except OSError as exc:
            raise EmergencyImageBundleError("Emergency image archive cannot be finalized create-only") from exc
        temporary.unlink()
        completed = True
        return digest, size, entries
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not completed:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def build_emergency_ir_image_bundle(
    *,
    repo: Path,
    emergency_patch_sha: str,
    output: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> EmergencyImageBundle:
    """Build, tag, save, and validate exactly three local Emergency images.

    This function intentionally has no seal, encryption, publisher, receiver,
    transfer, image-load, Compose, service, DNS, or remote-host operation.
    """

    if os.geteuid() != 0:
        _fail("Emergency image bundle build must run as root")
    # Refuse an output collision before the first Docker mutation.  The save
    # helper repeats this immediately before its create-only finalization.
    _secure_output_parent(output)
    source_root = _resolve_clean_source(
        repo=repo,
        emergency_patch_sha=emergency_patch_sha,
        runner=runner,
    )
    app_image = expected_app_image(emergency_patch_sha)
    tags = (app_image, POSTGRES_EMERGENCY_IMAGE, REDIS_EMERGENCY_IMAGE)
    for tag in tags:
        _require_absent_tag(tag, runner=runner)

    postgres_id = _image_id(
        POSTGRES_SOURCE_IMAGE,
        label="Emergency PostgreSQL source image preflight",
        runner=runner,
    )
    redis_id = _image_id(
        REDIS_SOURCE_IMAGE,
        label="Emergency Redis source image preflight",
        runner=runner,
    )
    if postgres_id is None or redis_id is None:
        _fail("Emergency base image preflight returned no image identity")
    labels = (
        ("org.opencontainers.image.revision", emergency_patch_sha),
        ("org.goldtrade.emergency.base-revision", SOURCE_RELEASE_SHA),
        ("org.goldtrade.emergency.scope", image_provenance.EMERGENCY_SCOPE),
        ("org.goldtrade.emergency.auth", image_provenance.EMERGENCY_AUTH_SCOPE),
    )
    command: list[str] = [
        DOCKER_BINARY,
        "build",
        "--pull=false",
        "--file",
        str(source_root / "Dockerfile"),
        "--tag",
        app_image,
    ]
    for key, value in labels:
        command.extend(("--label", f"{key}={value}"))
    command.append(str(source_root))
    _docker_success(
        command,
        label="Emergency application image build",
        runner=runner,
        timeout=7200,
    )
    app_id = _inspect_app_provenance(
        app_image=app_image,
        emergency_patch_sha=emergency_patch_sha,
        runner=runner,
    )
    _docker_success(
        (DOCKER_BINARY, "image", "tag", POSTGRES_SOURCE_IMAGE, POSTGRES_EMERGENCY_IMAGE),
        label="Emergency PostgreSQL image tag",
        runner=runner,
        timeout=30,
    )
    _docker_success(
        (DOCKER_BINARY, "image", "tag", REDIS_SOURCE_IMAGE, REDIS_EMERGENCY_IMAGE),
        label="Emergency Redis image tag",
        runner=runner,
        timeout=30,
    )
    if _image_id(
        POSTGRES_EMERGENCY_IMAGE,
        label="Emergency PostgreSQL tagged image inspection",
        runner=runner,
    ) != postgres_id:
        _fail("Emergency PostgreSQL tag does not bind the expected source image")
    if _image_id(
        REDIS_EMERGENCY_IMAGE,
        label="Emergency Redis tagged image inspection",
        runner=runner,
    ) != redis_id:
        _fail("Emergency Redis tag does not bind the expected source image")
    expected_ids = {
        app_image: app_id,
        POSTGRES_EMERGENCY_IMAGE: postgres_id,
        REDIS_EMERGENCY_IMAGE: redis_id,
    }
    digest, size, entries = _save_validated_archive(
        output=output,
        tags=tags,
        source_release_sha=SOURCE_RELEASE_SHA,
        emergency_patch_sha=emergency_patch_sha,
        expected_ids=expected_ids,
        runner=runner,
    )
    return EmergencyImageBundle(
        output=output,
        source_release_sha=SOURCE_RELEASE_SHA,
        emergency_patch_sha=emergency_patch_sha,
        sha256=digest,
        bytes=size,
        images=entries,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--emergency-patch-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        built = build_emergency_ir_image_bundle(
            repo=arguments.repo,
            emergency_patch_sha=arguments.emergency_patch_sha,
            output=arguments.output,
        )
    except EmergencyImageBundleError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "built-local-only-ready-for-seal",
                "output": str(built.output),
                "source_release_sha": built.source_release_sha,
                "emergency_patch_sha": built.emergency_patch_sha,
                "sha256": built.sha256,
                "bytes": built.bytes,
                "images": [dataclasses.asdict(item) for item in built.images],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
