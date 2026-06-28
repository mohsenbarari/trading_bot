# DEV_API_KEY History Rewrite And Rotation Roadmap

- Date: 2026-06-28
- Branch during execution: `candidate/sync-parity-hardening`
- Initial confirmed finding: `VF2` in `docs/SECURITY_AUDIT_VALIDATION_20260628.md`
- Final local branch head after execution: `f7dff814`
- Secret policy: no raw key, token, password, OTP, or env value is recorded here.

## Goal

Remove the compromised production `DEV_API_KEY` literal from Git branch/tag history, replace the current tracked value with a placeholder, rotate runtime `DEV_API_KEY` values for production and staging, and document the exact operational state left behind.

## Safety Constraints

- Do not print the compromised value in terminal output, docs, logs, or commits.
- Rewrite Git history only after confirming the worktree is clean.
- Rewrite from a disposable mirror clone first, then force-push only branch/tag refs.
- Do not mutate production DB or Redis data.
- Recreate only services that need the new env value.
- Clean local refs, worktrees, reflogs, env backups, and temporary key files after rotation.

## Stage S0 - Preflight And Scope Check

Status: Completed.

Actions performed:

1. Checked repo state:
   - `git status --short --branch`
   - current branch was `candidate/sync-parity-hardening`.
2. Listed local and remote branches with full commit ids:
   - `git branch --all --verbose --no-abbrev`
3. Identified files and code surfaces mentioning `DEV_API_KEY`, `dev_api_key`, or `X-DEV-API-KEY`.
4. Confirmed the current tracked `.github/copilot-instructions.md` had the `DEV_API_KEY` label at line 107, and that the value was a literal secret rather than only an env-var reference.
5. Checked Git worktrees:
   - main worktree: `/root/trading-bot/trading_bot`
   - detached old worktree: `/root/trading-bot/messenger-bench-pre`
   - prunable stale worktree metadata under `/tmp/trading-bot-bot-webapp-integration`
6. Confirmed `git-filter-repo` was not initially installed.

Risk finding:

- A local-only archive branch and the detached benchmark worktree later proved to still retain old secret-bearing commits. They were not remote branch blockers, but they had to be removed locally before local Git cleanup could be considered complete.

## Stage S1 - Exact Secret Extraction Without Disclosure

Status: Completed.

Actions performed:

1. Extracted the compromised value from the known `DEV_API_KEY` line without printing it.
2. Stored it temporarily under a root-owned `/tmp` file with `0600` permissions for automated replacement and verification only.
3. Created a replacement rule mapping:
   - old exact secret value -> `REDACTED_DEV_API_KEY`
4. Verified the extracted value was long enough to avoid accidental broad replacement.

Notes:

- The value itself was never printed.
- Replacement was exact-value based, not pattern based.

## Stage S2 - First Rewrite Attempt And Tool Change

Status: Completed.

Actions performed:

1. Created a disposable mirror clone:
   - `/tmp/trading-bot-secret-rewrite.git`
2. Started a `git filter-branch` index-filter rewrite.
3. Stopped that attempt before any push because it was too slow for the repo size.
4. Installed the purpose-built tool from Ubuntu packages:
   - `git-filter-repo`

Reasoning:

- `git filter-branch` was working only on the disposable mirror, but projected runtime was high.
- No remote refs were changed before switching tools.
- `git-filter-repo` is the safer and faster tool for exact secret replacement across history.

## Stage S3 - Branch/Tag History Rewrite

Status: Completed.

Actions performed:

1. Recreated the disposable mirror clone from GitHub.
2. Ran `git filter-repo --replace-text` using the exact replacement file.
3. Rewrote all fetched branch/tag history in the disposable mirror.
4. Removed temporary `refs/replace/*` refs from the mirror before any push, so old-to-new mapping refs would not be published.
5. Verified the old key was not present in rewritten branch/tag commits before force-push.

Pre-push verification:

- Checked `2270` branch/tag commits.
- Result: old key not found in rewritten branch/tag history.

## Stage S4 - Controlled Force Push

Status: Completed.

Actions performed:

1. Disabled mirror push behavior in the disposable mirror clone so explicit refspecs could be used.
2. Force-pushed only:
   - `refs/heads/*`
   - `refs/tags/*`
3. Did not push:
   - `refs/pull/*`
   - `refs/replace/*`
   - any internal temporary refs

Refs force-updated:

