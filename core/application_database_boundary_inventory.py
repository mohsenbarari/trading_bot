"""Static inventory of database-construction boundaries.

This module is intentionally a *test/audit seam*, not a runtime registry.  It
does not import SQLAlchemy, does not open a connection, and is never consulted
by the application while serving traffic.  Its only job is to make a new
engine, session factory, or raw PostgreSQL connection constructor visible in
code review before it can silently sit outside ``core.db``'s explicitly
registered writer-term and transaction-envelope engine hooks.

The inventory covers the complete checked-in Python tree except tests and
generated/virtual-environment directories.  Existing exceptional boundaries
are deliberately named rather than hidden:

* ``core/db.py`` owns the canonical application engine and session factory;
* migration and explicitly guarded scratch tools remain separate operational
  control planes;
* a small number of manual maintenance tools are visible as unguarded writer
  entry points; and
* the historical ``src.infrastructure.database.connection`` path is retained
  only as an inert, fail-closed compatibility module with an import fence.

Adding a constructor requires adding a reviewed registry entry and, if it is
not the canonical ``core.db`` engine, an equally explicit control strategy.
That deliberate friction narrows the known non-global-engine-hook gap without
changing any live runtime behaviour.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


APPLICATION_DATABASE_BOUNDARY_INVENTORY_SCHEMA = "application_database_boundary_inventory/v1"
CANONICAL_APPLICATION_DATABASE_MODULE = "core/db.py"
RETIRED_LEGACY_DATABASE_MODULE = "src/infrastructure/database/connection.py"

# These are directory *parts*, not glob patterns.  A source file elsewhere in
# the repository is deliberately included, including operator scripts.
_EXCLUDED_SOURCE_DIRECTORY_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "tests",
        "venv",
    }
)

# The keys are fully qualified names resolved from imports and straightforward
# local aliases.  The values are stable audit labels; they intentionally do
# not expose an implementation-specific import spelling in the registry.
_CONSTRUCTORS_BY_REFERENCE = {
    "sqlalchemy.create_engine": "sqlalchemy.create_engine",
    "sqlalchemy.engine_from_config": "sqlalchemy.engine_from_config",
    "sqlalchemy.engine.create_engine": "sqlalchemy.create_engine",
    "sqlalchemy.ext.asyncio.create_async_engine": "sqlalchemy.create_async_engine",
    "sqlalchemy.ext.asyncio.create_async_pool_from_url": "sqlalchemy.create_async_pool_from_url",
    "sqlalchemy.ext.asyncio.async_sessionmaker": "sqlalchemy.async_sessionmaker",
    "sqlalchemy.ext.asyncio.async_scoped_session": "sqlalchemy.async_scoped_session",
    "sqlalchemy.ext.asyncio.AsyncSession": "sqlalchemy.AsyncSession",
    "sqlalchemy.orm.sessionmaker": "sqlalchemy.sessionmaker",
    "sqlalchemy.orm.scoped_session": "sqlalchemy.scoped_session",
    "sqlalchemy.orm.Session": "sqlalchemy.Session",
    "psycopg2.connect": "dbapi.psycopg2.connect",
    "psycopg.connect": "dbapi.psycopg.connect",
    "psycopg_pool.ConnectionPool": "dbapi.psycopg_pool.ConnectionPool",
    "psycopg_pool.AsyncConnectionPool": "dbapi.psycopg_pool.AsyncConnectionPool",
    "asyncpg.connect": "dbapi.asyncpg.connect",
    "asyncpg.create_pool": "dbapi.asyncpg.create_pool",
    "aiopg.connect": "dbapi.aiopg.connect",
    "aiopg.create_pool": "dbapi.aiopg.create_pool",
    "pg8000.connect": "dbapi.pg8000.connect",
    "sqlite3.connect": "dbapi.sqlite3.connect",
}

_DATABASE_IMPORT_PREFIXES = (
    "aiopg",
    "asyncpg",
    "pg8000",
    "psycopg",
    "psycopg2",
    "psycopg_pool",
    "sqlalchemy",
    "sqlite3",
)


class ApplicationDatabaseBoundaryInventoryError(ValueError):
    """A stable static-audit refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ApplicationDatabaseBoundaryInventoryError(code)


