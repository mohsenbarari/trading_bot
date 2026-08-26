# Constraints

- Secrets never enter memory, prompts, logs, artifacts, `/tmp`, or Git. Infrastructure credentials use the root-only registry `/srv/trading-bot/secure/agent-access/`; load only the authorized subprocess credential and never print or hash it.
- Runtime-config tests must set `APP_ENV_FILE=config/unit-test.env.example`; never load the live `.env`.
- Market Store observations use canonical Toman units and magnitude guards; never restore Rial expectations or reconvert adapter-normalized legacy values.
- Estimator research/calibration cannot write beside live models or promote without the explicit runtime-staging flag; promotion is separately manual.
- Parity cannot promote moving-time replay. Compare XAU quotes/aligned values; minute buckets are not event-loss evidence. New/missing fields are schema drift, not financial mismatch.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep IDE state and non-example `.env*.local` untracked.
- External deployment, production, sync, or destructive changes require scoped verification and explicit authorization.
- Memory contains only concise, durable facts, decisions, constraints, corrections, and rejections.
- Queue-v1 is authorized; production deploy is separate. Staging/production bots, Publishers and channels stay distinct; collisions/shared-fleet opt-in block cutover.
- Telegram multi-publisher and B2B dispatch remain fail-closed by default; B2B dispatch may not be enabled unless multi-publisher is explicitly enabled.
- Offer `created_at` is the immutable lifetime anchor. Staging matrices record queue entry separately and use approved test lifetime, never schema rewrite.
- Noncanonical trade-delivery intents may keep `offer_id` null, but enqueue and repair must carry the source offer notes/home context explicitly.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
- Web offer-overtime preference belongs under Account/Settings; never render it in the Market feed.
