# Coin Market Intelligence

- USD/Herat leading digits: 3 same-book/source facts/15m; no constants/future replay.
- خ ن ف/ف ن ف، خ ف/ف ف یا none: tomorrow؛ خ ن/ف ن/نق: cash. Future wins; ignore registration.
- Prices: project-thousand, full-Toman /1,000, bounded zeros; reject quantities/scripts/years. `رب`=quarter؛ `پ`/`ت پ`/`پایین`=low-date.
- Named: no anchors. Unnamed: unit-safe `MAIN_ONLINE` + 2h same-book anchors; contradictions fail, overlaps need margin. Bootstrap: 3 messages/2 senders/30m/1.5% spread + one fact; no context default.
- Trades: isolated replies, oldest root, reciprocal offerer; cancellation/rejection gate fills. Only explicit reciprocal first fill changes quantity; ambiguity/overfill audit-only.
- Root-to-confirmation hashes; mismatches never rewrite roots. Three causal same-instrument offers/5m make >max(5%, 6 robust deviations) audit-only. Same-settlement preferred; cross-settlement fallback.
- Term structure yields to <=30m trade, 3 offers, or <=1% two-sided book; cap unsupported cash.
- Reconcile rejects bad reply graphs and keeps first availability. Projection drops absent/rejected; pending/conditional/>5m-late audit-only. Models use `available_at_utc`; reports source time.
- Malformed envelopes fail; inverted time cannot advance checkpoints; health dimensions stay separate.
- Private text: authenticated review only; projection opaque. Live jobs: `main`.
- Estimator: `estimator-live`; home CASH/TOMORROW; `/shadow` shadow/realised. Web displays stored facts, never recomputes.
- Regime order: private minute gold, Herat, XAU, resolved G1/G2 books. Keep direction/volatility and CASH/TOMORROW separate; low evidence abstains; live changes use hold-down. Public inputs are fallback only.
- Staging catalog mirrors Iran; Snapshot read-only. v3 exact bands; nearest same-family ±10% needs confirmation; ties require choice. Edits reuse scope, never auto-select; bot answers stale taps.
- Never put recurring collectors in dashboard/bridge `After=`; timer churn starves starts. Use `flock`.
- Condition v2 separates settlement/form, phase, deadline and 11 families; review phrases map only to parser-verified raw abbreviation spans. Sealed 240 stays blind, live shadow separate, and only digests/spans/revisions persist. Owner deferred training/operationalization: retain web review and shadow, with no offer, estimate, warning, tolerance, or trade effect; resumption needs an explicit decision and offline evaluation.
- Index book/time anchors; no per-message scan. Fetch newest-first then sort; oldest-first backlog preserves checkpoints.