@dataclass(frozen=True, slots=True)
class ApplicationDatabaseBoundary:
    """One statically discovered database construction call site."""

    relative_path: str
    scope: str
    constructor: str
    occurrence: int
    line: int

    @property
    def identity(self) -> tuple[str, str, str, int]:
        """Stable identity independent of unrelated line-number movement."""

        return (self.relative_path, self.scope, self.constructor, self.occurrence)

    def display(self) -> str:
        return (
            f"{self.relative_path}:{self.line}:{self.scope}:"
            f"{self.constructor}#{self.occurrence}"
        )


@dataclass(frozen=True, slots=True)
class RegisteredApplicationDatabaseBoundary:
    """A reviewed static classification for one construction call site."""

    relative_path: str
    scope: str
    constructor: str
    occurrence: int
    classification: str
    control_strategy: str

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (self.relative_path, self.scope, self.constructor, self.occurrence)


@dataclass(frozen=True, slots=True)
class StaticSourceSafetyContract:
    """Required source anchors for a reviewed exception path."""

    relative_path: str
    required_fragments: frozenset[str]


# The registry is intentionally literal.  Do not replace it with a path glob
# or a broad "scripts are exempt" rule: management and scratch tools can write
# to PostgreSQL just as effectively as an API route can.
REGISTERED_APPLICATION_DATABASE_BOUNDARIES = (
    RegisteredApplicationDatabaseBoundary(
        "alembic/env.py",
        "run_migrations_online",
        "sqlalchemy.engine_from_config",
        1,
        "migration-control-plane",
        "Alembic-only schema migration path; not an application request engine.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "core/db.py",
        "<module>",
        "sqlalchemy.create_async_engine",
        1,
        "canonical-application-engine",
        "Registers core.db Session hooks and the engine before_cursor_execute guard.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "core/db.py",
        "<module>",
        "sqlalchemy.async_sessionmaker",
        1,
        "canonical-application-session-factory",
        "Binds AsyncSessionLocal to the one canonical guarded application engine.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "core/metrics.py",
        "MetricsRegistry._connect",
        "dbapi.sqlite3.connect",
        1,
        "local-metrics-store",
        "SQLite metrics state only; it is not a PostgreSQL application-write path.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "core/trading_settings.py",
        "_get_sync_engine",
        "sqlalchemy.create_engine",
        1,
        "read-only-settings-exception",
        "The only caller is statically locked to literal SELECT execution.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "migrations/env.py",
        "run_migrations_online",
        "sqlalchemy.engine_from_config",
        1,
        "migration-control-plane",
        "Alembic-only schema migration path; not an application request engine.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/align_change_log_source_sequence.py",
        "align_change_log_sequence",
        "dbapi.psycopg2.connect",
        1,
        "manual-maintenance-writer",
        "Explicit sequence-realignment operator tool; never classified as a guarded app session.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/align_change_log_source_sequence.py",
        "read_source_watermark_floor",
        "dbapi.psycopg2.connect",
        1,
        "manual-maintenance-reader",
        "Explicit operator watermark inspection path.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/align_trade_number_sequence.py",
        "align_trade_number_sequence",
        "dbapi.psycopg2.connect",
        1,
        "manual-maintenance-writer",
        "Explicit sequence-realignment operator tool; never classified as a guarded app session.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/repair_registry_fingerprint_quarantine.py",
        "inspect_or_repair",
        "dbapi.psycopg2.connect",
        1,
        "manual-maintenance-writer",
        "Explicit confirmed quarantine repair tool; must remain separately reviewed.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/report_postgres_runtime.py",
        "collect_report",
        "dbapi.psycopg2.connect",
        1,
        "read-only-operator-report",
        "Read-only PostgreSQL tuning report; no application write authority.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/run_guarded_scratch_alembic.py",
        "verify_connected_database",
        "sqlalchemy.create_engine",
        1,
        "scratch-only-controlled",
        "Requires exact scratch target, guarded checkout, and preflight-connected database proof.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/run_market_postgres_gate.py",
        "_connect",
        "dbapi.psycopg2.connect",
        1,
        "scratch-only-controlled",
        "Localhost-only disposable market test control plane with mandatory cleanup.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/run_registration_scratch_suite.py",
        "_admin_engine",
        "sqlalchemy.create_engine",
        1,
        "scratch-only-controlled",
        "Explicit CI/test opt-in, guarded migration path, and runtime before/after proof.",
    ),
    RegisteredApplicationDatabaseBoundary(
        "scripts/run_registration_scratch_suite.py",
        "_database_snapshot",
        "sqlalchemy.create_engine",
        1,
        "scratch-only-controlled",
        "Read-only runtime snapshot used to prove the scratch suite did not mutate its runtime target.",
    ),
)


