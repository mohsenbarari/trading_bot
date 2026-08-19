# Coin Market Intelligence

- USD/Herat leading digits need 3 same-book/source facts/15m; no constants/future replay.
- `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, or no marker mean tomorrow; `خ ن`/`ف ن`/`نق` mean cash. Future wins; exclude registration.
- Prices accept project-thousand, full-Toman /1,000 or bounded zeros; reject quantities/scripts/years. `رب` is quarter; `پ`/`ت پ`/`پایین` mean low-date.
- Named offers need no anchors. Unnamed use unit-safe `MAIN_ONLINE` bands plus 2h same-book anchors; contradictions fail, overlaps need margin. Bootstrap needs 3 messages/2 senders/30m/1.5% spread and one fact; no context default.
- Trades require isolated replies, oldest root and reciprocal offerer; cancellation/rejection gates fills. Only explicit reciprocal first fill amends quantity; ambiguous/overfills are audit-only.
- Feedback hashes root-to-confirmation; mismatches cannot rewrite roots. With 3 causal same-instrument/5m offers, >max(5%, 6 robust deviations) is audit-only. Prefer same settlement; cross-settlement is fallback.
- Term structure cannot override <=30m evidence (trade, 3 offers, or <=1% two-sided book); cap unsupported cash.
- Reconciliation rejects invalid reply graphs; facts keep first availability. Projection drops absent/rejected facts; pending/conditional/>5m-late are audit-only. Models use `available_at_utc`; reports source time.
- Reject malformed envelopes; inverted time cannot advance checkpoints. Separate heartbeat/event/eligible health.
- Private text stays in authenticated review; Store/projection stay opaque. Live jobs use `main`.
- Estimator: `estimator-live`; home CASH/TOMORROW; `/shadow` shadow/realised. Web UI displays recorded parser/model facts and never recomputes.
- Canonical regime priority: private minute gold, Herat, then XAU and resolved Group 1/2 coin books. Direction/volatility and CASH/TOMORROW stay separate; low evidence abstains, live changes use hold-down. Public inputs are unconfigured fallback only.
- Staging catalog mirrors Iran; Snapshot is read-only. v3 uses exact bands, else confirms one nearest same-family center within ±10%; ties require choice. Edits reuse scope, omit receipt, never auto-select; bot enters choice before buttons and answers stale taps.
- Bridge/collectors share writer `flock`; ordering bridge `After=` recurring collectors starves it and stales Snapshots.
- Reviews use opaque digests, never raw identity/text. Revisions correct facts; anchors never alter prior input.
- Index anchors by book/time; never rescan per message. Fetch deltas newest-first then sort; oldest-first backlog preserves checkpoints.
