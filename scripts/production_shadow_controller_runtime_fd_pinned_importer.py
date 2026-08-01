#!/usr/bin/env python3
"""FD-pinned importer for one exact ``scripts`` source map.

This is a small stdlib-only primitive intended to be copied into a separately
root-installed dispatcher.  It deliberately has no CLI, no release-plan
parsing, no Git operation, and no runtime/materialization behavior.  Its
caller supplies an already-proven release directory descriptor and an exact
map of ``scripts`` source paths to SHA-256 digests.

While installed, the finder is the only route for the ``scripts`` namespace:
an absent ``scripts`` or ``scripts.*`` name fails instead of falling through
to ``PathFinder``.  The release directory is never added to ``sys.path``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import importlib
import importlib.machinery
import keyword
import os
from pathlib import PurePosixPath
import re
import stat
import sys
from types import MappingProxyType, ModuleType
from typing import Iterable, Mapping


MAX_SOURCE_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

# This importer is deliberately limited to the v3 controller pre-runtime
# proof/materialization sources.  The producer and convergence/cutover paths
# require a separate, future post-runtime design; accepting them here would
# turn this small import primitive into an unreviewed execution boundary.
PRE_RUNTIME_SOURCE_PATHS = frozenset(
    {
        "scripts/__init__.py",
        "scripts/build_production_shadow_controller_runtime_closure.py",
        "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py",
        "scripts/verify_production_shadow_controller_runtime_closure.py",
    }
)
POST_RUNTIME_UNAVAILABLE_SOURCE_PATHS = frozenset(
    {
        "scripts/produce_production_shadow_convergence_source_set.py",
        "scripts/orchestrate_production_shadow_convergence_gate.py",
        "scripts/production_shadow_cutover_controller.py",
        "scripts/verify_production_shadow_phase_evidence.py",
    }
)
_PRE_RUNTIME_STDLIB_TOP_LEVELS = frozenset(
    set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
)
_PROTECTED_SYS_COLLECTIONS = frozenset({"path", "meta_path", "modules"})
_MUTATING_COLLECTION_METHODS = frozenset(
    {
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
        "update",
        "setdefault",
        "__delitem__",
        "__iadd__",
        "__imul__",
        "__ior__",
        "__setitem__",
    }
)
_FORBIDDEN_DYNAMIC_CALLS = frozenset(
    {
        "__import__",
        "builtins.__import__",
        "builtins.compile",
        "builtins.eval",
        "builtins.exec",
        "compile",
        "eval",
        "exec",
        "importlib.machinery.ExtensionFileLoader",
        "importlib.machinery.FileFinder",
        "importlib.machinery.SourceFileLoader",
        "importlib.machinery.SourcelessFileLoader",
        "importlib.reload",
        "importlib.util.module_from_spec",
        "importlib.util.spec_from_file_location",
        "runpy.run_module",
        "runpy.run_path",
    }
)
_FORBIDDEN_IMPORTLIB_SYMBOLS = frozenset(
    {
        "ExtensionFileLoader",
        "FileFinder",
        "SourceFileLoader",
        "SourcelessFileLoader",
        "module_from_spec",
        "spec_from_file_location",
    }
)
_FORBIDDEN_MAGIC_PATH_NAMES = frozenset(
    {"__builtins__", "__cached__", "__file__", "__loader__", "__path__", "__spec__"}
)
_FORBIDDEN_MAGIC_PATH_ATTRIBUTES = frozenset(
    {"__cached__", "__file__", "__loader__", "__path__", "__spec__"}
)
_ALLOWED_FD_LITERAL_FRAGMENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "scripts/__init__.py": (),
        "scripts/build_production_shadow_controller_runtime_closure.py": ("/proc/self/fd",),
        "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py": ("/proc/self/fd/",),
        "scripts/verify_production_shadow_controller_runtime_closure.py": ("/proc/self/fd/",),
    }
)
_VERIFIER_SOURCE_PATH = "scripts/verify_production_shadow_controller_runtime_closure.py"


class FdPinnedScriptsImportError(RuntimeError):
    """An exact FD-pinned ``scripts`` import cannot be completed safely."""


@dataclass(frozen=True)
class FdPinnedScriptsSource:
    """One source path and its already-proven SHA-256 digest.

    ``relative_path`` must identify an ordinary Python file below ``scripts``.
    The importer derives the canonical module name and package status from the
    path so a caller cannot give one source two Python identities.
    """

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class _BoundSource:
    module_name: str
    relative_path: PurePosixPath
    sha256: str
    is_package: bool


@dataclass(frozen=True)
class _DescriptorIdentity:
    descriptor: int
    device: int
    inode: int
    mode: int
    links: int
    uid: int
    gid: int
    size: int
    modified_ns: int
    changed_ns: int


def _descriptor_identity(metadata: os.stat_result, *, descriptor: int) -> _DescriptorIdentity:
    return _DescriptorIdentity(
        descriptor=descriptor,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        links=metadata.st_nlink,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _belongs_to_scripts(module_name: str) -> bool:
    return module_name == "scripts" or module_name.startswith("scripts.")


def _normalize_source(entry: FdPinnedScriptsSource) -> _BoundSource:
    if not isinstance(entry, FdPinnedScriptsSource):
        raise FdPinnedScriptsImportError("scripts source map entry is invalid")
    relative = entry.relative_path
    if (
        not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or "\\" in relative
    ):
        raise FdPinnedScriptsImportError("scripts source path is invalid")
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] != "scripts"
        or path.as_posix() != relative
    ):
        raise FdPinnedScriptsImportError("scripts source path is invalid")
    if len(path.parts) < 2 or path.suffix != ".py":
        raise FdPinnedScriptsImportError("scripts source path is not an ordinary Python module")
    directory_parts = path.parts[:-1]
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in directory_parts):
        raise FdPinnedScriptsImportError("scripts source module identity is invalid")
    if path.name == "__init__.py":
        module_parts = directory_parts
        is_package = True
    else:
        stem = path.stem
        if not stem.isidentifier() or keyword.iskeyword(stem):
            raise FdPinnedScriptsImportError("scripts source module identity is invalid")
        module_parts = (*directory_parts, stem)
        is_package = False
    if not module_parts or module_parts[0] != "scripts":
        raise FdPinnedScriptsImportError("scripts source module identity is invalid")
    digest = entry.sha256
    if (
        not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or digest == "0" * 64
    ):
        raise FdPinnedScriptsImportError("scripts source digest is invalid")
    return _BoundSource(
        module_name=".".join(module_parts),
        relative_path=path,
        sha256=digest,
        is_package=is_package,
    )


def _bind_sources(sources: Iterable[FdPinnedScriptsSource]) -> Mapping[str, _BoundSource]:
    try:
        iterator = iter(sources)
    except TypeError as exc:
        raise FdPinnedScriptsImportError("scripts source map is invalid") from exc
    bound: dict[str, _BoundSource] = {}
    paths: set[PurePosixPath] = set()
    for entry in iterator:
        source = _normalize_source(entry)
        if source.module_name in bound or source.relative_path in paths:
            raise FdPinnedScriptsImportError("scripts source map is ambiguous")
        bound[source.module_name] = source
        paths.add(source.relative_path)
    root = bound.get("scripts")
    if root is None or not root.is_package:
        raise FdPinnedScriptsImportError("scripts source map lacks its package initializer")
    for source in bound.values():
        if source.module_name == "scripts":
            continue
        parent = source.module_name.rpartition(".")[0]
        parent_source = bound.get(parent)
        if parent_source is None or not parent_source.is_package:
            raise FdPinnedScriptsImportError("scripts source map lacks a parent package initializer")
    return MappingProxyType(dict(sorted(bound.items())))


def _source_policy_error(message: str) -> FdPinnedScriptsImportError:
    return FdPinnedScriptsImportError(
        f"scripts source violates the pre-runtime-only policy: {message}"
    )


def _validate_pre_runtime_source_set(sources: Mapping[str, _BoundSource]) -> None:
    paths = frozenset(source.relative_path.as_posix() for source in sources.values())
    unavailable = paths & POST_RUNTIME_UNAVAILABLE_SOURCE_PATHS
    if unavailable:
        raise FdPinnedScriptsImportError(
            "post-runtime scripts are unavailable from the pre-runtime source map"
        )
    if paths != PRE_RUNTIME_SOURCE_PATHS:
        raise FdPinnedScriptsImportError(
            "scripts source map is not the exact pre-runtime-only source set"
        )


def _relative_import_base(source: _BoundSource, node: ast.ImportFrom) -> str:
    module_parts = source.module_name.split(".")
    package_parts = module_parts if source.is_package else module_parts[:-1]
    if node.level > len(package_parts):
        raise _source_policy_error("relative import escapes the scripts package")
    prefix = package_parts[: len(package_parts) - node.level + 1]
    if node.module:
        prefix.extend(node.module.split("."))
    module = ".".join(prefix)
    if not module:
        raise _source_policy_error("relative import has no canonical module")
    return module


def _module_top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _require_allowed_import_module(
    module: str,
    *,
    sources: Mapping[str, _BoundSource],
) -> None:
    if not module or any(not part.isidentifier() for part in module.split(".")):
        raise _source_policy_error("import name is invalid")
    top_level = _module_top_level(module)
    if top_level == "scripts":
        if module not in sources:
            raise _source_policy_error("scripts import is absent from the exact source map")
        return
    if top_level == "builtins" or top_level not in _PRE_RUNTIME_STDLIB_TOP_LEVELS:
        raise _source_policy_error("import is outside the stdlib/scripts allowlist")


def _import_aliases(tree: ast.AST, *, source: _BoundSource) -> Mapping[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                aliases[bound_name] = alias.name if alias.asname else bound_name
        elif isinstance(node, ast.ImportFrom):
            base = _relative_import_base(source, node) if node.level else node.module
            if base is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{base}.{alias.name}"
    return MappingProxyType(aliases)


def _dotted_name(node: ast.AST, aliases: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _is_sys_module(node: ast.AST, aliases: Mapping[str, str]) -> bool:
    return _dotted_name(node, aliases) == "sys"


def _is_protected_sys_collection(node: ast.AST, aliases: Mapping[str, str]) -> bool:
    if isinstance(node, ast.Subscript):
        return _is_protected_sys_collection(node.value, aliases)
    return _dotted_name(node, aliases) in {
        f"sys.{name}" for name in _PROTECTED_SYS_COLLECTIONS
    }


def _is_allowed_verifier_import_module_call(
    source: _BoundSource,
    node: ast.Call,
) -> bool:
    """Allow the verifier's fixed dependency-origin check, and nothing else."""

    return (
        source.relative_path.as_posix() == _VERIFIER_SOURCE_PATH
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "module_name"
    )