# A scratch constructor is accepted only if its path remains tied to all of
# these concrete safety anchors.  General scripts are deliberately *not*
# granted this classification.
SCRATCH_ONLY_STATIC_SAFETY_CONTRACTS = (
    StaticSourceSafetyContract(
        "scripts/run_guarded_scratch_alembic.py",
        frozenset(
            {
                "TRADING_BOT_MIGRATION_MODE",
                "SCRATCH_DATABASE_PATTERN",
                "DENIED_DATABASE_NAMES",
                "validate_scratch_database_urls(",
                "validate_checkout(",
                "verify_connected_database(target)",
            }
        ),
    ),
    StaticSourceSafetyContract(
        "scripts/run_market_postgres_gate.py",
        frozenset(
            {
                "LOCAL_HOSTS",
                "ADMIN_DATABASE",
                "_validate_admin_url",
                "refusing pre-existing scratch database",
                "_drop_scratch_databases",
                "scratch cleanup incomplete",
            }
        ),
    ),
    StaticSourceSafetyContract(
        "scripts/run_registration_scratch_suite.py",
        frozenset(
            {
                "STAGE9_SCRATCH_DATABASES_ALLOWED",
                "TRADING_BOT_EXPECTED_CHECKOUT",
                "RUNTIME_DATABASE_DENYLIST",
                "TRADING_BOT_MIGRATION_MODE\": \"scratch\"",
                "generated scratch database already exists",
                "after != before",
            }
        ),
    ),
)


def _is_database_import(reference: str) -> bool:
    return reference.startswith(_DATABASE_IMPORT_PREFIXES)


def _scope_label(scope: tuple[str, ...]) -> str:
    return ".".join(scope) if scope else "<module>"


