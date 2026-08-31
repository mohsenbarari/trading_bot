# Constraints

- Secrets never enter memory, prompts, logs, artifacts, `/tmp`, or Git. Keep them root-only; authorized subprocesses never print/hash them.
- Runtime-config tests must set `APP_ENV_FILE=config/unit-test.env.example`; never load the live `.env`.
- Market Store uses canonical Toman units/magnitude guards; never restore Rial expectations or reconvert normalized legacy values.
- Point-in-time estimates require event, availability and local-insertion bounds; pin SQLite before timestamping, transfer before generation, and preserve the historical knowledge cutoff.
- Estimator research/calibration cannot write beside live models or promote without the explicit runtime-staging flag; promotion is separately manual.
- Parity cannot promote moving-time replay. Compare aligned XAU; minute buckets do not prove loss. Field presence is schema, not financial, drift.
- Preserve user changes. Keep one canonical worktree; any sibling clone/worktree requires an explicit task, owner, and expiry.
- Generated artifacts require bounded retention; preserve active/rollback releases, open-incident evidence, and the last restorable backup.
- Keep IDE state and non-example `.env*.local` untracked.
- External deploy, production, sync, or destructive changes require scoped verification and explicit authorization.
- Queue-v1 is authorized; production deploy is separate. Staging/production bots, publishers, and channels stay distinct; collisions block cutover.
- Telegram multi-publisher/B2B dispatch are fail-closed; B2B requires explicit multi-publisher enablement.
- Offer `created_at` is the immutable lifetime anchor. Staging matrices record queue entry separately and use approved test lifetime, never schema rewrite.
- Noncanonical trade-delivery intents may keep `offer_id` null; enqueue/repair must carry source notes/home context.
- Silent first-page offer refresh replaces the authoritative snapshot; merge only when additional pages are already loaded.
- Web offer-overtime preference belongs under Account/Settings; never render it in the Market feed.
