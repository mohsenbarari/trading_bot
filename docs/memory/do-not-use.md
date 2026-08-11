# Do Not Use / Tombstones

Tombstones are newest first.

- Do not use `has_bot_access` for runtime access gating; it is legacy compatibility for onboarding/accountant bootstrap and sync payload continuity. Use `account_status` as the access-control authority.
- Do not present developer-only login shortcuts as end-user product features.
- Do not replace `api/`, `bot/`, or `models/` wholesale with `src/`; the Clean Architecture migration is deliberately incremental.
- Do not infer chat albums from adjacent media; use `album_id` and `album_index` metadata.
