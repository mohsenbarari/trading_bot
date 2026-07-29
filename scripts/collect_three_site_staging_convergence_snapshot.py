#!/usr/bin/env python3
"""Collect one redacted, read-only convergence snapshot inside a site observer.

The command is intended for the ``*_sync_observer`` Compose service.  It does
not open a public listener, create a probe, mutate a row, or emit business
values/file bytes.  It reads one repeatable-read transaction and hashes the
local WebApp content-addressed files before returning a small typed snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.machinery
import importlib.util
import json
import keyword
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Any, Mapping
from uuid import UUID


HELD_RELEASE_ROOT_FD_ENV = "PRODUCTION_SHADOW_HELD_RELEASE_ROOT_FD"
HELD_COLLECTOR_FD_ENV = "PRODUCTION_SHADOW_HELD_CONVERGENCE_COLLECTOR_FD"
REQUIRED_PROJECT_PACKAGES = frozenset({"core", "models"})
RUNTIME_COLLECTOR_RELATIVE = Path(
    "scripts/collect_three_site_staging_convergence_snapshot.py"
)
GIT = "/usr/bin/git"
MAX_GIT_OUTPUT_BYTES = 64 * 1024
MAX_GIT_TREE_BYTES = 4 * 1024 * 1024
MAX_PROJECT_SOURCE_BYTES = 4 * 1024 * 1024
MAX_PROJECT_SOURCE_MODULES = 5_000
_HELD_RELEASE_ROOT_FD: int | None = None
_HELD_RELEASE_IMPORT_ROOT: str | None = None
_HELD_COLLECTOR_FD: int | None = None
_RUNTIME_IMPORTS_READY = False
_PROJECT_SOURCE_FINDER: "_GitReleaseSourceFinder | None" = None

# ``-S`` deliberately disables the interpreter's automatic site processing.
# The collector adds only these fixed, root-controlled distribution roots after
# its held-release/Git validation.  It never processes ``.pth`` files or
# imports ``sitecustomize``/``usercustomize``.  A host that keeps dependencies
# elsewhere is intentionally unavailable until its image contract is updated.
TRUSTED_SYSTEM_PACKAGE_ROOTS = (
    Path(f"/usr/local/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages"),
    Path(f"/usr/local/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"),
    Path("/usr/lib/python3/dist-packages"),
)
GIT_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
}
GIT_STRICT_OPTIONS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.pager=cat",
    "-c",
    "protocol.file.allow=never",
)

# These names are populated only by ``_load_trusted_runtime_dependencies``.
# Keeping imports lazy is deliberate: an imported collector module must not
# turn ambient Python/site state into convergence evidence.
func: Any = None
select: Any = None
text: Any = None
settings: Any = None
AsyncSessionLocal: Any = None
_hash_file: Any = None
resolve_runtime_identity: Any = None
build_database_parity_snapshot: Any = None
DrBlobManifest: Any = None
DrConflictQuarantine: Any = None
DrDestinationCursor: Any = None
DrEvent: Any = None
DrProducerCursor: Any = None
DrStreamCheckpoint: Any = None


class ConvergenceSnapshotError(RuntimeError):
    pass


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


@dataclass(frozen=True)
class _GitProjectSource:
    """One importable project module bound to an exact Git blob object."""

    module_name: str
    relative_path: PurePosixPath
    blob_sha: str
    package: bool


class _GitBlobSourceLoader:
    """Load only the source bytes addressed by a verified Git tree blob."""

    def __init__(self, source: _GitProjectSource) -> None:
        self._source = source

    def create_module(self, _spec: object) -> None:
        return None

    def is_package(self, _fullname: str) -> bool:
        return self._source.package

    def get_filename(self, _fullname: str) -> str:
        if _HELD_RELEASE_ROOT_FD is None:
            raise ConvergenceSnapshotError("held release root descriptor is unavailable")
        # This spelling resolves through an already-held directory descriptor,
        # never a caller/current-working-directory path.  The source loader
        # still compiles only the Git blob after the no-follow byte comparison.
        return (
            f"/proc/self/fd/{_HELD_RELEASE_ROOT_FD}/"
            f"{self._source.relative_path.as_posix()}"
        )

    def exec_module(self, module: Any) -> None:
        source = _git_blob_bytes(self._source)
        if _held_project_source_bytes(self._source) != source:
            raise ConvergenceSnapshotError(
                "held project module differs from the exact Git blob"
            )
        try:
            code = compile(
                source,
                self.get_filename(self._source.module_name),
                "exec",
                dont_inherit=True,
            )
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ConvergenceSnapshotError("verified project module cannot be compiled") from exc
        module.__file__ = self.get_filename(self._source.module_name)
        module.__cached__ = None
        exec(code, module.__dict__)


class _GitReleaseSourceFinder:
    """Import only Git-tree project modules; never fall back to the worktree."""

    def __init__(self, sources: Mapping[str, _GitProjectSource]) -> None:
        self._sources = dict(sources)
        self._project_roots = frozenset(
            source.module_name.split(".", maxsplit=1)[0]
            for source in self._sources.values()
        )

    def find_spec(
        self,
        fullname: str,
        _path: object = None,
        _target: object = None,
    ) -> object | None:
        source = self._sources.get(fullname)
        if source is not None:
            return importlib.util.spec_from_loader(
                fullname,
                _GitBlobSourceLoader(source),
                is_package=source.package,
            )
        if fullname.split(".", maxsplit=1)[0] in self._project_roots:
            # A Git-tracked application namespace must never resolve via an
            # installed package, cwd, zip file, or a replacement worktree.
            raise ModuleNotFoundError("project import is absent from the verified Git tree")
        return None


CONTAINER_SOURCE_MANIFEST_SCHEMA = (
    "production-shadow-container-collector-source-manifest-v1"
)
CONTAINER_SOURCE_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
CONTAINER_SOURCE_MANIFEST_MAX_FILES = 5_000
CONTAINER_REQUIRED_SOURCE_PATHS = frozenset(
    {
        "scripts/collect_production_shadow_compose_runtime_snapshot.py",
        "scripts/collect_three_site_staging_convergence_snapshot.py",
        "core/__init__.py",
        "models/__init__.py",
    }
)


@dataclass(frozen=True)
class _ContainerManifestSource:
    """One approved project source for the container-only import boundary."""

    module_name: str
    relative_path: PurePosixPath
    sha256: str
    package: bool


class _ContainerManifestSourceLoader:
    """Compile one project module only after its manifest-bound bytes match."""

    def __init__(self, source: _ContainerManifestSource, *, release_root: Path) -> None:
        self._source = source
        self._release_root = release_root

    def create_module(self, _spec: object) -> None:
        return None

    def is_package(self, _fullname: str) -> bool:
        return self._source.package

    def get_filename(self, _fullname: str) -> str:
        return str(self._release_root / self._source.relative_path)

    def exec_module(self, module: Any) -> None:
        payload = _read_container_manifest_source(
            self._release_root,
            self._source.relative_path,
        )
        if hashlib.sha256(payload).hexdigest() != self._source.sha256:
            raise ConvergenceSnapshotError("container manifest project module digest differs")
        try:
            code = compile(
                payload,
                self.get_filename(self._source.module_name),
                "exec",
                dont_inherit=True,
            )
        except (SyntaxError, ValueError, TypeError) as exc:
            raise ConvergenceSnapshotError("container manifest project module cannot be compiled") from exc
        module.__file__ = self.get_filename(self._source.module_name)
        module.__cached__ = None
        exec(code, module.__dict__)


class _ContainerManifestSourceFinder:
    """Deny every core/models import that is absent from the source manifest."""

    def __init__(
        self,
        sources: Mapping[str, _ContainerManifestSource],
        *,
        release_root: Path,
    ) -> None:
        self._sources = dict(sources)
        self._release_root = release_root

    def find_spec(
        self,
        fullname: str,
        _path: object = None,
        _target: object = None,
    ) -> object | None:
        source = self._sources.get(fullname)
        if source is not None:
            return importlib.util.spec_from_loader(
                fullname,
                _ContainerManifestSourceLoader(source, release_root=self._release_root),
                is_package=source.package,
            )
        if fullname.split(".", maxsplit=1)[0] in REQUIRED_PROJECT_PACKAGES:
            raise ModuleNotFoundError(
                "container project import is absent from the exact source manifest"
            )
        return None


def _canonical_container_manifest_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConvergenceSnapshotError("container source manifest is not canonical JSON") from exc


def _container_manifest_module_path(path: PurePosixPath) -> tuple[str, bool] | None:
    """Accept only ordinary core/models Python modules, never namespace paths."""

    if len(path.parts) < 2 or path.parts[0] not in REQUIRED_PROJECT_PACKAGES:
        return None
    return _module_name_from_git_path(path)


def _read_container_manifest_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or not 1 <= before.st_size <= CONTAINER_SOURCE_MANIFEST_MAX_BYTES
        ):
            raise ConvergenceSnapshotError("container source manifest file is unsafe")
        payload = bytearray()
        while len(payload) <= CONTAINER_SOURCE_MANIFEST_MAX_BYTES:
            block = os.read(
                descriptor,
                min(64 * 1024, CONTAINER_SOURCE_MANIFEST_MAX_BYTES + 1 - len(payload)),
            )
            if not block:
                break
            payload.extend(block)
        if len(payload) > CONTAINER_SOURCE_MANIFEST_MAX_BYTES:
            raise ConvergenceSnapshotError("container source manifest exceeds its bound")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ConvergenceSnapshotError("container source manifest changed while read")
        return bytes(payload)
    except OSError as exc:
        raise ConvergenceSnapshotError("container source manifest is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_container_manifest_source(release_root: Path, relative_path: PurePosixPath) -> bytes:
    """Open a manifest path below the mounted release without following links."""

    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise ConvergenceSnapshotError("container manifest project path is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    allow_empty = relative_path.as_posix() in {
        "core/__init__.py",
        "models/__init__.py",
    }
    directory_descriptor = -1
    file_descriptor = -1
    try:
        directory_descriptor = os.open(release_root, directory_flags)
        _assert_root_controlled_descriptor(
            directory_descriptor,
            label="container manifest release root",
            directory=True,
            private=True,
        )
        for part in relative_path.parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
            _assert_root_controlled_descriptor(
                directory_descriptor,
                label="container manifest project directory",
                directory=True,
                private=False,
            )
        file_descriptor = os.open(
            relative_path.parts[-1],
            file_flags,
            dir_fd=directory_descriptor,
        )
        before = _assert_root_controlled_descriptor(
            file_descriptor,
            label="container manifest project source",
            directory=False,
            private=False,
        )
        if not (0 if allow_empty else 1) <= before.st_size <= MAX_PROJECT_SOURCE_BYTES:
            raise ConvergenceSnapshotError("container manifest project source size is invalid")
        payload = bytearray()
        while len(payload) <= MAX_PROJECT_SOURCE_BYTES:
            block = os.read(file_descriptor, min(64 * 1024, MAX_PROJECT_SOURCE_BYTES + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        if len(payload) > MAX_PROJECT_SOURCE_BYTES:
            raise ConvergenceSnapshotError("container manifest project source is oversized")
        after = _assert_root_controlled_descriptor(
            file_descriptor,
            label="container manifest project source",
            directory=False,
            private=False,
        )
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ConvergenceSnapshotError("container manifest project source changed while read")
        return bytes(payload)
    except OSError as exc:
        raise ConvergenceSnapshotError("container manifest project source is unavailable") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _load_container_manifest_sources(
    *,
    source_manifest_path: Path,
    release_root: Path,
    release_sha: str,
) -> dict[str, _ContainerManifestSource]:
    """Parse and verify the complete source closure before importing core/models."""

    payload = _read_container_manifest_bytes(source_manifest_path)
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceSnapshotError("container source manifest is invalid") from exc
    if (
        not isinstance(document, Mapping)
        or set(document) != {"schema", "release_sha", "release_tree_sha", "files", "source_manifest_sha256"}
        or document.get("schema") != CONTAINER_SOURCE_MANIFEST_SCHEMA
        or document.get("release_sha") != release_sha
        or SHA40.fullmatch(document.get("release_tree_sha", "")) is None
        or not isinstance(document.get("files"), Mapping)
        or not isinstance(document.get("source_manifest_sha256"), str)
    ):
        raise ConvergenceSnapshotError("container source manifest differs")
    unsigned = {key: value for key, value in document.items() if key != "source_manifest_sha256"}
    manifest_sha256 = hashlib.sha256(_canonical_container_manifest_json(unsigned)).hexdigest()
    if document["source_manifest_sha256"] != manifest_sha256:
        raise ConvergenceSnapshotError("container source manifest digest differs")
    if _canonical_container_manifest_json(document) != payload:
        raise ConvergenceSnapshotError("container source manifest is not canonical")
    files = document["files"]
    if (
        not CONTAINER_REQUIRED_SOURCE_PATHS.issubset(files)
        or not 1 <= len(files) <= CONTAINER_SOURCE_MANIFEST_MAX_FILES
    ):
        raise ConvergenceSnapshotError("container source manifest is incomplete")
    sources: dict[str, _ContainerManifestSource] = {}
    for raw_path, expected_sha256 in files.items():
        if not isinstance(raw_path, str) or not isinstance(expected_sha256, str):
            raise ConvergenceSnapshotError("container source manifest entry is invalid")
        try:
            relative_path = PurePosixPath(raw_path)
        except TypeError as exc:
            raise ConvergenceSnapshotError("container source manifest path is invalid") from exc
        if (
            raw_path != relative_path.as_posix()
            or not raw_path.isascii()
            or relative_path.is_absolute()
            or any(part in {"", ".", "..", "__pycache__"} for part in relative_path.parts)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or expected_sha256 == "0" * 64
        ):
            raise ConvergenceSnapshotError("container source manifest entry is invalid")
        if raw_path in {
            "scripts/collect_production_shadow_compose_runtime_snapshot.py",
            "scripts/collect_three_site_staging_convergence_snapshot.py",
        }:
            source = None
        else:
            module = _container_manifest_module_path(relative_path)
            if module is None:
                raise ConvergenceSnapshotError("container source manifest path escapes project namespaces")
            module_name, package = module
            if module_name in sources:
                raise ConvergenceSnapshotError("container source manifest module is ambiguous")
            source = _ContainerManifestSource(
                module_name=module_name,
                relative_path=relative_path,
                sha256=expected_sha256,
                package=package,
            )
        observed = _read_container_manifest_source(release_root, relative_path)
        if hashlib.sha256(observed).hexdigest() != expected_sha256:
            raise ConvergenceSnapshotError("container source manifest entry digest differs")
        if source is not None:
            sources[source.module_name] = source
    for package in REQUIRED_PROJECT_PACKAGES:
        source = sources.get(package)
        if source is None or not source.package:
            raise ConvergenceSnapshotError("container source manifest lacks a regular project package")
    return sources


def _install_container_manifest_source_finder(
    *,
    source_manifest_path: Path,
    release_root: Path,
    release_sha: str,
) -> _ContainerManifestSourceFinder:
    preloaded = _project_modules_already_loaded()
    if preloaded:
        raise ConvergenceSnapshotError("container collector cannot trust preloaded project modules")
    sources = _load_container_manifest_sources(
        source_manifest_path=source_manifest_path,
        release_root=release_root,
        release_sha=release_sha,
    )
    finder = _ContainerManifestSourceFinder(sources, release_root=release_root)
    sys.meta_path.insert(0, finder)
    return finder


def _validate_container_manifest_imports(finder: _ContainerManifestSourceFinder) -> None:
    project_modules = _project_modules_already_loaded()
    if not project_modules:
        raise ConvergenceSnapshotError("container collector imported no project modules")
    for name in project_modules:
        module = sys.modules.get(name)
        spec = getattr(module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        if (
            not isinstance(loader, _ContainerManifestSourceLoader)
            or loader._source.module_name != name
            or loader._release_root != finder._release_root
        ):
            raise ConvergenceSnapshotError("container collector project module escaped source manifest")


def _project_modules_already_loaded(
    *,
    project_roots: frozenset[str] = REQUIRED_PROJECT_PACKAGES,
) -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if name.split(".", maxsplit=1)[0] in project_roots
    )


def _require_isolated_collector_interpreter() -> None:
    """Prove ``-I -S`` was active before this process imported application code."""

    flags = sys.flags
    if (
        getattr(flags, "isolated", 0) != 1
        or getattr(flags, "ignore_environment", 0) != 1
        or getattr(flags, "no_user_site", 0) != 1
        or getattr(flags, "no_site", 0) != 1
        or not bool(getattr(flags, "safe_path", False))
    ):
        raise ConvergenceSnapshotError(
            "convergence collector must be launched by an isolated Python interpreter (-I -S)"
        )
    if any(not isinstance(entry, str) or not Path(entry).is_absolute() for entry in sys.path):
        raise ConvergenceSnapshotError("convergence collector isolated interpreter path is unsafe")
    _assert_trusted_interpreter_import_paths()
    baseline_finders = (
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    )
    expected_finders = (
        ((_PROJECT_SOURCE_FINDER,) if _PROJECT_SOURCE_FINDER is not None else ())
        + baseline_finders
    )
    if tuple(sys.meta_path) != expected_finders:
        raise ConvergenceSnapshotError("convergence collector import finder state is unsafe")
    if "PYTHONPATH" in os.environ:
        raise ConvergenceSnapshotError("convergence collector must not receive PYTHONPATH")
    if any(name in sys.modules for name in ("site", "sitecustomize", "usercustomize")):
        raise ConvergenceSnapshotError("convergence collector must start without site processing")
    project_roots = (
        _PROJECT_SOURCE_FINDER._project_roots
        if _PROJECT_SOURCE_FINDER is not None
        else REQUIRED_PROJECT_PACKAGES
    )
    preloaded = _project_modules_already_loaded(project_roots=project_roots)
    if preloaded and _PROJECT_SOURCE_FINDER is None:
        raise ConvergenceSnapshotError("convergence collector cannot trust preloaded project modules")
    for name in preloaded:
        module = sys.modules.get(name)
        spec = getattr(module, "__spec__", None)
        if not isinstance(getattr(spec, "loader", None), _GitBlobSourceLoader):
            raise ConvergenceSnapshotError("convergence collector project module escaped Git source loader")


def _assert_trusted_interpreter_import_paths() -> None:
    """Keep the isolated bootstrap importer out of cwd/release-controlled paths."""

    try:
        allowed_roots = tuple(
            root.resolve(strict=True)
            for root in {
                Path(sys.base_prefix),
                Path(sys.exec_prefix),
                Path("/usr/lib"),
                Path("/usr/local/lib"),
            }
            if root.exists()
        )
    except OSError as exc:
        raise ConvergenceSnapshotError("trusted interpreter roots are unavailable") from exc
    if not allowed_roots:
        raise ConvergenceSnapshotError("trusted interpreter roots are unavailable")
    for entry in sys.path:
        candidate = Path(entry)
        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise ConvergenceSnapshotError("convergence collector interpreter path is unavailable") from exc
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise ConvergenceSnapshotError("convergence collector interpreter path escaped trusted roots")
        current = resolved if resolved.exists() else resolved.parent
        while True:
            try:
                metadata = current.stat(follow_symlinks=False)
            except OSError as exc:
                raise ConvergenceSnapshotError("convergence collector interpreter path is unavailable") from exc
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                raise ConvergenceSnapshotError("convergence collector interpreter path is not root-controlled")
            if current == current.parent:
                break
            current = current.parent


def _parse_inherited_descriptor(value: str | None, *, label: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[3-9][0-9]*", value) is None:
        raise ConvergenceSnapshotError(f"{label} descriptor binding is invalid")
    return int(value)


def _assert_root_controlled_descriptor(
    descriptor: int,
    *,
    label: str,
    directory: bool,
    private: bool,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ConvergenceSnapshotError(f"{label} descriptor is unavailable") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    unsafe_mode = 0o077 if private else 0o022
    if (
        not expected(metadata.st_mode)
        or metadata.st_uid != 0
        or (not directory and metadata.st_nlink != 1)
        or stat.S_IMODE(metadata.st_mode) & unsafe_mode
    ):
        raise ConvergenceSnapshotError(f"{label} descriptor is not root-controlled")
    return metadata


def _bootstrap_held_release_import_root() -> tuple[int, str, int]:
    """Validate only inherited FDs before any project/site-package import."""

    _require_isolated_collector_interpreter()
    descriptor = _parse_inherited_descriptor(
        os.environ.get(HELD_RELEASE_ROOT_FD_ENV),
        label="held release root",
    )
    metadata = _assert_root_controlled_descriptor(
        descriptor,
        label="held release root",
        directory=True,
        private=True,
    )
    collector_descriptor = _parse_inherited_descriptor(
        os.environ.get(HELD_COLLECTOR_FD_ENV),
        label="held convergence collector",
    )
    collector_metadata = _assert_root_controlled_descriptor(
        collector_descriptor,
        label="held convergence collector",
        directory=False,
        private=False,
    )
    import_root = f"/proc/self/fd/{descriptor}"
    try:
        observed = os.stat(import_root, follow_symlinks=True)
    except OSError as exc:
        raise ConvergenceSnapshotError("held release root import path is unavailable") from exc
    if (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise ConvergenceSnapshotError("held release root import path differs from descriptor")
    try:
        executing = os.stat(__file__, follow_symlinks=True)
    except OSError as exc:
        raise ConvergenceSnapshotError("held convergence collector path is unavailable") from exc
    if (executing.st_dev, executing.st_ino) != (
        collector_metadata.st_dev,
        collector_metadata.st_ino,
    ):
        raise ConvergenceSnapshotError(
            "held convergence collector path differs from descriptor"
        )
    # Do not add the held release to ``sys.path``.  The source finder installed
    # after Git-object validation is the sole project import route; a normal
    # path finder could otherwise execute a modified worktree module or pyc.
    if import_root in sys.path:
        raise ConvergenceSnapshotError("held release root must not be an ambient import path")
    return descriptor, import_root, collector_descriptor


def _module_name_from_git_path(path: PurePosixPath) -> tuple[str, bool] | None:
    """Return an importable module identity only for normal Python source paths."""

    if path.suffix != ".py" or path.is_absolute() or not path.parts:
        return None
    parts = path.parts
    if any(
        part in {"", ".", ".."}
        or not part.isidentifier()
        or keyword.iskeyword(part)
        for part in parts[:-1]
    ):
        return None
    stem = path.stem
    if stem == "__init__":
        module_parts = parts[:-1]
        package = True
    elif stem.isidentifier() and not keyword.iskeyword(stem):
        module_parts = (*parts[:-1], stem)
        package = False
    else:
        return None
    if not module_parts or any(keyword.iskeyword(part) for part in module_parts):
        return None
    return ".".join(module_parts), package


def _verified_project_sources(release_sha: str) -> dict[str, _GitProjectSource]:
    """Index every importable application source by its exact Git tree blob."""

    if SHA40.fullmatch(release_sha) is None:
        raise ConvergenceSnapshotError("collector release identity is invalid")
    payload = _strict_git_bytes(
        ["ls-tree", "-r", "-z", "--full-tree", release_sha],
        label="held release source tree",
        max_bytes=MAX_GIT_TREE_BYTES,
    )
    sources: dict[str, _GitProjectSource] = {}
    paths: set[PurePosixPath] = set()
    records = payload.split(b"\0")
    if len(records) > MAX_PROJECT_SOURCE_MODULES * 4:
        raise ConvergenceSnapshotError("held release source tree is oversized")
    for record in records:
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", maxsplit=1)
            mode, object_type, blob_sha = metadata.split(b" ", maxsplit=2)
            text_path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConvergenceSnapshotError("held release source tree entry is invalid") from exc
        if object_type != b"blob":
            continue
        if mode not in {b"100644", b"100755"} or re.fullmatch(rb"[0-9a-f]{40}", blob_sha) is None:
            raise ConvergenceSnapshotError("held release source tree blob is invalid")
        path = PurePosixPath(text_path)
        module = _module_name_from_git_path(path)
        if module is None:
            continue
        module_name, package = module
        if path in paths or module_name in sources:
            raise ConvergenceSnapshotError("held release source tree is ambiguous")
        paths.add(path)
        sources[module_name] = _GitProjectSource(
            module_name=module_name,
            relative_path=path,
            blob_sha=blob_sha.decode("ascii"),
            package=package,
        )
    if len(sources) > MAX_PROJECT_SOURCE_MODULES:
        raise ConvergenceSnapshotError("held release source tree has too many modules")
    missing = [name for name in REQUIRED_PROJECT_PACKAGES if name not in sources or not sources[name].package]
    if missing:
        raise ConvergenceSnapshotError("held release source tree lacks required project packages")
    return sources


def _git_blob_bytes(source: _GitProjectSource) -> bytes:
    """Read a source object by immutable blob id, never through the worktree."""

    payload = _strict_git_bytes(
        ["cat-file", "blob", source.blob_sha],
        label="verified project module",
        max_bytes=MAX_PROJECT_SOURCE_BYTES,
    )
    return payload


def _held_project_source_bytes(source: _GitProjectSource) -> bytes:
    """Read one worktree source through held no-follow descriptors only.

    This is not a Git worktree operation: it does no attribute lookup, filter,
    status, index, or configuration evaluation.  It exists solely to reject a
    replaced local file before the immutable Git blob is compiled and executed.
    """

    if _HELD_RELEASE_ROOT_FD is None:
        raise ConvergenceSnapshotError("held release root descriptor is unavailable")
    if any(part in {"", ".", ".."} for part in source.relative_path.parts):
        raise ConvergenceSnapshotError("verified project module path is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor = -1
    file_descriptor = -1
    try:
        directory_descriptor = os.dup(_HELD_RELEASE_ROOT_FD)
        _assert_root_controlled_descriptor(
            directory_descriptor,
            label="held release root",
            directory=True,
            private=True,
        )
        for part in source.relative_path.parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
            _assert_root_controlled_descriptor(
                directory_descriptor,
                label="held project module directory",
                directory=True,
                private=False,
            )
        file_descriptor = os.open(
            source.relative_path.parts[-1],
            file_flags,
            dir_fd=directory_descriptor,
        )
        before = _assert_root_controlled_descriptor(
            file_descriptor,
            label="held project module",
            directory=False,
            private=False,
        )
        if before.st_size > MAX_PROJECT_SOURCE_BYTES:
            raise ConvergenceSnapshotError("held project module size is invalid")
        payload = bytearray()
        while True:
            block = os.read(file_descriptor, 64 * 1024)
            if not block:
                break
            payload.extend(block)
            if len(payload) > MAX_PROJECT_SOURCE_BYTES:
                raise ConvergenceSnapshotError("held project module is oversized")
        after = _assert_root_controlled_descriptor(
            file_descriptor,
            label="held project module",
            directory=False,
            private=False,
        )
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ConvergenceSnapshotError("held project module changed while being read")
        return bytes(payload)
    except OSError as exc:
        raise ConvergenceSnapshotError("held project module is unavailable") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _install_verified_project_source_loader(release_sha: str) -> None:
    """Install the only importer allowed to execute release application source."""

    global _PROJECT_SOURCE_FINDER
    if _PROJECT_SOURCE_FINDER is not None:
        return
    sources = _verified_project_sources(release_sha)
    project_roots = frozenset(
        source.module_name.split(".", maxsplit=1)[0]
        for source in sources.values()
    )
    preloaded = _project_modules_already_loaded(project_roots=project_roots)
    if preloaded:
        raise ConvergenceSnapshotError("convergence collector cannot trust preloaded project modules")
    finder = _GitReleaseSourceFinder(sources)
    sys.meta_path.insert(0, finder)
    _PROJECT_SOURCE_FINDER = finder


def _validate_imported_project_module_provenance() -> None:
    """Require every imported application module to have the Git source loader."""

    if _PROJECT_SOURCE_FINDER is None:
        raise ConvergenceSnapshotError("verified project source loader is unavailable")
    project_modules = _project_modules_already_loaded(
        project_roots=_PROJECT_SOURCE_FINDER._project_roots
    )
    if not project_modules:
        raise ConvergenceSnapshotError("convergence collector imported no project modules")
    for name in project_modules:
        module = sys.modules.get(name)
        spec = getattr(module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        if not isinstance(loader, _GitBlobSourceLoader):
            raise ConvergenceSnapshotError("convergence collector project module escaped Git source loader")


def _root_controlled_system_package_root(path: Path) -> None:
    """Allow a fixed system dependency root without running ``site`` hooks."""

    if not path.is_absolute() or path.is_symlink():
        raise ConvergenceSnapshotError("trusted system package root is unsafe")
    current = path
    while True:
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ConvergenceSnapshotError("trusted system package root is unavailable") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ConvergenceSnapshotError("trusted system package root is not root-controlled")
        if current == current.parent:
            return
        current = current.parent


def _install_trusted_system_package_roots() -> None:
    for root in TRUSTED_SYSTEM_PACKAGE_ROOTS:
        if not root.exists():
            continue
        _root_controlled_system_package_root(root)
        text_root = os.fspath(root)
        if text_root not in sys.path:
            sys.path.append(text_root)


def _strict_git_bytes(arguments: list[str], *, label: str, max_bytes: int) -> bytes:
    if _HELD_RELEASE_ROOT_FD is None:
        raise ConvergenceSnapshotError("held release root descriptor is unavailable")
    _require_fixed_git_object_command(arguments)
    try:
        result = subprocess.run(
            [
                GIT,
                *GIT_STRICT_OPTIONS,
                "--no-replace-objects",
                "-C",
                f"/proc/self/fd/{_HELD_RELEASE_ROOT_FD}",
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=GIT_SAFE_ENV,
            close_fds=True,
            pass_fds=(_HELD_RELEASE_ROOT_FD,),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConvergenceSnapshotError(f"{label} Git readback is unavailable") from exc
    if (
        result.returncode != 0
        or len(result.stdout) > max_bytes
        or len(result.stderr) > MAX_GIT_OUTPUT_BYTES
    ):
        raise ConvergenceSnapshotError(f"{label} Git readback is invalid")
    return result.stdout


def _require_fixed_git_object_command(arguments: list[str]) -> None:
    """Allow only the object/ref reads needed by the sealed source loader."""

    if (
        len(arguments) == 5
        and arguments[:4] == ["ls-tree", "-r", "-z", "--full-tree"]
        and isinstance(arguments[4], str)
        and SHA40.fullmatch(arguments[4]) is not None
    ):
        return
    if (
        len(arguments) == 3
        and arguments[:2] == ["rev-parse", "--verify"]
        and isinstance(arguments[2], str)
        and re.fullmatch(r"[0-9a-f]{40}\^\{(?:commit|tree)\}", arguments[2]) is not None
    ):
        return
    if len(arguments) == 3 and arguments[:2] == ["cat-file", "blob"] and isinstance(arguments[2], str):
        target = arguments[2]
        if SHA40.fullmatch(target) is not None:
            return
        if re.fullmatch(r"[0-9a-f]{40}:.+", target) is not None:
            relative = target.split(":", maxsplit=1)[1]
            path = PurePosixPath(relative)
            if (
                not path.is_absolute()
                and path.parts
                and all(part not in {"", ".", ".."} for part in path.parts)
            ):
                return
    raise ConvergenceSnapshotError("held release Git command is not a fixed object read")


def _strict_git_text(arguments: list[str], *, label: str) -> str:
    try:
        return _strict_git_bytes(
            arguments,
            label=label,
            max_bytes=MAX_GIT_OUTPUT_BYTES,
        ).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ConvergenceSnapshotError(f"{label} Git readback is not ASCII") from exc


def _held_collector_sha256() -> str:
    if _HELD_COLLECTOR_FD is None:
        raise ConvergenceSnapshotError("held convergence collector descriptor is unavailable")
    try:
        before = os.fstat(_HELD_COLLECTOR_FD)
        os.lseek(_HELD_COLLECTOR_FD, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            block = os.read(_HELD_COLLECTOR_FD, 64 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(_HELD_COLLECTOR_FD)
        os.lseek(_HELD_COLLECTOR_FD, 0, os.SEEK_SET)
    except OSError as exc:
        raise ConvergenceSnapshotError("held convergence collector cannot be read") from exc
    stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
    if any(getattr(before, field) != getattr(after, field) for field in stable):
        raise ConvergenceSnapshotError("held convergence collector changed while being read")
    return digest.hexdigest()


def _verify_held_release_before_runtime_import(release_sha: str) -> None:
    """Bind this collector inode only to fixed Git commit/tree/blob objects."""

    if SHA40.fullmatch(release_sha) is None:
        raise ConvergenceSnapshotError("collector release identity is invalid")
    if _HELD_RELEASE_ROOT_FD is None:
        raise ConvergenceSnapshotError("held release root descriptor is unavailable")
    commit = _strict_git_text(
        ["rev-parse", "--verify", f"{release_sha}^{{commit}}"],
        label="held release commit",
    )
    tree = _strict_git_text(
        ["rev-parse", "--verify", f"{release_sha}^{{tree}}"],
        label="held release tree",
    )
    if commit != release_sha or SHA40.fullmatch(tree) is None:
        raise ConvergenceSnapshotError("held release commit/tree object is not exact")
    expected = _strict_git_bytes(
        ["cat-file", "blob", f"{release_sha}:{RUNTIME_COLLECTOR_RELATIVE.as_posix()}"],
        label="held convergence collector",
        max_bytes=64 * 1024 * 1024,
    )
    if not expected or hashlib.sha256(expected).hexdigest() != _held_collector_sha256():
        raise ConvergenceSnapshotError("held convergence collector differs from the exact release")
    try:
        os.stat(".env", dir_fd=_HELD_RELEASE_ROOT_FD, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConvergenceSnapshotError("held release .env check failed") from exc
    raise ConvergenceSnapshotError("held release must not contain a .env file")


def _load_trusted_runtime_dependencies(release_sha: str) -> None:
    """Import application/runtime dependencies only after provenance is fixed."""

    global _RUNTIME_IMPORTS_READY
    global func, select, text, settings, AsyncSessionLocal, _hash_file
    global resolve_runtime_identity, build_database_parity_snapshot
    global DrBlobManifest, DrConflictQuarantine, DrDestinationCursor, DrEvent
    global DrProducerCursor, DrStreamCheckpoint
    if _RUNTIME_IMPORTS_READY:
        return
    _require_held_release_execution(release_sha=release_sha)
    _verify_held_release_before_runtime_import(release_sha)
    _install_verified_project_source_loader(release_sha)
    _install_trusted_system_package_roots()
    try:
        from sqlalchemy import func as sqlalchemy_func, select as sqlalchemy_select, text as sqlalchemy_text
        from core.config import settings as runtime_settings
        from core.db import AsyncSessionLocal as runtime_session_factory
        from core.dr_blob_plane import _hash_file as runtime_hash_file
        from core.runtime_identity import resolve_runtime_identity as runtime_identity
        from core.sync_parity import build_database_parity_snapshot as runtime_parity_snapshot
        from models.dr_event import (
            DrBlobManifest as runtime_blob_manifest,
            DrConflictQuarantine as runtime_conflict_quarantine,
            DrDestinationCursor as runtime_destination_cursor,
            DrEvent as runtime_event,
            DrProducerCursor as runtime_producer_cursor,
            DrStreamCheckpoint as runtime_stream_checkpoint,
        )
    except Exception as exc:
        raise ConvergenceSnapshotError(
            "trusted runtime dependencies are unavailable from the sealed interpreter roots"
        ) from exc
    _validate_imported_project_module_provenance()
    func = sqlalchemy_func
    select = sqlalchemy_select
    text = sqlalchemy_text
    settings = runtime_settings
    AsyncSessionLocal = runtime_session_factory
    _hash_file = runtime_hash_file
    resolve_runtime_identity = runtime_identity
    build_database_parity_snapshot = runtime_parity_snapshot
    DrBlobManifest = runtime_blob_manifest
    DrConflictQuarantine = runtime_conflict_quarantine
    DrDestinationCursor = runtime_destination_cursor
    DrEvent = runtime_event
    DrProducerCursor = runtime_producer_cursor
    DrStreamCheckpoint = runtime_stream_checkpoint
    _RUNTIME_IMPORTS_READY = True


def load_container_runtime_dependencies(
    *,
    release_sha: str,
    release_root: Path,
    source_manifest_path: Path | None = None,
) -> None:
    """Initialize the same read-only collector inside a pinned app image.

    This path is deliberately separate from the descriptor-bound host
    collector.  Docker cannot inherit the controller's held file descriptors;
    the container caller instead supplies the release root through its fixed,
    read-only bind mount.  The small wrapper entrypoint proves this path is
    its own release root before it calls here.
    """

    global _RUNTIME_IMPORTS_READY
    global func, select, text, settings, AsyncSessionLocal, _hash_file
    global resolve_runtime_identity, build_database_parity_snapshot
    global DrBlobManifest, DrConflictQuarantine, DrDestinationCursor, DrEvent
    global DrProducerCursor, DrStreamCheckpoint
    root = release_root.resolve(strict=True)
    if (
        not root.is_dir()
        or root.name != release_sha
        or release_sha != str(root.name)
        or Path(__file__).resolve().parent.parent != root
        or root.joinpath(".env").exists()
    ):
        raise ConvergenceSnapshotError("container collector release root differs")
    _require_isolated_collector_interpreter()
    # ``-I -S`` intentionally removed site processing, including .pth hooks.
    # Add only the fixed, root-controlled system distribution roots needed by
    # this pinned app image before importing SQLAlchemy or application code.
    _install_trusted_system_package_roots()
    preloaded = [
        name
        for name in sys.modules
        if name.split(".", maxsplit=1)[0] in {"core", "models"}
    ]
    if preloaded:
        raise ConvergenceSnapshotError("container collector cannot trust preloaded project modules")
    manifest_finder: _ContainerManifestSourceFinder | None = None
    if source_manifest_path is not None:
        manifest_finder = _install_container_manifest_source_finder(
            source_manifest_path=source_manifest_path,
            release_root=root,
            release_sha=release_sha,
        )
    else:
        root_text = os.fspath(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    if _RUNTIME_IMPORTS_READY:
        return
    try:
        from sqlalchemy import func as sqlalchemy_func, select as sqlalchemy_select, text as sqlalchemy_text
        from core.config import settings as runtime_settings
        from core.db import AsyncSessionLocal as runtime_session_factory
        from core.dr_blob_plane import _hash_file as runtime_hash_file
        from core.runtime_identity import resolve_runtime_identity as runtime_identity
        from core.sync_parity import build_database_parity_snapshot as runtime_parity_snapshot
        from models.dr_event import (
            DrBlobManifest as runtime_blob_manifest,
            DrConflictQuarantine as runtime_conflict_quarantine,
            DrDestinationCursor as runtime_destination_cursor,
            DrEvent as runtime_event,
            DrProducerCursor as runtime_producer_cursor,
            DrStreamCheckpoint as runtime_stream_checkpoint,
        )
    except Exception as exc:
        raise ConvergenceSnapshotError("container collector dependencies are unavailable") from exc
    if manifest_finder is not None:
        _validate_container_manifest_imports(manifest_finder)
    else:
        for module_name in (
            "core.config",
            "core.db",
            "core.dr_blob_plane",
            "core.runtime_identity",
            "core.sync_parity",
            "models.dr_event",
        ):
            module_file = getattr(sys.modules.get(module_name), "__file__", None)
            try:
                if module_file is None or Path(module_file).resolve().is_relative_to(root) is False:
                    raise ValueError
            except (OSError, ValueError) as exc:
                raise ConvergenceSnapshotError("container collector project module escaped release root") from exc
    func = sqlalchemy_func
    select = sqlalchemy_select
    text = sqlalchemy_text
    settings = runtime_settings
    AsyncSessionLocal = runtime_session_factory
    _hash_file = runtime_hash_file
    resolve_runtime_identity = runtime_identity
    build_database_parity_snapshot = runtime_parity_snapshot
    DrBlobManifest = runtime_blob_manifest
    DrConflictQuarantine = runtime_conflict_quarantine
    DrEvent = runtime_event
    DrProducerCursor = runtime_producer_cursor
    DrStreamCheckpoint = runtime_stream_checkpoint
    _RUNTIME_IMPORTS_READY = True


if __name__ == "__main__":
    try:
        (
            _HELD_RELEASE_ROOT_FD,
            _HELD_RELEASE_IMPORT_ROOT,
            _HELD_COLLECTOR_FD,
        ) = _bootstrap_held_release_import_root()
    except ConvergenceSnapshotError as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


SITES = ("bot_fi", "webapp_fi", "webapp_ir")
WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = "three-site-staging-convergence-site-snapshot-v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _zero_hash() -> str:
    return "0" * 64


def _hash_or_zero(value: Any, *, label: str, zero_allowed: bool) -> str:
    raw = str(value or "")
    if SHA256.fullmatch(raw) is None or (not zero_allowed and raw == _zero_hash()):
        raise ConvergenceSnapshotError(f"{label} hash is invalid")
    return raw


async def _stream_transaction_hash(
    db,
    *,
    origin_site: str,
    producer_epoch: int,
    destination_site: str,
    destination_sequence: int,
) -> str:
    if destination_sequence == 0:
        return _zero_hash()
    result = await db.execute(
        text(
            "SELECT destination_streams -> CAST(:destination AS text) ->> 'transaction_hash' "
            "FROM dr_events WHERE origin_physical_site=:origin "
            "AND producer_epoch=:epoch "
            "AND (destination_streams -> CAST(:destination AS text) ->> 'sequence')::bigint=:sequence"
        ),
        {
            "origin": origin_site,
            "epoch": producer_epoch,
            "destination": destination_site,
            "sequence": destination_sequence,
        },
    )
    values = list(result.scalars())
    if len(values) != 1:
        raise ConvergenceSnapshotError("stream transaction tail is missing or ambiguous")
    return _hash_or_zero(values[0], label="stream transaction", zero_allowed=False)


async def _source_streams(db, *, site: str, producer_epoch: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrDestinationCursor).where(
                DrDestinationCursor.origin_physical_site == site,
                DrDestinationCursor.producer_epoch == producer_epoch,
            )
        )
    ).scalars().all()
    by_destination: dict[str, DrDestinationCursor] = {}
    for row in rows:
        destination = str(row.destination_site)
        if destination not in SITES or destination == site or destination in by_destination:
            raise ConvergenceSnapshotError("source destination cursor is invalid")
        by_destination[destination] = row
    output = []
    for destination in SITES:
        if destination == site:
            continue
        cursor = by_destination.get(destination)
        sequence = int(cursor.last_sequence) if cursor is not None else 0
        transaction_hash = await _stream_transaction_hash(
            db,
            origin_site=site,
            producer_epoch=producer_epoch,
            destination_site=destination,
            destination_sequence=sequence,
        )
        output.append(
            {
                "destination_site": destination,
                "source_sequence": sequence,
                "source_transaction_hash": transaction_hash,
            }
        )
    return output


async def _destination_streams(db, *, site: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DrStreamCheckpoint).where(DrStreamCheckpoint.destination_site == site)
        )
    ).scalars().all()
    output = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        origin = str(row.origin_physical_site)
        epoch = int(row.producer_epoch)
        received = int(row.contiguous_received_sequence)
        applied = int(row.contiguous_applied_sequence)
        if origin not in SITES or origin == site or epoch < 1 or received < applied or received < 0:
            raise ConvergenceSnapshotError("destination checkpoint is invalid")
        key = (origin, epoch)
        if key in seen:
            raise ConvergenceSnapshotError("destination checkpoint is duplicated")
        seen.add(key)
        output.append(
            {
                "origin_site": origin,
                "producer_epoch": epoch,
                "received_sequence": received,
                "applied_sequence": applied,
                "received_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=received,
                ),
                "applied_transaction_hash": await _stream_transaction_hash(
                    db,
                    origin_site=origin,
                    producer_epoch=epoch,
                    destination_site=site,
                    destination_sequence=applied,
                ),
            }
        )
    return sorted(output, key=lambda item: (item["origin_site"], item["producer_epoch"]))


async def _assert_single_source_epoch(db, *, site: str, producer_epoch: int) -> None:
    cursor_rows = (
        await db.execute(
            select(DrProducerCursor.producer_epoch, DrProducerCursor.last_sequence).where(
                DrProducerCursor.origin_physical_site == site
            )
        )
    ).all()
    if any(int(epoch) != producer_epoch and int(sequence) > 0 for epoch, sequence in cursor_rows):
        raise ConvergenceSnapshotError("historic producer epoch requires a multi-epoch convergence gate")
    foreign_event_count = int(
        await db.scalar(
            select(func.count(DrEvent.event_id)).where(
                DrEvent.origin_physical_site == site,
                DrEvent.producer_epoch != producer_epoch,
            )
        )
        or 0
    )
    if foreign_event_count:
        raise ConvergenceSnapshotError("historic producer event requires a multi-epoch convergence gate")


async def _blob_records(db, *, site: str) -> list[dict[str, Any]]:
    if site not in WEBAPP_SITES:
        return []
    rows = (
        await db.execute(
            select(DrBlobManifest)
            .where(DrBlobManifest.state != "tombstoned")
            .order_by(DrBlobManifest.content_hash)
        )
    ).scalars().all()
    root = Path(settings.dr_blob_root).resolve()
    records: list[dict[str, Any]] = []
    for row in rows:
        if row.state != "uploaded":
            raise ConvergenceSnapshotError("Blob manifest is not uploaded")
        content_hash = _hash_or_zero(row.content_hash, label="Blob content", zero_allowed=False)
        try:
            path = Path(str(row.local_path)).resolve(strict=True)
            path.relative_to(root)
            local_hash, local_size = _hash_file(path)
        except Exception as exc:
            raise ConvergenceSnapshotError("Blob local read-back failed") from exc
        if local_hash != content_hash or local_size != int(row.size_bytes):
            raise ConvergenceSnapshotError("Blob local content identity differs")
        records.append(
            {
                "content_hash": content_hash,
                "size_bytes": int(row.size_bytes),
                "object_version_id": str(row.object_version_id or ""),
                "object_ciphertext_hash": _hash_or_zero(
                    row.object_ciphertext_hash, label="Blob ciphertext", zero_allowed=False
                ),
                "object_ciphertext_size": int(row.object_ciphertext_size or 0),
                "encryption_key_id": str(row.encryption_key_id or ""),
                "encryption_algorithm": str(row.encryption_algorithm or ""),
                "local_content_hash": local_hash,
                "local_size_bytes": local_size,
            }
        )
    return records


async def collect(*, campaign_id: str, release_sha: str, plan_sha256: str, max_rows_per_table: int) -> dict[str, Any]:
    _require_held_release_execution(release_sha=release_sha)
    _load_trusted_runtime_dependencies(release_sha)
    return await _collect_loaded_runtime(
        campaign_id=campaign_id,
        release_sha=release_sha,
        plan_sha256=plan_sha256,
        max_rows_per_table=max_rows_per_table,
    )


async def collect_container_safe(
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
    max_rows_per_table: int,
    release_root: Path,
    source_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Collect through the pinned container image and read-only release bind."""

    load_container_runtime_dependencies(
        release_sha=release_sha,
        release_root=release_root,
        source_manifest_path=source_manifest_path,
    )
    return await _collect_loaded_runtime(
        campaign_id=campaign_id,
        release_sha=release_sha,
        plan_sha256=plan_sha256,
        max_rows_per_table=max_rows_per_table,
    )


