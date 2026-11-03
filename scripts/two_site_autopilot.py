#!/usr/bin/env python3
"""Provider-neutral entry point for the two-site automation supervisor."""
try:
    from scripts.two_site_codex_autopilot import main
except ModuleNotFoundError:
    from two_site_codex_autopilot import main

if __name__ == "__main__":
    raise SystemExit(main())
