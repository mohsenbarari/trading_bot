# Constraints

- Never store secrets in project memory, prompts, logs, or commits.
- Runtime-config tests must set `APP_ENV_FILE=config/unit-test.env.example`; never load the live `.env`.
- Product Market Store observations must use canonical Toman units and magnitude guards; never restore Rial-scale product expectations or apply a second Rial-to-Toman conversion to legacy values already normalized by an adapter.
- Estimator research/calibration commands must not write beside the live model or promote a model unless the operator supplies the explicit runtime-staging flag; promotion remains a separate manual decision.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep local IDE state and non-example `.env*.local` files untracked.
- External deployment, production, sync, or destructive changes require scoped verification and explicit authorization.
- Keep memory concise and factual; record only durable decisions, constraints, corrections, and rejected approaches.
- Queue-v1 production is authorized, but production deployment remains separately scoped. Staging/production central bots, five Publishers, and channels must be distinct; any collision or shared-fleet opt-in blocks cutover.
- Telegram multi-publisher and B2B dispatch remain fail-closed by default; B2B dispatch may not be enabled unless multi-publisher is explicitly enabled.
- Offer `created_at` remains the immutable registration-time lifetime anchor; staging matrices must never rewrite it. Record central-queue entry separately in evidence. Production registration is expected to hand off directly to central ingress, while a test backlog is handled with an approved staging lifetime rather than a schema change.
- Noncanonical trade-delivery intents may keep `offer_id` null, but enqueue and repair must carry the source offer notes/home context explicitly.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
- Web offer-overtime preference belongs under Account/Settings; never render it in the Market feed.
