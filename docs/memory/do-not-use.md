# Do Not Use / Tombstones

Tombstones are newest first.

- Never escape Docker template quotes; run them before writer quiescence.
- Never hash raw schema dumps; strip only PostgreSQL random `\\restrict`/`\\unrestrict` keys and version banners.
- Do not restore retired Release0/WA-IR/FI/Writer-Witness/Object-Delta refs/artifacts or three-site full-matrix v2/v3; that obsolete control plane conflicts with current signed sync, multi-publisher Queue, and canonical migrations.
- Do not restore the deleted admin/bot/Telegram market-monitoring branches; they were immature. Redesign any future monitoring capability from current `main` and its Queue/privacy contracts.
- Do not restore or reuse the retired Emergency IR fast-track branch family or its artifacts: the audited implementation was pinned to an obsolete base/schema, was internally unconsolidated, and was fully removed. Redesign any future isolated fallback from current `main`.
- Do not merge or resurrect `candidate/coin-price-intelligence`: its estimator app was already imported and subsequently hardened on `main`; its remaining Shadow v2/Gemma/static-bundle/direct-promotion paths and old DR migration chain are unpromoted or incompatible with the canonical Market Store architecture.
- Do not layer the obsolete single-bot limiter/skew series or its staging-only direct-send bypass onto the multi-publisher B2B queue; retain only architecture-independent fixes after targeted review.
- Do not use `has_bot_access` for runtime access gating; it is legacy compatibility for onboarding/accountant bootstrap and sync payload continuity. Use `account_status` as the access-control authority.
- Do not present developer-only login shortcuts as end-user product features.
- Do not replace `api/`, `bot/`, or `models/` wholesale with `src/`; the Clean Architecture migration is deliberately incremental.
- Do not infer chat albums from adjacent media; use `album_id` and `album_index` metadata.