- `backup/candidate-bot-webapp-integration-before-staging-cleanup-20260620`
- `backup/staging-bot-webapp-user-switcher-before-candidate-cleanup-20260620`
- `candidate/bot-standard-user-panel`
- `candidate/bot-webapp-integration`
- `candidate/bot-webapp-trade-status-fix`
- `candidate/sync-parity-hardening`
- `codex/logging-foundation-20260607`
- `loger`
- `main`
- `staging/bot-webapp-user-switcher-runtime`
- `staging/user-switcher`
- `staging/web-push-user-switcher`
- tag `v1.0.0.0`

Important impact:

- All agents and local clones based on pre-rewrite history must fetch/reset or re-clone.
- Pushing from an old clone can reintroduce the compromised history.

## Stage S5 - Local Checkout Synchronization And Cleanup

Status: Completed.

Actions performed:

1. Fetched rewritten branch/tag refs from GitHub with force.
2. Reset current checkout to:
   - `origin/candidate/sync-parity-hardening`
3. Repointed local branches that had remote counterparts to their rewritten `origin/*` refs.
4. Deleted a local-only archive branch because it still retained the old key:
   - `archive/trading-production-grade-web-push-merge-local`
5. Removed the detached old benchmark worktree because its `HEAD` still retained the old key:
   - `/root/trading-bot/messenger-bench-pre`
6. Pruned stale worktree metadata.
7. Expired reflogs:
   - `git reflog expire --expire=now --expire-unreachable=now --all`
8. Ran aggressive local GC:
   - `git gc --prune=now --aggressive`
9. Deleted stale local refs that still retained old commits:
   - `refs/codex/turn-diffs/*`
   - `refs/original/*`
   - `refs/stash`
10. Repeated reflog expiry and GC after deleting stale refs.

Local verification:

- Checked `2270` local reachable commits.
- Result: old key not found in local refs.
- `HEAD` also did not contain the old key.

## Stage S6 - Runtime Key Rotation

Status: Completed.

Actions performed:

1. Generated one new production `DEV_API_KEY`.
2. Generated a separate new staging `DEV_API_KEY`.
3. Updated production foreign runtime/source env files:
   - `.env`
   - `/root/secure-envs/trading-bot/.env.foreign.production`
4. Updated production Iran local runtime/source env files:
   - `.env.iran`
   - `/root/secure-envs/trading-bot/.env.iran.production`
5. Updated staging runtime env:
   - `.env.staging`
6. Copied updated Iran env files to the Iran server:
   - `/srv/trading-bot/current/.env`
   - `/root/secure-envs/trading-bot/.env.iran.production`
7. Set env-file permissions to `0600`.

Verification:

- Local production env files: old key absent, new production key hash matched.
- Local staging env file: old key absent, new staging key hash matched.
- Remote Iran env files: old key hash did not match, new production key hash matched.

## Stage S7 - Service Reload/Recreate

Status: Completed.

Foreign production:

1. Recreated only env-dependent app-side services:
   - `app`
   - `bot`
   - `sync_worker`
2. Did not recreate DB or Redis.
3. Verified containers were up after recreation.
4. Verified container env hashes matched the new production key.

Iran production:

1. Attempted to recreate `app` and `sync_worker`.
2. Encountered the known Docker Compose v1 `ContainerConfig` issue.
3. Removed/recreated only stateless target containers.
4. The failed compose convergence temporarily left the existing DB container stopped.
5. Started the same existing DB container without recreation.
6. Verified final Iran state:
   - Redis up.
   - DB up and healthy.
   - App up and healthy.
   - Sync worker up.
7. Verified Iran container env hashes matched the new production key.

Staging:

1. Ran `scripts/deploy_staging.sh deploy` so staging nginx and app env received the new staging key.
2. Observed the normal staging frontend build and app health wait.
3. Recreated remaining staging env-dependent services explicitly:
   - `foreign_app`
   - `foreign_sync_worker`
   - `bot`
4. Verified final staging state:
   - app healthy.
   - foreign_app healthy.
   - bot up.
   - foreign_sync_worker up.
   - DB healthy.
   - Redis up.
5. Verified staging container env hashes matched the new staging key.

## Stage S8 - Backup And Temporary Secret Cleanup

Status: Completed.

Actions performed:

