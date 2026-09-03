# Constraints

- Secrets never enter memory/prompts/logs/artifacts/`/tmp`/Git; keep them root-only
  and never print/hash them.
- Runtime-config tests must set `APP_ENV_FILE=config/unit-test.env.example`; never load the live `.env`.
- Market Store uses canonical Toman magnitude guards; never restore Rial or reconvert normalized legacy values.
- Point-in-time estimates preserve event/availability/local-insert bounds and the
  historical cutoff; pin SQLite before timestamping and transfer before generation.
- Estimator research cannot write beside live models; staging flag and separate manual promotion are mandatory.
- Parity freezes time and aligns XAU; minute buckets do not prove loss. Field presence is schema, not financial, drift.
- Preserve user changes; use one integration worktree plus at most two
  expiring Stage worktrees with task/base/locks/owner and cleanup.
- Generated artifacts have bounded retention; keep active/rollback releases, open-incident evidence and the last restorable backup.
- Keep IDE state and non-example `.env*.local` untracked.
- External deploy/production/sync/destruction requires scoped verification and explicit authorization.
- Queue-v1 is authorized; production deploy is separate. Staging/production Telegram identities stay distinct; collisions block cutover.
- Telegram multi-publisher/B2B is fail-closed and needs explicit enablement.
- Offer `created_at` is the immutable lifetime anchor. Staging matrices record queue entry separately and use approved test lifetime, never schema rewrite.
- Noncanonical trade-delivery may keep `offer_id` null; enqueue/repair carries source notes/home context.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
- Web offer-overtime preference belongs under Account/Settings; never render it in the Market feed.
- Finland consolidation is topology-only: preserve Web/Bot policy, contract,
  state, timing and side effects. Fixes need separate approval; unknown parity blocks.
