#!/usr/bin/env python3
"""Bootstrap entry point for the sealed Emergency Object Storage receiver.

The bootstrap deliberately invokes this file with ``python -I -B``.  Isolated
mode does not add the script directory to ``sys.path``, so add only the
root-owned extracted bundle directory explicitly before importing the bundled
``scripts`` namespace.  This avoids inheriting controller or host Python paths
while keeping the tiny receiver bundle self-contained.
"""

from pathlib import Path
import os
import stat
import sys
import types


ENTRYPOINT_DIRECTORY = Path(__file__).resolve().parent
for candidate in (ENTRYPOINT_DIRECTORY, *ENTRYPOINT_DIRECTORY.parents):
    if (candidate / "scripts" / "emergency_ir_object_storage_receiver.py").is_file():
        BUNDLE_ROOT = candidate
        break
else:  # pragma: no cover - the bootstrap bundle contract always supplies it.
    raise RuntimeError("sealed Emergency receiver bundle is incomplete")


def _install_pinned_scripts_namespace() -> None:
    """Prevent an ambient system-site ``scripts`` package from shadowing us."""

    scripts_root = BUNDLE_ROOT / "scripts"
    try:
        state = scripts_root.lstat()
    except OSError as exc:
        raise RuntimeError("sealed Emergency receiver scripts directory is unavailable") from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISDIR(state.st_mode)
        or state.st_uid != os.geteuid()
        or stat.S_IMODE(state.st_mode) & 0o022
    ):
        raise RuntimeError("sealed Emergency receiver scripts directory is unsafe")
    try:
        (scripts_root / "__init__.py").lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("sealed Emergency receiver package initializer cannot be inspected") from exc
    else:
        raise RuntimeError("sealed Emergency receiver bundle contains an unsupported scripts initializer")
    expected = str(scripts_root)
    present = sys.modules.get("scripts")
    if present is not None:
        paths = getattr(present, "__path__", None)
        if (
            getattr(present, "__file__", None) is not None
            or paths is None
            or [str(item) for item in paths] != [expected]
        ):
            raise RuntimeError("sealed Emergency receiver scripts namespace was preloaded from an ambient path")
        return
    namespace = types.ModuleType("scripts")
    namespace.__package__ = "scripts"
    namespace.__path__ = [expected]  # type: ignore[attr-defined]
    sys.modules["scripts"] = namespace


_install_pinned_scripts_namespace()

from scripts.emergency_ir_object_storage_receiver import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
