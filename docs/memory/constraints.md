# Constraints

- Secrets never enter memory, prompts, logs, artifacts, `/tmp`, or Git. Credentials stay in the root-only registry; load only the authorized subprocess secret and never print/hash it.
- Runtime-config tests must set `APP_ENV_FILE=config/unit-test.env.example`; never load the live `.env`.
- Market Store observations use canonical Toman units and magnitude guards; never restore Rial expectations or reconvert adapter-normalized legacy values.
- Point-in-time estimates require event, availability and local insertion by evaluation; pin SQLite before live timestamping, require transfer before generation, and keep the outer knowledge cutoff for historical anchors.
- Estimator research/calibration cannot write beside live models or promote without the explicit runtime-staging flag; promotion is separately manual.
- Parity cannot promote moving-time replay. Compare aligned XAU values; minute buckets do not prove loss. Field presence is schema drift, not financial mismatch.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep IDE state and non-example `.env*.local` untracked.
- External deploy, production, sync, or destructive changes require scoped verification and explicit authorization.
- Queue-v1 is authorized; production deploy is separate. Staging/production bots, Publishers and channels stay distinct; collisions/shared-fleet opt-in block cutover.
- Telegram multi-publisher/B2B dispatch are fail-closed; B2B requires explicit multi-publisher enablement.
- Offer `created_at` is the immutable lifetime anchor. Staging matrices record queue entry separately and use approved test lifetime, never schema rewrite.
- Noncanonical trade-delivery intents may keep `offer_id` null, but enqueue and repair must carry the source offer notes/home context explicitly.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
- Web offer-overtime preference belongs under Account/Settings; never render it in the Market feed.
