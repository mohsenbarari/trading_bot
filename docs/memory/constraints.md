# Constraints

- Never store credentials, API keys, OTPs, session tokens, connection strings, or other secrets in project memory, prompts, logs, or commits.
- Product Market Store observations must use canonical Toman units and magnitude guards; never restore Rial-scale product expectations or apply a second Rial-to-Toman conversion to legacy values already normalized by an adapter.
- Estimator research/calibration commands must not write beside the live model or promote a model unless the operator supplies the explicit runtime-staging flag; promotion remains a separate manual decision.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep local IDE state and non-example `.env*.local` files untracked.
- Deployment, production, synchronization, and destructive data operations require scoped verification and explicit user authorization when they change external state.
- Keep durable memory concise, factual, and reviewable. Record only decisions, constraints, repeated corrections, and rejected approaches that remain relevant across sessions.
- Telegram multi-publisher and B2B dispatch remain fail-closed by default; B2B dispatch may not be enabled unless multi-publisher is explicitly enabled.
- Offer `created_at` remains the immutable registration-time lifetime anchor; staging matrices must never rewrite it. Record central-queue entry separately in evidence. Production registration is expected to hand off directly to central ingress, while a test backlog is handled with an approved staging lifetime rather than a schema change.
- Noncanonical trade-delivery intents may keep `offer_id` null, but enqueue and repair must carry the source offer notes/home context explicitly.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
