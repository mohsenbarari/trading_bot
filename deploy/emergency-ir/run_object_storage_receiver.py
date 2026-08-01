#!/usr/bin/env python3
"""Bootstrap entry point for the sealed Emergency Object Storage receiver.

The bootstrap deliberately invokes this file with ``python -I -B``.  Isolated
mode does not add the script directory to ``sys.path``, so add only the
root-owned extracted bundle directory explicitly before importing the bundled
``scripts`` namespace.  This avoids inheriting controller or host Python paths
while keeping the tiny receiver bundle self-contained.
"""

from pathlib import Path
import sys


ENTRYPOINT_DIRECTORY = Path(__file__).resolve().parent
for candidate in (ENTRYPOINT_DIRECTORY, *ENTRYPOINT_DIRECTORY.parents):
    if (
        (candidate / "scripts" / "__init__.py").is_file()
        and (candidate / "scripts" / "emergency_ir_object_storage_receiver.py").is_file()
    ):
        BUNDLE_ROOT = candidate
        break
else:  # pragma: no cover - the bootstrap bundle contract always supplies it.
    raise RuntimeError("sealed Emergency receiver bundle is incomplete")
_BUNDLE_ROOT_TEXT = str(BUNDLE_ROOT)
sys.path[:] = [entry for entry in sys.path if entry != _BUNDLE_ROOT_TEXT]
sys.path.insert(0, _BUNDLE_ROOT_TEXT)

from scripts.emergency_ir_object_storage_receiver import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