async def _collect_loaded_runtime(
    *, campaign_id: str, release_sha: str, plan_sha256: str, max_rows_per_table: int
) -> dict[str, Any]:
    """Use only dependencies initialized by one of the two explicit loaders."""

    identity = resolve_runtime_identity()
    site = identity.physical_site
    if site not in SITES or str(settings.release_sha or "") != release_sha:
        raise ConvergenceSnapshotError("runtime identity/release differs from the convergence campaign")
    if max_rows_per_table < 1:
        raise ConvergenceSnapshotError("max rows per table is invalid")
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            producer_epoch = int(settings.dr_producer_epoch)
            if producer_epoch < 1:
                raise ConvergenceSnapshotError("runtime producer epoch is invalid")
            await _assert_single_source_epoch(db, site=site, producer_epoch=producer_epoch)
            parity = await build_database_parity_snapshot(
                db, mode="deep", max_rows_per_table=max_rows_per_table
            )
            if any(bool(table.get("truncated")) for table in parity["tables"].values()):
                raise ConvergenceSnapshotError("database parity snapshot exceeded its approved row bound")
            source_streams = await _source_streams(db, site=site, producer_epoch=producer_epoch)
            destination_streams = await _destination_streams(db, site=site)
            conflict_count = int(
                await db.scalar(
                    select(func.count(DrConflictQuarantine.quarantine_id)).where(
                        DrConflictQuarantine.resolved_at.is_(None)
                    )
                )
                or 0
            )
            blobs = await _blob_records(db, site=site)
        finally:
            await db.rollback()
    return {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "plan_sha256": plan_sha256,
        "site": site,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "producer_epoch": producer_epoch,
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": conflict_count,
        "database_snapshot": parity,
        "blob_records": blobs,
    }


