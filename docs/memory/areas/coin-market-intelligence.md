# Coin Market Intelligence

- USD/Herat: 3 facts/source/15m؛ no future replay.
- Settlement: خ ن ف/ف ن ف، خ ف/ف ف or none=tomorrow؛ خ ن/ف ن/نق=cash؛ future wins. Ingress accepts joined/spaced/ZWNJ خف/فف.
- Prices: project-thousand؛ full-Toman /1,000؛ 1–3 digits ×1,000؛ 4 invalid. Exclude quantities/scripts/years. `رب`=quarter؛ `پ`/`ت پ`/`پایین`=low-date.
- Named needs no anchors. Unnamed uses `MAIN_ONLINE` + 2h same-book anchors؛ contradictions fail. Bootstrap=3 messages/2 senders/30m/1.5% spread+fact.
- Trades: isolated replies, oldest root, reciprocal offerer؛ cancellation/rejection gate fills. Only explicit reciprocal first fill changes quantity؛ ambiguity/overfill audit-only.
- Hash mismatch never rewrites roots. Three causal same-instrument offers/5m make >max(5%, 6 deviations) audit-only. Prefer same-settlement؛ term structure yields to <=30m trade, 3 offers, or <=1% two-sided book.
- Reconcile rejects bad reply graphs, keeps first availability. Drop absent/rejected؛ pending/conditional/>5m-late audit-only. Models use `available_at_utc`.
- Malformed envelopes fail؛ inverted time cannot advance checkpoints. Private text needs authenticated review؛ projection opaque؛ jobs on `main`.
- Estimator: `estimator-live`؛ CASH/TOMORROW؛ `/shadow` facts-only؛ freshness=`event_time`/120s؛ timers oneshot inactivity. `LOW_PAPER_FALLBACK` has no price authority. Publisher transactional؛ SAFE_NO_DATA=0؛ failure=3.
- Regime order: private minute gold, Herat, XAU, resolved G1/G2. Separate direction/volatility and CASH/TOMORROW؛ low evidence abstains؛ hold-down changes؛ public fallback.
- Staging catalog mirrors Iran؛ Snapshot read-only. v3 exact bands؛ same-family ±10% confirmation؛ ties require choice؛ edits reuse scope؛ no auto-select؛ stale taps fail.
- Packs require «پک»؛ infer تمام/نیم/ربع from Snapshot؛ quantity 100؛ no lots؛ audit=`PACK_ONLY`.
- Collectors stay out of bridge `After=`؛ use `flock`.
- Host readiness uses non-secret settings, never Compose `.env`. Closed-market staleness is `DEGRADED_GUARD_FAIL_OPEN` only with healthy collectors/readable inputs.
- Condition v2 separates settlement/form, phase, deadline, 11 families؛ verified spans required. Sealed 240/live shadow stays non-operational؛ retain evidence؛ resume needs explicit offline-evaluated decision.
- Index anchors؛ no per-message scan. Fetch newest-first then sort؛ oldest-first backlog preserves checkpoints.
