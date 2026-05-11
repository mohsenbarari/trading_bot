# CI Policy

This repository now treats browser-matrix validation as a formal gate, not an ad hoc local command.

## Merge Gate

Target branch: `main`

Trigger surface:
- GitHub pull requests targeting `main`
- Manual `workflow_dispatch` reruns when a maintainer wants to replay the gate

Required checks to mark as required in GitHub branch protection:
- `Merge Gate / repository-governance`
- `Merge Gate / frontend-browser-matrix (chromium)`
- `Merge Gate / frontend-browser-matrix (firefox)`
- `Merge Gate / frontend-browser-matrix (webkit)`

What the merge gate enforces:
- `make test-gate`
- `make test-diff-gate BASE=<pull-request-base-sha>`
- Frontend Playwright matrix on Chromium, Firefox, and WebKit

Merge policy:
- No PR should merge to `main` until all four required checks are green.
- Browser failures are blocking, even if Chromium passes, because Firefox/WebKit are now first-class support targets.

## Pre-release Gate

Target surface:
- Every push to `main`
- Manual `workflow_dispatch` for a specific release-candidate ref

Workflow name:
- `Pre-release Gate`

Required checks before a release/deploy candidate is considered ready:
- `Pre-release Gate / repository-governance`
- `Pre-release Gate / frontend-browser-matrix (chromium)`
- `Pre-release Gate / frontend-browser-matrix (firefox)`
- `Pre-release Gate / frontend-browser-matrix (webkit)`

What the pre-release gate enforces:
- `make test-gate`
- Frontend Playwright matrix on Chromium, Firefox, and WebKit

Pre-release policy:
- Deploy/release from the exact commit SHA that passed the pre-release gate.
- If a maintainer manually dispatches the workflow for a release candidate, use that run as the authoritative pre-release signal for the chosen ref.

## CI Bootstrap Notes

The browser-matrix workflows intentionally bootstrap the real backend test surface instead of mocking it:
- Write an ephemeral CI `.env`
- Run the staged backend bootstrap script that builds images, starts `db` and `redis`, executes `migration` as a one-shot step, then starts `app` and performs a container-local readiness probe against `/api/config`
- Build the frontend and run Playwright against the preview server in CI mode

This is required because the current Playwright specs seed data via `docker exec trading_bot_app ...` and hit the live backend on `http://127.0.0.1:8000`.

The staged bootstrap is intentional: a single `docker compose up --wait` proved too brittle across GitHub runner environments and produced poor failure visibility when the backend stack died before Playwright started. The readiness probe is also container-local so CI and local debug flows do not depend on publishing port `8000` to the host.

## Repository Limitation

The workflows in this repository create the status checks, but GitHub branch protection itself still has to be configured in repository settings to mark the merge-gate checks as required.