def _require_held_release_execution(*, release_sha: str | None = None) -> None:
    """Reject imported/direct callers before they can reach runtime imports."""

    if (
        _HELD_RELEASE_ROOT_FD is None
        or _HELD_RELEASE_IMPORT_ROOT is None
        or _HELD_COLLECTOR_FD is None
        or os.environ.get(HELD_RELEASE_ROOT_FD_ENV) != str(_HELD_RELEASE_ROOT_FD)
        or os.environ.get(HELD_COLLECTOR_FD_ENV) != str(_HELD_COLLECTOR_FD)
        or _HELD_RELEASE_IMPORT_ROOT in sys.path
        or (_RUNTIME_IMPORTS_READY and _PROJECT_SOURCE_FINDER is None)
    ):
        raise ConvergenceSnapshotError(
            "convergence collector CLI requires a descriptor-bound isolated release"
        )
    _require_isolated_collector_interpreter()
    if release_sha is not None and SHA40.fullmatch(release_sha) is None:
        raise ConvergenceSnapshotError("collector release identity is invalid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--max-rows-per-table", type=int, default=10000)
    args = parser.parse_args(argv)
    try:
        if str(UUID(args.campaign_id)) != args.campaign_id:
            raise ValueError
        if SHA40.fullmatch(args.release_sha) is None or SHA256.fullmatch(args.plan_sha256) is None:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise SystemExit("campaign identity is invalid") from exc
    try:
        _require_held_release_execution(release_sha=args.release_sha)
        _load_trusted_runtime_dependencies(args.release_sha)
        print(_canonical(asyncio.run(collect(
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
            max_rows_per_table=args.max_rows_per_table,
        ))))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
