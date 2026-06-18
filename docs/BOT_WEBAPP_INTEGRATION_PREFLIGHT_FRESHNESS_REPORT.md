# Bot/WebApp Integration Preflight Freshness Report

Date: 2026-06-18

Purpose: record the required pre-code freshness check for
`candidate/bot-webapp-integration` before implementation starts. This report is an operational
gate only. It does not authorize merge, rebase, production deploy, or any cross-branch promotion.

## Branch And Remote State

- Checked branch: `candidate/bot-webapp-integration`
- Worktree used for the check: `/tmp/trading-bot-bot-webapp-integration`
- Working tree state during the check: clean
- `git fetch origin` completed before collecting SHAs.
- Local `main`: `3ea87511b9938392e078f14b29c129745d216242`
- `origin/main`: `3ea87511b9938392e078f14b29c129745d216242`
- Local `candidate/bot-webapp-integration`:
  `db90ff45b7e7ad6f5b89e518e798acf03357fb7b`
- `origin/candidate/bot-webapp-integration`:
  `db90ff45b7e7ad6f5b89e518e798acf03357fb7b`
- Merge base between `main` and `candidate/bot-webapp-integration`:
  `136eff94e66a078b2311b9c48dc8090a619d6f5a`

## Divergence

`git rev-list --left-right --count main...HEAD` returned:

```text
41 34
```

Interpretation:

- `main` has 41 commits that are not in `candidate/bot-webapp-integration`.
- `candidate/bot-webapp-integration` has 34 commits that are not in `main`.
- The candidate branch is up to date with its remote tracking branch.
- Local `main` and `origin/main` are aligned.

## Diff Surface

From the merge base to `candidate/bot-webapp-integration`, the candidate branch changes only:

- `docs/BOT_WEBAPP_CROSS_SERVER_POLICY_AND_CHALLENGES.md`

The current candidate-side diff stat against the merge base is:

```text
1 file changed, 1186 insertions(+), 126 deletions(-)
```

However, `main` has moved substantially since the branch point. `HEAD..main` shows 45 changed files,
including implementation areas that matter for this roadmap:

- `api/routers/offers.py`
- `api/routers/trades.py`
- `bot/handlers/trade_create.py`
- `bot/handlers/trade_manage.py`
- `core/events.py`
- `core/offer_expiry.py`
- `core/server_routing.py`
- `core/web_push.py`
- `models/offer.py`
- `schemas.py`
- offer, expiry, trade forwarding, Web Push, notification, and production deployment tests

## Merge Simulation

`git merge-tree --write-tree main HEAD` completed successfully and returned:

```text
40d1ce2222b726852e7f0b72ea587bf86f2fbc80
```

This means Git did not detect a textual merge conflict for combining current `main` with current
`candidate/bot-webapp-integration`.

## Risk Assessment

- Textual merge conflict risk is low based on the successful merge simulation.
- Semantic drift risk is medium to high because `main` changed offer creation, trade execution,
  bot handlers, offer expiry, Web Push, notification behavior, deployment scripts, migrations, and
  related tests after the candidate branch point.
- Starting implementation from the stale candidate branch would increase the chance of designing
  against outdated code paths.

## Recommendation

Before code implementation starts, refresh `candidate/bot-webapp-integration` from current `main`.

Preferred refresh method:

- Use a normal merge from `main` into `candidate/bot-webapp-integration`.
- Reason: the candidate branch has already been pushed and contains a visible roadmap decision
  history. A merge preserves that history without force-pushing.

Alternative:

- Rebase onto `main` only if the owner explicitly wants linear history and accepts the need to
  rewrite the pushed candidate branch.

Required approval:

- This report does not authorize the refresh by itself.
- The owner must explicitly approve the merge or rebase before it is performed.
- After refresh, rerun the branch check and re-run a short freshness/status check before starting
  Level 1 code changes.

## Next Implementation Gate After Refresh

After the branch is refreshed and clean, start Level 1 implementation in this order:

1. Add the source-surface and `Offer.home_server` tests for WebApp, Bot, and sync-created offers.
2. Add the model/table inventory and starter sync-registry tests.
3. Make unknown or policy-forbidden sync tables fail closed.
4. Add deployment/runtime surface guard tests before changing runtime behavior.

No production action is allowed from this roadmap without the final documented gates and the exact
owner approval phrase.
