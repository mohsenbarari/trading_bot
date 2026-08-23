"""Single source of truth for the current Alembic head."""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


CANONICAL_SCHEMA_HEAD = "ff6c7d8e9f01"
CANONICAL_SCHEMA_PARENT = "a496c8d0e1f2"
REPO_ROOT = Path(__file__).resolve().parents[1]


def alembic_script_directory(repo_root: Path = REPO_ROOT) -> ScriptDirectory:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    return ScriptDirectory.from_config(config)


def current_schema_heads(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    return tuple(alembic_script_directory(repo_root).get_heads())


def assert_canonical_schema_head(repo_root: Path = REPO_ROOT) -> str:
    heads = current_schema_heads(repo_root)
    if heads != (CANONICAL_SCHEMA_HEAD,):
        raise RuntimeError("alembic_canonical_head_mismatch")
    script = alembic_script_directory(repo_root)
    revision = script.get_revision(CANONICAL_SCHEMA_HEAD)
    if str(revision.down_revision) != CANONICAL_SCHEMA_PARENT:
        raise RuntimeError("alembic_canonical_parent_mismatch")
    return CANONICAL_SCHEMA_HEAD