class _DatabaseConstructorVisitor(ast.NodeVisitor):
    """Resolve imports/straight aliases without importing the target module."""

    def __init__(self, *, scope: tuple[str, ...] = (), aliases: dict[str, str] | None = None) -> None:
        self._scope = scope
        self._aliases = dict(aliases or {})
        self.calls: list[tuple[str, str, int, int]] = []
        self.star_imports: list[tuple[str, int]] = []
        self.dynamic_database_accesses: list[tuple[str, int]] = []

    def _spawn(self, name: str) -> "_DatabaseConstructorVisitor":
        return _DatabaseConstructorVisitor(scope=(*self._scope, name), aliases=self._aliases)

    def _merge(self, child: "_DatabaseConstructorVisitor") -> None:
        self.calls.extend(child.calls)
        self.star_imports.extend(child.star_imports)
        self.dynamic_database_accesses.extend(child.dynamic_database_accesses)

    def _visit_block(self, statements: Iterable[ast.stmt]) -> None:
        for statement in statements:
            self.visit(statement)

    def _resolve_reference(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self._aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve_reference(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Subscript):
            return self._resolve_reference(node.value)
        return None

    def _bind_assignment_target(self, target: ast.AST, reference: str | None) -> None:
        if reference is None:
            return
        if isinstance(target, ast.Name):
            self._aliases[target.id] = reference
        elif isinstance(target, (ast.Tuple, ast.List)):
            # Tuple unpacking cannot preserve a trustworthy callable identity.
            # Do not guess; a later direct constructor call will still be seen.
            return

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802 - ast API spelling
        self._visit_block(node.body)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802 - ast API spelling
        for imported in node.names:
            if not _is_database_import(imported.name):
                continue
            if imported.asname:
                self._aliases[imported.asname] = imported.name
            else:
                # ``import sqlalchemy.ext.asyncio`` binds ``sqlalchemy``.
                self._aliases[imported.name.split(".", 1)[0]] = imported.name.split(".", 1)[0]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802 - ast API spelling
        module = node.module or ""
        if not _is_database_import(module):
            return
        for imported in node.names:
            if imported.name == "*":
                self.star_imports.append((module, node.lineno))
                continue
            local_name = imported.asname or imported.name
            self._aliases[local_name] = f"{module}.{imported.name}"

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast API spelling
        self.visit(node.value)
        reference = self._resolve_reference(node.value)
        for target in node.targets:
            self._bind_assignment_target(target, reference)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast API spelling
        if node.value is None:
            return
        self.visit(node.value)
        self._bind_assignment_target(node.target, self._resolve_reference(node.value))

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802 - ast API spelling
        self.visit(node.value)
        self._bind_assignment_target(node.target, self._resolve_reference(node.value))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API spelling
        # A dynamic lookup hides the exact factory from the literal inventory,
        # so fail it closed instead of pretending it is safe.  Straight local
        # aliases are resolved above and remain auditable.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            base = self._resolve_reference(node.args[0])
            candidate = f"{base}.{node.args[1].value}" if base else ""
            if candidate in _CONSTRUCTORS_BY_REFERENCE:
                self.dynamic_database_accesses.append((candidate, node.lineno))
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"__import__", "import_module"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and _is_database_import(node.args[0].value)
        ):
            self.dynamic_database_accesses.append((node.args[0].value, node.lineno))
        reference = self._resolve_reference(node.func)
        constructor = _CONSTRUCTORS_BY_REFERENCE.get(reference or "")
        if constructor is not None:
            self.calls.append((_scope_label(self._scope), constructor, node.lineno, node.col_offset))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API spelling
        # Decorators/defaults execute in the containing scope, while the body
        # gets an independent local alias table that inherits imports seen so
        # far.  That is enough to model normal import aliases without trying to
        # emulate Python execution.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)):
            self.visit(default)
        child = self._spawn(node.name)
        child._visit_block(node.body)
        self._merge(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast API spelling
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast API spelling
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        child = self._spawn(node.name)
        child._visit_block(node.body)
        self._merge(child)


def _source_paths(source_root: Path) -> tuple[Path, ...]:
    root = Path(source_root)
    if not root.is_dir():
        _fail("application_database_boundary_source_root_missing")
    paths: list[Path] = []
    for candidate in root.rglob("*.py"):
        relative_parts = candidate.relative_to(root).parts
        if any(part in _EXCLUDED_SOURCE_DIRECTORY_PARTS for part in relative_parts):
            continue
        paths.append(candidate)
    return tuple(sorted(paths))


def _parse_source(path: Path, *, source_root: Path) -> ast.Module:
    relative_path = path.relative_to(source_root).as_posix()
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    except (OSError, SyntaxError, UnicodeError) as exc:
        _fail(f"application_database_boundary_source_unreadable:{relative_path}:{type(exc).__name__}")


def discover_application_database_boundaries(source_root: Path) -> tuple[ApplicationDatabaseBoundary, ...]:
    """Return every statically resolvable database constructor in source.

    This parses source only.  It intentionally does not import modules or
    resolve settings, so invoking the audit cannot create a network connection
    or activate a policy.
    """

    root = Path(source_root)
    raw: list[tuple[str, str, str, int, int]] = []
    for path in _source_paths(root):
        visitor = _DatabaseConstructorVisitor()
        tree = _parse_source(path, source_root=root)
        visitor.visit(tree)
        relative_path = path.relative_to(root).as_posix()
        if visitor.star_imports:
            modules = ",".join(f"{module}@{line}" for module, line in visitor.star_imports)
            _fail(f"application_database_boundary_star_import:{relative_path}:{modules}")
        if visitor.dynamic_database_accesses:
            accesses = ",".join(
                f"{reference}@{line}" for reference, line in visitor.dynamic_database_accesses
            )
            _fail(f"application_database_boundary_dynamic_access:{relative_path}:{accesses}")
        raw.extend(
            (relative_path, scope, constructor, line, column)
            for scope, constructor, line, column in visitor.calls
        )

    occurrences: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    discovered: list[ApplicationDatabaseBoundary] = []
    for relative_path, scope, constructor, line, _column in sorted(raw):
        key = (relative_path, scope, constructor)
        occurrences[key] += 1
        discovered.append(
            ApplicationDatabaseBoundary(
                relative_path=relative_path,
                scope=scope,
                constructor=constructor,
                occurrence=occurrences[key],
                line=line,
            )
        )
    return tuple(discovered)


def _registered_by_identity() -> dict[tuple[str, str, str, int], RegisteredApplicationDatabaseBoundary]:
    registered: dict[tuple[str, str, str, int], RegisteredApplicationDatabaseBoundary] = {}
    for entry in REGISTERED_APPLICATION_DATABASE_BOUNDARIES:
        if entry.identity in registered:
            _fail(f"application_database_boundary_duplicate_registry:{entry.identity!r}")
        registered[entry.identity] = entry
    return registered


def assert_application_database_boundary_inventory(
    discovered: Iterable[ApplicationDatabaseBoundary],
) -> None:
    """Refuse an unreviewed constructor or a stale literal registry entry."""

    registered = _registered_by_identity()
    observed: dict[tuple[str, str, str, int], ApplicationDatabaseBoundary] = {}
    for boundary in discovered:
        if boundary.identity in observed:
            _fail(f"application_database_boundary_duplicate_observation:{boundary.display()}")
        observed[boundary.identity] = boundary

    unexpected = tuple(sorted(set(observed) - set(registered)))
    missing = tuple(sorted(set(registered) - set(observed)))
    if unexpected or missing:
        details: list[str] = []
        if unexpected:
            details.append(
                "unregistered=" + ",".join(observed[identity].display() for identity in unexpected)
            )
        if missing:
            details.append("stale=" + ",".join(repr(identity) for identity in missing))
        _fail("application_database_boundary_inventory_mismatch:" + ";".join(details))


def _find_top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    _fail(f"application_database_boundary_required_function_missing:{name}")


def _literal_select_sql(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "text":
        return None
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return None
    return node.args[0].value.lstrip().upper()


def assert_trading_settings_sync_engine_read_only(source_root: Path) -> None:
    """Keep the one non-``core.db`` SQLAlchemy app engine read-only.

    ``_get_sync_engine`` is a historical synchronous cache helper.  Its exact
    callers and SQL are therefore constrained rather than treated as a normal
    app write engine.  Any dynamic SQL, non-SELECT execution, or a second
    caller fails the audit and must be reviewed as a new boundary.
    """

    root = Path(source_root)
    relative_path = "core/trading_settings.py"
    tree = _parse_source(root / relative_path, source_root=root)
    load_function = _find_top_level_function(tree, "_load_from_db_sync")

    load_function_node_ids = {id(node) for node in ast.walk(load_function)}
    callers: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_get_sync_engine":
            callers.append(
                ("_load_from_db_sync" if id(node) in load_function_node_ids else "other", node.lineno)
            )
    if not callers or any(scope != "_load_from_db_sync" for scope, _line in callers) or len(callers) != 1:
        _fail("application_database_boundary_trading_settings_engine_callers_changed")

    execute_calls = [
        node
        for node in ast.walk(load_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    ]
    if len(execute_calls) != 1:
        _fail("application_database_boundary_trading_settings_execute_surface_changed")
    sql = _literal_select_sql(execute_calls[0].args[0]) if execute_calls[0].args else None
    if sql is None or not sql.startswith("SELECT "):
        _fail("application_database_boundary_trading_settings_non_select_sql")


def _absolute_import_from_reference(node: ast.ImportFrom, *, relative_path: str) -> str:
    """Resolve an ``ImportFrom`` module just far enough for the legacy fence."""

    module_parts = tuple(part for part in (node.module or "").split(".") if part)
    if node.level == 0:
        return ".".join(module_parts)
    source_parts = Path(relative_path).with_suffix("").parts
    package_parts = source_parts[:-1]
    retained = len(package_parts) - (node.level - 1)
    if retained < 0:
        return ""
    return ".".join((*package_parts[:retained], *module_parts))


def _is_legacy_connection_import(node: ast.AST, *, relative_path: str) -> bool:
    if isinstance(node, ast.ImportFrom):
        module = _absolute_import_from_reference(node, relative_path=relative_path)
        if module == "src.infrastructure.database.connection":
            return True
        return module == "src.infrastructure.database" and any(
            alias.name == "connection" for alias in node.names
        )
    if isinstance(node, ast.Import):
        return any(alias.name == "src.infrastructure.database.connection" for alias in node.names)
    if (
        isinstance(node, ast.Call)
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "src.infrastructure.database.connection"
    ):
        if isinstance(node.func, ast.Name):
            return node.func.id in {"__import__", "import_module"}
        return isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
    return False


def assert_legacy_connection_module_is_unwired(source_root: Path) -> None:
    """Refuse application-source use of the retired, fail-closed module.

    This is deliberately stricter than merely recording a lack of current
    importers.  The legacy file fails closed even for importlib/manual calls,
    and any direct import spelling used by application source is additionally
    a release-audit failure until the caller is migrated to ``core.db`` or a
    separately reviewed boundary.
    """

    root = Path(source_root)
    importers: list[str] = []
    for path in _source_paths(root):
        relative_path = path.relative_to(root).as_posix()
        if relative_path == RETIRED_LEGACY_DATABASE_MODULE:
            continue
        tree = _parse_source(path, source_root=root)
        if any(
            _is_legacy_connection_import(node, relative_path=relative_path) for node in ast.walk(tree)
        ):
            importers.append(relative_path)
    if importers:
        _fail("application_database_boundary_legacy_connection_imported:" + ",".join(sorted(importers)))


def assert_scratch_only_static_safety_contracts(source_root: Path) -> None:
    """Require concrete safety anchors for every scratch-only constructor."""

    root = Path(source_root)
    contracts = {contract.relative_path: contract for contract in SCRATCH_ONLY_STATIC_SAFETY_CONTRACTS}
    scratch_paths = {
        entry.relative_path
        for entry in REGISTERED_APPLICATION_DATABASE_BOUNDARIES
        if entry.classification == "scratch-only-controlled"
    }
    if scratch_paths != set(contracts):
        _fail("application_database_boundary_scratch_contract_coverage_mismatch")
    for relative_path, contract in contracts.items():
        try:
            source = (root / relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            _fail(
                f"application_database_boundary_scratch_contract_unreadable:{relative_path}:{type(exc).__name__}"
            )
        missing = sorted(fragment for fragment in contract.required_fragments if fragment not in source)
        if missing:
            _fail(
                "application_database_boundary_scratch_contract_missing:"
                + relative_path
                + ":"
                + ",".join(missing)
            )


def assert_core_db_guard_registration_contract(source_root: Path) -> None:
    """Verify the canonical engine remains paired with both guard installers."""

    root = Path(source_root)
    tree = _parse_source(root / CANONICAL_APPLICATION_DATABASE_MODULE, source_root=root)
    calls: set[str] = set()
    engine_guard_calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "register_application_writer_term_guards":
            calls.add(node.func.id)
        elif node.func.id == "register_application_writer_term_engine_guard":
            engine_guard_calls.append(node)
    if "register_application_writer_term_guards" not in calls:
        _fail("application_database_boundary_core_db_session_guard_missing")
    if not any(
        len(call.args) == 1
        and isinstance(call.args[0], ast.Attribute)
        and call.args[0].attr == "sync_engine"
        and isinstance(call.args[0].value, ast.Name)
        and call.args[0].value.id == "engine"
        for call in engine_guard_calls
    ):
        _fail("application_database_boundary_core_db_engine_guard_missing")


def run_application_database_boundary_audit(source_root: Path) -> tuple[ApplicationDatabaseBoundary, ...]:
    """Run the complete default-off source audit and return its inventory."""

    discovered = discover_application_database_boundaries(source_root)
    assert_application_database_boundary_inventory(discovered)
    assert_core_db_guard_registration_contract(source_root)
    assert_trading_settings_sync_engine_read_only(source_root)
    assert_legacy_connection_module_is_unwired(source_root)
    assert_scratch_only_static_safety_contracts(source_root)
    return discovered
