# Deployment Surface Centralization Roadmap

## Goal

Reduce Iran host/domain migration from a multi-file manual change into a manifest-driven flow where runtime, deploy scripts, and standalone operational helpers read from a single configuration surface.

This roadmap is deliberately incremental. Each stage must preserve the current two-server behavior while removing hardcoded Iran/foreign network identities from code paths that are expensive to audit during a production move.

## Source of Truth

Primary source of truth:

- `deploy/production/online.env`

Generated or derived surfaces:

- Foreign runtime `.env`
- Iran runtime `.env`
- Nginx server_name / certbot targets
- `/etc/hosts` sync entries
- Healthcheck URLs
- CORS / host affinity / SMS public links

## Stage C0 — Runtime Surface Inventory

Files to audit:

- `core/config.py`
- `main.py`
- `core/server_routing.py`
- `core/sms.py`
- `deploy.sh`
- `Makefile`
- `scripts/setup_iran_nginx.sh`
- `scripts/recover_cross_server_sync.sh`
- `scripts/sample_sync_health.py`
- `scripts/production_deploy_online.sh`

Exit criteria:

- Every Iran/foreign host, IP, origin, URL, and public domain dependency is classified as either:
  - runtime-derived
  - deploy-derived
  - test fixture only
  - historical/doc-only

## Stage C1 — Runtime Network Identity Centralization

Move runtime network identity reads behind configuration helpers so app code no longer hardcodes Iran or foreign domains/IPs.

Planned changes:

- Add settings fields for:
  - `IRAN_SERVER_ALIASES`
  - `FOREIGN_SERVER_ALIASES`
  - `EXTRA_CORS_ORIGINS`
- Add helper functions for:
  - origin normalization
  - host extraction
  - env-driven server alias expansion
  - SMS public host derivation
- Refactor:
  - `main.py`
  - `core/server_routing.py`
  - `core/sms.py`

Exit criteria:

- Runtime host/origin routing depends on env-derived values, not embedded Iran/foreign literals.
- SMS OTP footer host is derived from config, not hardcoded.

## Stage C2 — Standalone Operational Script Parameterization

Remove direct Iran domain/path assumptions from standalone scripts that may be run outside the full release flow.

Planned changes:

- Parameterize `scripts/setup_iran_nginx.sh` via env/defaults for:
  - app domain
  - certbot email
  - project directory
  - static asset directory

Exit criteria:

- Standalone Nginx bootstrap can target a new Iran domain without editing the script.

## Stage C3 — Operator Entry Point Consolidation

Reduce hardcoded Iran SSH target duplication across operator commands.

Planned changes:

- Make `Makefile`, `deploy.sh`, `scripts/recover_cross_server_sync.sh`, and `scripts/sample_sync_health.py` prefer manifest/env-derived Iran host values before fallback defaults.
- Keep explicit fallback values only as last-resort development defaults.

Exit criteria:

- Changing the Iran server for day-to-day operational commands does not require editing multiple entrypoint files.

## Stage C4 — Release Artifact Generation

Generate deploy/runtime artifacts from the manifest rather than editing templates by hand.

Planned changes:

- Add a renderer for:
  - foreign runtime env
  - Iran runtime env
  - host mapping entries
  - Nginx values consumed by release
- Add validation for missing or contradictory deploy values.

Exit criteria:

- Operator edits one manifest and the release helpers derive the rest.

## Stage C5 — Hardcode Guardrail

Add a focused regression gate that fails when new Iran/foreign deployment identities leak back into runtime code.

Planned changes:

- Add tests or lint-like checks for:
  - hardcoded production Iran/foreign domains in runtime code
  - hardcoded production Iran IPs in runtime code
  - script defaults drifting from manifest expectations

Exit criteria:

- New hardcoded deployment identity leakage is caught in CI/local validation.

## Recommended Execution Order

1. Stage C1
2. Stage C2
3. Stage C3
4. Stage C4
5. Stage C5

## Current Turn Scope

This turn should implement:

- Stage C1
- Stage C2

Stage C3+ stay separate because they touch more operator workflows and deserve isolated verification.