def _validate_imports(
    tree: ast.AST,
    *,
    source: _BoundSource,
    sources: Mapping[str, _BoundSource],
) -> Mapping[str, str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _require_allowed_import_module(alias.name, sources=sources)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                raise _source_policy_error("star imports are unavailable")
            base = _relative_import_base(source, node) if node.level else node.module
            if base is None:
                raise _source_policy_error("import-from has no module")
            _require_allowed_import_module(base, sources=sources)
            if base == "scripts" or sources.get(base, None) and sources[base].is_package:
                for alias in node.names:
                    child = f"{base}.{alias.name}"
                    if child not in sources:
                        raise _source_policy_error(
                            "scripts package import is absent from the exact source map"
                        )
            if base in {"importlib", "importlib.util", "importlib.machinery"}:
                if any(alias.name in _FORBIDDEN_IMPORTLIB_SYMBOLS for alias in node.names):
                    raise _source_policy_error("dynamic import loader is unavailable")
            if base == "sys" and any(
                alias.name in _PROTECTED_SYS_COLLECTIONS for alias in node.names
            ):
                raise _source_policy_error("protected sys import state cannot be aliased")
            if base == "builtins":
                raise _source_policy_error("builtins import is unavailable")
    return _import_aliases(tree, source=source)


def _validate_fd_path_literals(tree: ast.AST, *, source: _BoundSource) -> None:
    observed = tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "/proc/self/fd" in node.value
    )
    expected = _ALLOWED_FD_LITERAL_FRAGMENTS[source.relative_path.as_posix()]
    if observed != expected:
        raise _source_policy_error("direct release descriptor path is unavailable")


