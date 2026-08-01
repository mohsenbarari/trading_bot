#!/usr/bin/env python3
"""Hard fail-closed boundary for the not-yet-implemented launch coordinator.

This module deliberately does not import Docker, subprocess, SSH, or a cloud
client.  It prevents a generated Compose file from being mistaken for an
approved deployment path while the reviewed execution coordinator and live
transport evidence are still absent.
"""

from __future__ import annotations

import json
import sys


def blocked_launch_result() -> dict[str, str]:
    return {
        "status": "blocked",
        "error": (
            "physical PostgreSQL launch is intentionally unavailable: "
            "a reviewed root-only execution coordinator and live continuity "
            "evidence are required"
        ),
        "error_class": "PhysicalPostgresLaunchUnavailable",
    }


def main() -> int:
    print(json.dumps(blocked_launch_result(), sort_keys=True, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
