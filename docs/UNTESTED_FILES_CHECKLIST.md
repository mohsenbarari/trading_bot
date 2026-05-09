# Untested Files Checklist

## Scope

- Snapshot: 2026-05-09 06:47 UTC
- Inventory source: `git ls-files`
- Excluded from this report: `frontend/**`, `src/**`, and `tests/**`
- A file is treated as already covered only when it is either present in the current backend coverage snapshot (`tmp/backend-coverage-html/status.json`) or explicitly validated by an existing smoke test such as `tests/test_deploy_surface_smoke.py`, `tests/test_migration_smoke.py`, `tests/test_scripts_surface_smoke.py`, or `tests/test_static_surface_smoke.py`.
- Result: 21 tracked files still have no direct automated coverage or file-specific smoke validation at this snapshot.
- All other tracked files outside the excluded directories already have executable coverage or explicit smoke validation.

## High-Priority Runtime Or Config Gaps

- [ ] `seed_fake_data.py` - executable data seeding script; add CLI/smoke coverage.
- [ ] `run_migration.sh` - shell entrypoint; add `bash -n` or execution smoke.
- [ ] `requirements.txt` - dependency manifest; add consistency/install smoke.
- [ ] `trading_settings.json` - default config payload; add schema/default-load validation.
- [ ] `deploy.sh.example` - deployment example file; add syntax/parity smoke.
- [ ] `migrations/__init__py` - tracked migration artifact with a suspicious filename; decide whether to remove/rename or validate explicitly.
- [ ] `migrations/versions/9` - empty tracked file under migration revisions; decide whether to remove or cover with repository hygiene checks.

## Repository Metadata And Documentation Gaps

- [ ] `.dockerignore` - repository metadata file; no automated validation yet.
- [ ] `.github/copilot-instructions.md` - instruction document; no lint/schema validation yet.
- [ ] `.gitignore` - repository metadata file; no automated validation yet.
- [ ] `.repomixignore` - assistant packaging metadata file; no automated validation yet.
- [ ] `LOCAL_ASSISTANT_CONTEXT.md` - local assistant context document; no automated validation yet.
- [ ] `STRUCTURE.md` - repository structure document; no automated validation yet.
- [ ] `__init__.py` - root package marker; not covered by any explicit smoke check.
- [ ] `docs/AUTOMATED_TEST_CHECKLIST.md` - checklist document; no markdown/link validation yet.
- [ ] `docs/GROUP_CHANNEL_MESSENGER_SPEC.md` - product specification document; no markdown/link validation yet.
- [ ] `repomix-output.xml` - generated assistant corpus artifact; no integrity smoke validation yet.

## Migration Template And Meta Gaps

- [ ] `alembic/README` - migration helper document; no automated validation yet.
- [ ] `alembic/script.py.mako` - Alembic template file; not validated directly.
- [ ] `migrations/README` - migration helper document; no automated validation yet.
- [ ] `migrations/script.py.mako` - migration template file; not validated directly.

## Suggested Next Coverage Steps

1. Add smoke coverage for `run_migration.sh`, `seed_fake_data.py`, and `deploy.sh.example` first because they are executable/supporting runtime entrypoints.
2. Add schema or consistency validation for `trading_settings.json` and `requirements.txt`.
3. Add a repository-hygiene/doc-validation test slice for markdown, ignore files, and template files.
4. Resolve whether `migrations/__init__py` and `migrations/versions/9` should remain tracked; if yes, cover them with an explicit repository-surface smoke test.