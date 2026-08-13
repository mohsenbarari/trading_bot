# Do Not Use / Tombstones

Tombstones are newest first.

- Do not merge or resurrect `feature/three-site-full-matrix-live-driver-v2` or `v3`: their three-site DR/Writer Witness design is retired, their Queue implementation is superseded by the current multi-publisher B2B architecture, and their old migration line is incompatible with canonical `main`.
- Do not merge or resurrect `candidate/coin-price-intelligence`: its estimator app was already imported and subsequently hardened on `main`; its remaining Shadow v2/Gemma/static-bundle/direct-promotion paths and old DR migration chain are unpromoted or incompatible with the canonical Market Store architecture.
- Do not layer the obsolete single-bot limiter/skew series or its staging-only direct-send bypass onto the multi-publisher B2B queue; retain only architecture-independent fixes after targeted review.
- Do not use `has_bot_access` for runtime access gating; it is legacy compatibility for onboarding/accountant bootstrap and sync payload continuity. Use `account_status` as the access-control authority.
- Do not present developer-only login shortcuts as end-user product features.
- Do not replace `api/`, `bot/`, or `models/` wholesale with `src/`; the Clean Architecture migration is deliberately incremental.
- Do not infer chat albums from adjacent media; use `album_id` and `album_index` metadata.
