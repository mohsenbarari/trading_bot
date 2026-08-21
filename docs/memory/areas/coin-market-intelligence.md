# Coin Market Intelligence

- USD/Herat: 3 facts/book-source/15m؛ no constants/future replay.
- خ ن ف/ف ن ف، خ ف/ف ف یا none: tomorrow؛ خ ن/ف ن/نق: cash. Future wins.
- Prices: project-thousand; full-Toman /1,000. Inputs 1–3 digits ×1,000؛ 4 invalid. Reject quantities/scripts/years. `رب`=quarter؛ `پ`/`ت پ`/`پایین`=low-date.
- Named: no anchors. Unnamed: unit-safe `MAIN_ONLINE` + 2h same-book anchors; contradictions fail. Bootstrap: 3 messages/2 senders/30m/1.5% spread + one fact.
- Trades: isolated replies, oldest root, reciprocal offerer; cancellation/rejection gate fills. Only explicit reciprocal first fill changes quantity؛ ambiguity/overfill audit-only.
- Root/confirmation hash mismatch never rewrites roots. Three causal same-instrument offers/5m make >max(5%, 6 robust deviations) audit-only. Prefer same-settlement.
- Term structure yields to <=30m trade, 3 offers, or <=1% two-sided book.
- Reconcile rejects bad reply graphs and keeps first availability. Projection drops absent/rejected; pending/conditional/>5m-late are audit-only. Models use `available_at_utc`.
- Malformed envelopes fail؛ inverted time cannot advance checkpoints.
- Private text: authenticated review؛ opaque projection؛ jobs on `main`.
- Estimator: `estimator-live`؛ CASH/TOMORROW؛ `/shadow` facts-only. Freshness=`event_time`. Timers: oneshot inactivity. Aggregate paper=`LOW_PAPER_FALLBACK`, no price authority. Staging publisher transactional؛ SAFE_NO_DATA=exit0؛ exit3=failure؛ freshness=120s.
- Regime order: private minute gold, Herat, XAU, resolved G1/G2 books. Keep direction/volatility and CASH/TOMORROW separate; low evidence abstains; live changes use hold-down. Public inputs are fallback only.
- Staging catalog mirrors Iran؛ Snapshot read-only. v3 exact bands؛ same-family ±10% confirmation؛ ties require choice. Edits reuse scope؛ never auto-select؛ reject stale taps.
- Packs require explicit «پک»: derive تمام/نیم/ربع only from matching base Snapshot rates, never independent pack rates. Packs are wholesale: exactly 100, no lots.
- Collectors stay out of dashboard/bridge `After=`؛ use `flock`.
- Condition v2 separates settlement/form, phase, deadline and 11 families؛ reviews need verified spans. Sealed 240/live shadow remain non-operational؛ persist only digests/spans/revisions. Resume requires explicit offline-evaluated decision.
- Index book/time anchors؛ no per-message scan. Fetch newest-first then sort؛ oldest-first backlog preserves checkpoints.