1. Identified env backup files created during rotation.
2. Removed backups that could contain the compromised old key:
   - `.env.bak-20260628T174324Z`
   - `.env.staging.bak-20260628T174324Z`
   - `.env.iran.bak-20260628T174324Z`
   - `/root/secure-envs/trading-bot/.env.iran.production.bak-20260628T174324Z`
   - `/root/secure-envs/trading-bot/.env.foreign.production.bak-20260628T174324Z`
3. Removed temporary key and replacement files under `/tmp`.
4. Removed disposable mirror/verification clones:
   - `/tmp/trading-bot-secret-rewrite.git`
   - `/tmp/trading-bot-verify-clean.git`

## Stage S9 - Fresh Remote Verification

Status: Completed with a GitHub-managed PR-ref limitation.

Actions performed:

1. Created a fresh mirror clone from GitHub after force-push.
2. Checked fetched refs for the exact old key.
3. Found branch/tag refs clean.
4. Found the old key still present only in GitHub-managed pull request refs:
   - `refs/pull/1/merge`
   - `refs/pull/2/head`

Analysis:

- These are not normal branch or tag refs.
- They are generated/retained by GitHub for pull requests.
- They were not controllable through the branch/tag force-push operation.
- PR #1 was open at verification time.
- PR #2 was already merged at verification time.

Required follow-up if provider-level erasure is required:

1. Close or update the open PR that owns `refs/pull/1/merge`.
2. Ask GitHub Support for secret purge/cache cleanup for historical `refs/pull/*` refs.
3. Re-run a fresh mirror verification after GitHub-side cleanup.

## Stage S10 - Documentation And Commit

Status: Completed.

Actions performed:

1. Recorded a compact summary in `.github/copilot-instructions.md`.
2. Committed and pushed the summary:
   - `f7dff814 docs: record dev api key rotation`
3. Added this detailed roadmap/log so future agents can see the exact sequence and limitations.

## Stage S11 - Secret Comparison And Lint Gate Hardening

Status: Completed as a follow-up hardening item.

Actions performed:

1. Added `core.security.constant_time_secret_equals()` to centralize configured secret comparison with `hmac.compare_digest`.
2. Replaced direct equality/inequality checks for:
   - `DEV_API_KEY` admin/dev dependencies.
   - staging-only `dev-login` remote key access.
   - sync router dev-key, sync API key, observability key, and HMAC signature checks.
   - metrics observability key access.
   - session-authority and trade-forwarding internal sync keys.
3. Hardened commodities request-source detection so a request is marked `bot` only when `X-DEV-API-KEY` is actually valid, not merely present.
4. Added focused tests for the comparison helper and commodities invalid-key behavior.
5. Added a tracked-file static secret lint test that fails if a real-looking `DEV_API_KEY` literal is committed again.
6. No production deploy or runtime secret mutation was performed in this stage.

## Current Status

- Current branch: `candidate/sync-parity-hardening`
- Current local head: `f7dff814`
- Current remote branch head: `origin/candidate/sync-parity-hardening` -> `f7dff814`
- `origin/main` after rewrite: `70d6669d`
- Local working tree after the rotation run was clean before this documentation update.
- Branch/tag history is rewritten and force-pushed.
- Runtime production and staging keys are rotated.
- Production and staging app-side services are running with the rotated keys.
- DB/Redis data was not intentionally mutated.

## Remaining Risk Register

### R1 - GitHub `refs/pull/*` Retain Old Secret

Status: Open.

Risk:

- A fresh mirror fetch still found the old exact value in GitHub-managed PR refs.

Mitigation already done:

- Branch and tag refs were rewritten.
- Runtime keys were rotated, so the old key should no longer be accepted by production/staging services.

Remaining action:

- GitHub-side cleanup/support is required if full provider-level history erasure is mandatory.

### R2 - Other Agents May Push Old History

Status: Open operational risk.

Risk:

- Any old clone or agent checkout can reintroduce old commits if it pushes without resetting to rewritten history.

Required rule:

- All active agents must re-clone or run a hard reset to the rewritten remote branch they work on.
- No branch should be pushed from a pre-rewrite clone.

### R3 - DEV_API_KEY Still Used As A Broad Bypass Surface

Status: Partially remediated; design-scope narrowing remains open.

Scope:

- This run removed and rotated the leaked value.
- The follow-up hardening replaced regular equality checks and added a tracked-file secret-lint gate.
- It did not yet redesign every `DEV_API_KEY` consumer or remove all production dev-key bypass dependencies.

Next recommended remediation:

- Narrow production usage of dev-key bypass dependencies.
- Keep the static secret-lint gate in the required test suite.
