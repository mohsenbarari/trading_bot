# Constraints

- Never store credentials, API keys, OTPs, session tokens, connection strings, or other secrets in project memory, prompts, logs, or commits.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Keep local IDE state and non-example `.env*.local` files untracked.
- Deployment, production, synchronization, and destructive data operations require scoped verification and explicit user authorization when they change external state.
- Keep durable memory concise, factual, and reviewable. Record only decisions, constraints, repeated corrections, and rejected approaches that remain relevant across sessions.
- Telegram multi-publisher and B2B dispatch remain fail-closed by default; B2B dispatch may not be enabled unless multi-publisher is explicitly enabled.
- For bot-origin offers, the authoritative lifetime must start at the first durable central-queue enqueue; web-origin offers retain their registration-time lifetime. The timestamp must be exact, immutable, and available to both lifecycle enforcement and surface synchronization.
