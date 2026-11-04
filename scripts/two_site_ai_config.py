"""Public role policy and local, credential-free CLI identity configuration."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat

AI_CONFIG_REL = Path("docs/refactor/two-server/templates/automation-models.json")
AI_RUNTIME_REL = Path(".local/two-site-refactor/ai-runtime.json")
ROLES = ("coordinator", "final_reviewer", "worker", "reviewer")
_PROVIDERS = {"coordinator": "qoder", "final_reviewer": "qoder",
              "worker": "antigravity", "reviewer": "antigravity"}
_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_MAX_BYTES = 65536
_QODER_OUTPUT_MIN = 32768
_QODER_OUTPUT_MAX = 1048576


class AIConfigError(ValueError):
    """Configuration is missing, unsafe or does not match the closed schema."""


def _need(ok: object, code: str) -> None:
    if not ok:
        raise AIConfigError(code)


def _pairs(items: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in items:
        _need(key not in value, "AI_CONFIG_DUPLICATE_KEY")
        value[key] = item
    return value


def _read(root: Path, relative: Path, *, private: bool = False) -> dict:
    descriptor = None
    try:
        root = Path(root).resolve(strict=True)
        _need(root.is_dir(), "AI_CONFIG_ROOT")
        path = root / relative
        for parent in relative.parents:
            info = (root / parent).lstat()
            _need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "AI_CONFIG_PARENT")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
              and before.st_nlink == 1 and not before.st_mode & 0o022
              and 0 < before.st_size <= _MAX_BYTES, "AI_CONFIG_CUSTODY")
        if private:
            _need(stat.S_IMODE(before.st_mode) == 0o600, "AI_RUNTIME_MODE")
        raw = os.read(descriptor, _MAX_BYTES + 1)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        _need(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k) for k in fields)
              and all(getattr(after, k) == getattr(path.lstat(), k) for k in fields), "AI_CONFIG_CHANGED")
        def nonfinite(_value):
            raise AIConfigError("AI_CONFIG_NONFINITE")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=nonfinite)
        _need(type(value) is dict, "AI_CONFIG_SHAPE")
        return value
    except AIConfigError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise AIConfigError("AI_CONFIG_UNREADABLE") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_ai_config(root: Path) -> dict:
    value = _read(root, AI_CONFIG_REL)
    _need(set(value) in ({"schema_version", "roles"},
                         {"schema_version", "roles", "qoder_execution"})
          and type(value["schema_version"]) is int
          and value["schema_version"] == 1, "AI_CONFIG_SCHEMA")
    _need(type(value["roles"]) is dict and set(value["roles"]) == set(ROLES), "AI_CONFIG_ROLES")
    for role in ROLES:
        setting = value["roles"][role]
        _need(type(setting) is dict and set(setting) == {"provider", "model", "reasoning_effort"},
              "AI_CONFIG_ROLE_FIELDS")
        _need(setting["provider"] == _PROVIDERS[role], "AI_CONFIG_PROVIDER")
        _need(type(setting["model"]) is str
              and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", setting["model"]), "AI_CONFIG_MODEL")
        _need(type(setting["reasoning_effort"]) is str and setting["reasoning_effort"] in _EFFORTS,
              "AI_CONFIG_EFFORT")
    if "qoder_execution" in value:
        execution = value["qoder_execution"]
        _need(type(execution) is dict and set(execution) == {"max_output_tokens"},
              "AI_CONFIG_QODER_EXECUTION_FIELDS")
        maximum = execution["max_output_tokens"]
        _need(type(maximum) is int and _QODER_OUTPUT_MIN <= maximum <= _QODER_OUTPUT_MAX,
              "AI_CONFIG_QODER_OUTPUT_TOKENS")
    return copy.deepcopy(value)


def role_settings(root: Path, role: str) -> dict:
    _need(type(role) is str and role in ROLES, "AI_CONFIG_ROLE")
    return load_ai_config(root)["roles"][role]


def load_runtime_config(root: Path) -> dict:
    value = _read(root, AI_RUNTIME_REL, private=True)
    _need(set(value) == {"schema_version", "coordinator"} and type(value["schema_version"]) is int
          and value["schema_version"] == 1, "AI_RUNTIME_SCHEMA")
    runtime = value["coordinator"]
    _need(type(runtime) is dict and {"path", "version", "sha256"} <= set(runtime)
          and set(runtime) <= {"path", "version", "sha256", "config_dir"}, "AI_RUNTIME_FIELDS")
    for key in ("path", "config_dir"):
        if key in runtime:
            _need(type(runtime[key]) is str and Path(runtime[key]).is_absolute()
                  and not any(ord(c) < 32 for c in runtime[key]), "AI_RUNTIME_PATH")
    _need(type(runtime["version"]) is str and 0 < len(runtime["version"]) <= 128
          and runtime["version"] == runtime["version"].strip()
          and all(32 <= ord(c) < 127 for c in runtime["version"]), "AI_RUNTIME_VERSION")
    _need(type(runtime["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", runtime["sha256"]),
          "AI_RUNTIME_SHA256")
    return copy.deepcopy(value)
