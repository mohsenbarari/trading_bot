# Constraints

- Never store credentials, API keys, OTPs, session tokens, connection strings, or other secrets in project memory, prompts, logs, or commits.
- Preserve existing user worktree changes unless the task explicitly owns them.
- Deployment, production, synchronization, and destructive data operations require scoped verification and explicit user authorization when they change external state.
- Keep durable memory concise, factual, and reviewable. Record only decisions, constraints, repeated corrections, and rejected approaches that remain relevant across sessions.