def _validate_pre_runtime_ast(
    tree: ast.AST,
    *,
    source: _BoundSource,
    sources: Mapping[str, _BoundSource],
) -> None:
    aliases = _validate_imports(tree, source=source, sources=sources)
    allowed_verifier_module_file_uses = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_MAGIC_PATH_NAMES:
            raise _source_policy_error("direct release module path is unavailable")
        if isinstance(node, ast.Attribute):
            dotted = _dotted_name(node, aliases)
            if node.attr == "__file__":
                if (
                    source.relative_path.as_posix() == _VERIFIER_SOURCE_PATH
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "module"
                ):
                    allowed_verifier_module_file_uses += 1
                else:
                    raise _source_policy_error("direct release module path is unavailable")
            elif node.attr in _FORBIDDEN_MAGIC_PATH_ATTRIBUTES:
                raise _source_policy_error("direct release module path is unavailable")
            if dotted in {"sys.__dict__", "sys.modules.__dict__"}:
                raise _source_policy_error("protected sys import state is unavailable")
        if isinstance(node, ast.Call):
            dotted = _dotted_name(node.func, aliases)
            if dotted in _FORBIDDEN_DYNAMIC_CALLS:
                raise _source_policy_error("dynamic loader is unavailable")
            if dotted == "importlib.import_module" and not _is_allowed_verifier_import_module_call(
                source, node
            ):
                raise _source_policy_error("dynamic import is unavailable")
            if dotted in {"setattr", "builtins.setattr", "delattr", "builtins.delattr"}:
                if node.args and _is_sys_module(node.args[0], aliases):
                    raise _source_policy_error("protected sys import state cannot be mutated")
            if dotted in {"getattr", "builtins.getattr", "vars", "builtins.vars"}:
                if node.args and _is_sys_module(node.args[0], aliases):
                    raise _source_policy_error("protected sys import state cannot be accessed dynamically")
            if dotted in {"operator.setitem", "operator.delitem"}:
                if node.args and _is_protected_sys_collection(node.args[0], aliases):
                    raise _source_policy_error("protected sys import state cannot be mutated")
            if isinstance(node.func, ast.Attribute) and _is_protected_sys_collection(
                node.func.value, aliases
            ) and node.func.attr in _MUTATING_COLLECTION_METHODS:
                raise _source_policy_error("protected sys import state cannot be mutated")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(_is_protected_sys_collection(target, aliases) for target in targets):
                raise _source_policy_error("protected sys import state cannot be mutated")
            value = getattr(node, "value", None)
            if value is not None and _is_protected_sys_collection(value, aliases):
                raise _source_policy_error("protected sys import state cannot be aliased")
        if isinstance(node, ast.Delete) and any(
            _is_protected_sys_collection(target, aliases) for target in node.targets
        ):
            raise _source_policy_error("protected sys import state cannot be mutated")
    if allowed_verifier_module_file_uses > 1:
        raise _source_policy_error("direct release module path is unavailable")
    _validate_fd_path_literals(tree, source=source)


