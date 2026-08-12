# Constraints

- Never store credentials, API keys, OTPs, session tokens, connection strings, or other secrets in project memory, prompts, logs, or commits.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep local IDE state and non-example `.env*.local` files untracked.
- Deployment, production, synchronization, and destructive data operations require scoped verification and explicit user authorization when they change external state.
- Keep durable memory concise, factual, and reviewable. Record only decisions, constraints, repeated corrections, and rejected approaches that remain relevant across sessions.
- Telegram multi-publisher and B2B dispatch remain fail-closed by default; B2B dispatch may not be enabled unless multi-publisher is explicitly enabled.
- Offer `created_at` remains the immutable registration-time lifetime anchor; staging matrices must never rewrite it. Record central-queue entry separately in evidence. Production registration is expected to hand off directly to central ingress, while a test backlog is handled with an approved staging lifetime rather than a schema change.
