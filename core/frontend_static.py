"""Serve the immutable frontend build without leaving its trusted directory.

Paths passed by the ASGI router are already URL-decoded. Do not decode them a
second time: literal percent-encoded filenames are not filesystem traversal.
The build directory and its contents must not be writable by upload clients.
"""

import logging
from pathlib import Path, PureWindowsPath

from starlette.responses import FileResponse, JSONResponse, Response


logger = logging.getLogger(__name__)
FRONTEND_DOCUMENT_HEADERS = {"Referrer-Policy": "no-referrer"}
BLOCKED_FRONTEND_PROBE_PATHS = {"openapi.json", "docs", "redoc"}


def _contained_frontend_path(static_dir: Path, full_path: str) -> Path | None:
    """Resolve against the build root, rejecting traversal and symlink escapes."""
    if (
        "\x00" in full_path
        or "\\" in full_path
        or full_path.startswith("/")
        or PureWindowsPath(full_path).drive
        or any(part in {".", ".."} for part in full_path.split("/"))
    ):
        return None
    try:
        build_root = static_dir.resolve()
        candidate = (build_root / full_path).resolve()
        if not candidate.is_relative_to(build_root):
            return None
        return candidate
    except (OSError, RuntimeError, ValueError):
        # Includes malformed paths, inaccessible directories and symlink loops.
        return None


def frontend_static_response(static_dir: Path, full_path: str) -> Response:
    """Apply the existing SPA policy after enforcing the filesystem boundary."""
    if (
        full_path.startswith("api/")
        or full_path in BLOCKED_FRONTEND_PROBE_PATHS
        or full_path.startswith("docs/")
        or full_path.startswith("redoc/")
    ):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    file_path = _contained_frontend_path(static_dir, full_path)
    if file_path is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        if file_path.is_file():
            return FileResponse(file_path, headers=FRONTEND_DOCUMENT_HEADERS)
    except (OSError, ValueError):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if full_path.startswith("assets/") and full_path.endswith(".js"):
        logger.warning("Old JS chunk requested; returning a bounded 410 response.")
        return Response(
            status_code=410,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # The index needs the same boundary check: a fallback must never follow an
    # out-of-build symlink, even when the requested SPA path itself is harmless.
    index_path = _contained_frontend_path(static_dir, "index.html")
    if index_path is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(index_path, headers=FRONTEND_DOCUMENT_HEADERS)