def _validate_pre_runtime_source(
    source: _BoundSource,
    raw: bytes,
    *,
    sources: Mapping[str, _BoundSource],
) -> None:
    try:
        decoded = raw.decode("utf-8")
        tree = ast.parse(decoded, filename=source.relative_path.as_posix())
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise _source_policy_error("source is not valid UTF-8 Python") from exc
    _validate_pre_runtime_ast(tree, source=source, sources=sources)


class _FdPinnedScriptsSourceLoader:
    """Compile one digest-bound source through the held release descriptor."""

    def __init__(self, owner: "FdPinnedScriptsModuleMap", source: _BoundSource) -> None:
        self._owner = owner
        self._source = source

    def create_module(self, _spec: object) -> None:
        return None

    def is_package(self, _fullname: str) -> bool:
        return self._source.is_package

    def get_filename(self, _fullname: str) -> str:
        return self._owner._source_filename(self._source)

    def exec_module(self, module: ModuleType) -> None:
        self._owner._execute_source(self, module)


class FdPinnedScriptsModuleMap:
    """Install one canonical, digest-bound importer for ``scripts`` only.

    The constructor duplicates ``release_descriptor`` and owns that duplicate.
    It therefore remains FD-pinned even if the caller closes its original
    descriptor.  The descriptor and every traversed source path are checked
    with no-follow opens before a mapped digest is accepted.
    """

    def __init__(
        self,
        *,
        release_descriptor: int,
        sources: Iterable[FdPinnedScriptsSource],
        expected_uid: int = 0,
        maximum_source_bytes: int = MAX_SOURCE_BYTES,
    ) -> None:
        if type(expected_uid) is not int or expected_uid < 0:
            raise FdPinnedScriptsImportError("scripts release owner is invalid")
        if type(maximum_source_bytes) is not int or maximum_source_bytes < 1:
            raise FdPinnedScriptsImportError("scripts source size bound is invalid")
        self._expected_uid = expected_uid
        self._maximum_source_bytes = maximum_source_bytes
        self._release_descriptor = self._duplicate_descriptor(release_descriptor)
        self._closed = False
        self._installed = False
        try:
            self._release_identity = self._capture_release_identity()
            self._sources = _bind_sources(sources)
            _validate_pre_runtime_source_set(self._sources)
            self._validate_pre_runtime_sources()
        except Exception:
            self._close_descriptor()
            raise

    @property
    def installed(self) -> bool:
        return self._installed and not self._closed

    def __enter__(self) -> "FdPinnedScriptsModuleMap":
        return self.install()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def install(self) -> "FdPinnedScriptsModuleMap":
        self._require_open()
        if self._installed:
            raise FdPinnedScriptsImportError("scripts source map is already installed")
        if any(_belongs_to_scripts(name) for name in sys.modules):
            raise FdPinnedScriptsImportError("scripts namespace is already preloaded")
        if any(finder is self for finder in sys.meta_path):
            raise FdPinnedScriptsImportError("scripts source finder is already present")
        sys.meta_path.insert(0, self)
        self._installed = True
        return self

    def close(self) -> None:
        """Remove the finder and every ``scripts`` module loaded in this session."""

        if self._closed:
            return
        self._remove_finder()
        if self._installed:
            self._purge_scripts_namespace()
        self._close_descriptor()
        self._closed = True

    def import_module(self, module_name: str) -> ModuleType:
        """Import one mapped canonical name and validate all loaded provenance."""

        self._require_installed()
        if module_name not in self._sources:
            self._abort()
            raise FdPinnedScriptsImportError("requested scripts module is absent from the source map")
        try:
            module = importlib.import_module(module_name)
            self.assert_loaded_provenance()
            return module
        except BaseException:
            self._abort()
            raise

    def assert_loaded_provenance(self) -> None:
        """Require every loaded ``scripts`` module to use this exact map loader."""

        self._require_installed()
        try:
            self._assert_finder_position()
            for name, module in tuple(sys.modules.items()):
                if not _belongs_to_scripts(name):
                    continue
                source = self._sources.get(name)
                spec = getattr(module, "__spec__", None)
                loader = getattr(spec, "loader", None)
                if (
                    source is None
                    or not isinstance(loader, _FdPinnedScriptsSourceLoader)
                    or loader._owner is not self
                    or loader._source != source
                ):
                    raise FdPinnedScriptsImportError(
                        "scripts module escaped the exact FD-pinned source map"
                    )
                self._assert_module_identity(loader, module)
        except BaseException:
            self._abort()
            raise

    def find_spec(
        self,
        fullname: str,
        _path: object = None,
        _target: object = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del _path, _target
        source = self._sources.get(fullname)
        if source is not None:
            try:
                self._require_installed()
                self._assert_finder_position()
                self._assert_release_identity()
                loader = _FdPinnedScriptsSourceLoader(self, source)
                spec = importlib.machinery.ModuleSpec(
                    fullname,
                    loader,
                    is_package=source.is_package,
                )
                # Keep package child lookup entirely in this finder.  In
                # particular, do not expose a release-backed path to PathFinder.
                if source.is_package:
                    spec.submodule_search_locations = []
                spec.origin = self._source_filename(source)
                return spec
            except BaseException:
                self._abort()
                raise
        if _belongs_to_scripts(fullname):
            self._abort()
            raise ModuleNotFoundError(
                "scripts import is absent from the exact FD-pinned source map",
                name=fullname,
            )
        return None

    def _execute_source(
        self,
        loader: _FdPinnedScriptsSourceLoader,
        module: ModuleType,
    ) -> None:
        source = loader._source
        try:
            self._require_installed()
            raw = self._read_source(source)
            if hashlib.sha256(raw).hexdigest() != source.sha256:
                raise FdPinnedScriptsImportError("scripts source digest differs from the exact source map")
            _validate_pre_runtime_source(source, raw, sources=self._sources)
            filename = self._source_filename(source)
            try:
                code = compile(raw, filename, "exec", dont_inherit=True)
            except (SyntaxError, TypeError, ValueError) as exc:
                raise FdPinnedScriptsImportError("scripts source cannot be compiled") from exc
            module.__file__ = filename
            module.__cached__ = None
            exec(code, module.__dict__)
            self._assert_finder_position()
            if sys.modules.get(source.module_name) is not module:
                raise FdPinnedScriptsImportError("scripts source replaced its canonical module state")
            self._assert_module_identity(loader, module)
        except BaseException:
            self._abort()
            raise

    def _validate_pre_runtime_sources(self) -> None:
        """Admit every mapped source before any one of them can execute."""

        for source in self._sources.values():
            raw = self._read_source(source)
            if hashlib.sha256(raw).hexdigest() != source.sha256:
                raise FdPinnedScriptsImportError(
                    "scripts source digest differs from the exact source map"
                )
            _validate_pre_runtime_source(source, raw, sources=self._sources)

    def _assert_module_identity(
        self,
        loader: _FdPinnedScriptsSourceLoader,
        module: ModuleType,
    ) -> None:
        source = loader._source
        expected_package = (
            source.module_name
            if source.is_package
            else source.module_name.rpartition(".")[0]
        )
        spec = getattr(module, "__spec__", None)
        if (
            getattr(module, "__name__", None) != source.module_name
            or getattr(module, "__package__", None) != expected_package
            or getattr(module, "__loader__", None) is not loader
            or getattr(spec, "name", None) != source.module_name
            or getattr(spec, "loader", None) is not loader
            or getattr(module, "__file__", None) != self._source_filename(source)
            or getattr(module, "__cached__", None) is not None
            or (
                source.is_package
                and (
                    getattr(module, "__path__", None) != []
                    or getattr(spec, "submodule_search_locations", None) != []
                )
            )
        ):
            raise FdPinnedScriptsImportError("scripts source changed its canonical module identity")

    def _read_source(self, source: _BoundSource) -> bytes:
        self._assert_release_identity()
        directory_descriptor = -1
        file_descriptor = -1
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            directory_descriptor = os.dup(self._release_descriptor)
            self._assert_controlled_directory(
                directory_descriptor,
                label="scripts release directory",
            )
            for part in source.relative_path.parts[:-1]:
                try:
                    child = os.open(part, directory_flags, dir_fd=directory_descriptor)
                except OSError as exc:
                    raise FdPinnedScriptsImportError(
                        "scripts source directory is unavailable"
                    ) from exc
                os.close(directory_descriptor)
                directory_descriptor = child
                self._assert_controlled_directory(
                    directory_descriptor,
                    label="scripts source directory",
                )
            try:
                file_descriptor = os.open(
                    source.relative_path.parts[-1],
                    file_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise FdPinnedScriptsImportError("scripts source is unavailable") from exc
            before = self._assert_controlled_regular(
                file_descriptor,
                label="scripts source",
            )
            if before.st_size > self._maximum_source_bytes:
                raise FdPinnedScriptsImportError("scripts source exceeds its size bound")
            payload = bytearray()
            while len(payload) <= self._maximum_source_bytes:
                try:
                    block = os.read(
                        file_descriptor,
                        min(64 * 1024, self._maximum_source_bytes + 1 - len(payload)),
                    )
                except OSError as exc:
                    raise FdPinnedScriptsImportError("scripts source cannot be read") from exc
                if not block:
                    break
                payload.extend(block)
            if len(payload) > self._maximum_source_bytes:
                raise FdPinnedScriptsImportError("scripts source exceeds its size bound")
            after = self._assert_controlled_regular(
                file_descriptor,
                label="scripts source",
            )
            if _descriptor_identity(before, descriptor=file_descriptor) != _descriptor_identity(
                after,
                descriptor=file_descriptor,
            ):
                raise FdPinnedScriptsImportError("scripts source changed while being read")
            self._assert_release_identity()
            return bytes(payload)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            if directory_descriptor >= 0:
                os.close(directory_descriptor)

    def _source_filename(self, source: _BoundSource) -> str:
        self._require_open()
        return f"/proc/self/fd/{self._release_descriptor}/{source.relative_path.as_posix()}"

    def _capture_release_identity(self) -> _DescriptorIdentity:
        metadata = self._assert_controlled_directory(
            self._release_descriptor,
            label="scripts release directory",
        )
        return _descriptor_identity(metadata, descriptor=self._release_descriptor)

    def _assert_release_identity(self) -> None:
        self._require_open()
        metadata = self._assert_controlled_directory(
            self._release_descriptor,
            label="scripts release directory",
        )
        if _descriptor_identity(metadata, descriptor=self._release_descriptor) != self._release_identity:
            raise FdPinnedScriptsImportError("scripts release descriptor changed")

    def _assert_controlled_directory(self, descriptor: int, *, label: str) -> os.stat_result:
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise FdPinnedScriptsImportError(f"{label} is unavailable") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or metadata.st_gid != self._expected_uid
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise FdPinnedScriptsImportError(f"{label} is not root-controlled")
        return metadata

    def _assert_controlled_regular(self, descriptor: int, *, label: str) -> os.stat_result:
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise FdPinnedScriptsImportError(f"{label} is unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self._expected_uid
            or metadata.st_gid != self._expected_uid
            or metadata.st_nlink != 1
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise FdPinnedScriptsImportError(f"{label} is not root-controlled")
        return metadata

    def _duplicate_descriptor(self, descriptor: int) -> int:
        if type(descriptor) is not int or descriptor < 3:
            raise FdPinnedScriptsImportError("scripts release descriptor is invalid")
        try:
            duplicate = os.dup(descriptor)
            os.set_inheritable(duplicate, False)
            return duplicate
        except OSError as exc:
            raise FdPinnedScriptsImportError("scripts release descriptor cannot be retained") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise FdPinnedScriptsImportError("scripts source map is closed")

    def _require_installed(self) -> None:
        self._require_open()
        if not self._installed:
            raise FdPinnedScriptsImportError("scripts source map is not installed")

    def _assert_finder_position(self) -> None:
        if not sys.meta_path or sys.meta_path[0] is not self:
            raise FdPinnedScriptsImportError("scripts source finder state changed")

    def _remove_finder(self) -> None:
        while True:
            try:
                sys.meta_path.remove(self)
            except ValueError:
                return

    def _purge_scripts_namespace(self) -> None:
        # Installation rejects every preloaded ``scripts`` name.  On teardown
        # every remaining one therefore came from this importer session.
        for name in tuple(sys.modules):
            if _belongs_to_scripts(name):
                sys.modules.pop(name, None)

    def _close_descriptor(self) -> None:
        if self._release_descriptor < 0:
            return
        try:
            os.close(self._release_descriptor)
        except OSError:
            pass
        self._release_descriptor = -1

    def _abort(self) -> None:
        if self._closed:
            return
        self._remove_finder()
        if self._installed:
            self._purge_scripts_namespace()
        self._close_descriptor()
        self._closed = True
