# GitHub Copilot Instructions

This file is intentionally short. Do not duplicate architecture, release history,
or operational runbooks here.

1. Read `AGENTS.md` before making substantial changes.
2. Follow `docs/memory/manifest.md` and load only task-relevant memory files.
3. Treat current code, tests, ADRs, and active runbooks as authoritative. Do not
   restore behavior from stale branches or historical plans.
4. Runtime entry points are `main.py`, `run_bot.py`, and `frontend/src/main.ts`.
   The legacy `api/`, `bot/`, and `models/` trees remain authoritative while
   migration to `src/` proceeds incrementally.
5. Explain project work to the owner in Persian. Keep code, identifiers, comments,
   and commit messages in English.
6. Never expose or commit secrets. Non-example environment files stay local and
   ignored.
7. Do not infer authorization for deploys, production mutations, branch deletion,
   history rewriting, or destructive cleanup.
8. Keep generated logs, test output, backups, and runtime data out of Git and
   subject to the repository retention policy.
