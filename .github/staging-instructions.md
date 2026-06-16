# Staging Environment Instructions

This file is the authoritative guardrail for staging validation and every
production-grade change that must be proven before production promotion.

## Hard Rules

- Treat production as protected. Do not run `make production-release`,
  `make up`, `make iran`, `make foreign`, or any production deploy command
  unless the user explicitly asks for production deployment in the same turn.
- Use only `scripts/deploy_staging.sh` for staging lifecycle work.
- Staging frontend builds must use an isolated artifact directory, defaulting to
  `mini_app_dist_staging`, and must never write to or serve production
  `mini_app_dist`.
- Never point staging at production data, production Redis, production Docker
  volumes, or production sync peers.
- Never enable cross-server sync in staging unless a dedicated non-production
  peer is explicitly approved.
- Never use the production Telegram bot token in staging.
- Never commit `.env.staging`, `.env.staging.*`, secrets, Basic Auth passwords,
  VAPID private keys, API keys, generated host files, OTPs, tokens, or signed
  URLs.
- Any staging-only auth bypass, user switcher, quick login, or test-user helper
  must be hard-gated by `settings.environment == "staging"` and the staging
  `DEV_API_KEY`.
- If a staging helper could expose data, create sessions, switch users, or alter
  login behavior, tests must prove it is blocked outside staging.
- Staging is for validation, not a second production. Do not seed it from live
  production data unless the user explicitly approves a sanitized process.
- Staging branches are not automatically release branches. A branch used for
  staging validation must never be merged to `main` unless it passes the
  production-readiness review and the user explicitly approves promotion.
- Do not create long-lived staging/candidate branches from stale commits. Branch
  from the intended base and state the base commit in the work summary.

## Current Staging Shape

| Item | Current Contract |
|---|---|
| Domain | `staging.362514.ir` |
| Compose project | `trading_bot_staging` |
| Compose file | `deploy/staging/docker-compose.staging.yml` |
| Runtime env | `.env.staging` |
| API loopback port | `127.0.0.1:8100` by default |
| Frontend dist | `mini_app_dist_staging`; never production `mini_app_dist` |
| Nginx template | `deploy/staging/nginx-staging.conf.template` |
| Deploy script | `scripts/deploy_staging.sh` |
| Default services | `app`, `migration`, `db`, `redis` |
| Default disabled services | `bot`, `sync_worker` |

Staging must remain isolated through a separate Docker Compose project, separate
PostgreSQL and Redis volumes, separate uploads/audit volumes, separate Nginx
site, separate env file, and separate frontend build artifact.

## Safe Commands

```bash
scripts/deploy_staging.sh check
scripts/deploy_staging.sh ensure-env
scripts/deploy_staging.sh deploy
scripts/deploy_staging.sh ps
scripts/deploy_staging.sh health
scripts/deploy_staging.sh logs
scripts/deploy_staging.sh down
```

## Branch Naming And Promotion Rules

Branch names must make release intent explicit:

| Branch pattern | Purpose | May merge to `main`? |
|---|---|---|
| `staging/<topic>` | Staging-only environment/tooling work | No, unless explicitly promoted |
| `candidate/<topic>` | Release-candidate work validated on staging | Yes, only after approval |
| `hotfix/<topic>` | Urgent production fix | Yes, after focused staging validation or explicit emergency approval |
| `experiment/<topic>` | Throwaway prototype or risky exploration | No |
| `archive/<topic>` | Preserved reference branch | No |

Rules:

- Never name a staging-only branch as if it were a release candidate.
- Never merge `staging/*`, `experiment/*`, or `archive/*` into `main` by default.
- Before promoting staging work, create or rename to `candidate/<topic>` and
  document what changed, what was tested on staging, and what remains risky.
- If a branch contains staging-only code such as quick-login, user switcher, fake
  users, Basic Auth config, or local test credentials, it must not be merged to
  `main` unless those paths are environment-gated and verified by tests.
- If branch purpose is unclear, stop and ask the user before merging, rebasing
  across release branches, or deleting the branch.

## Active Branch Intent Registry

| Branch | Intent | Main promotion |
|---|---|---|
| `candidate/trading-production-grade` | Production-grade trade/offer hardening and market notification work validated through staging before any production promotion. | Allowed after staging validation and explicit approval |
| `candidate/web-push-notifications` | Web Push notification work that should be merged to `main` only at the correct release point after staging validation and explicit approval. | Allowed after approval |
| `staging/user-switcher` | Staging-only user-switcher/reference branch. | Not allowed |
| `staging/web-push-user-switcher` | Active staging branch for Web Push/user-switcher validation and staging guardrail work. | Not allowed as-is; promote through `candidate/*` only if explicitly approved |

## Validation Expectations

For staging infrastructure changes:

- Run `scripts/deploy_staging.sh check`.
- Run focused unit tests for touched staging/auth/session/frontend areas.
- If runtime behavior changed, deploy to staging with
  `scripts/deploy_staging.sh deploy` and verify
  `scripts/deploy_staging.sh health`.

For production-grade application changes:

- Prefer focused backend/frontend tests first.
- Deploy to staging before any production release consideration when runtime
  behavior changes.
- Do not run production benchmark/load tests unless the user explicitly asks for
  production validation in the same turn.
- Document rollback and remaining risk before requesting production promotion.

## Production-Grade Workflow

1. Implement the smallest safe change.
2. Validate locally with focused tests.
3. Deploy and validate on staging when the change affects runtime behavior.
4. Report staging results and remaining risk.
5. Deploy to production only after explicit user approval or a previously agreed
   release gate for that exact change.

## Change History

| Date | Assistant | Description |
| :--- | :--- | :--- |
| 2026-06-16 | Codex | Added the staging frontend artifact guardrail: staging builds into `mini_app_dist_staging`, staging Nginx serves that isolated directory, the staging Docker build copies that same artifact, and regression coverage rejects sharing production `mini_app_dist`. |
| 2026-06-16 | Codex | Updated the `candidate/trading-production-grade` registry entry to include market notification work after Web Push and market-offer push changes moved onto that production-candidate branch for staging validation. |
| 2026-06-16 | Codex | Added the staging guardrail document to `candidate/trading-production-grade` so the production-grade trading roadmap can proceed from a compliant candidate branch instead of a staging-only branch. |
