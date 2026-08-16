# Coin Market Intelligence

- USD/Herat repairs infer omitted leading digits from three prior same-book,
  same-source facts within 15 minutes; never add constants or replay out of order.
- Private-group `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, and marker-less sides mean
  tomorrow; `خ ن`/`ف ن` and `نق`/cash words mean cash. Future wins. Do not use
  this default for user registration.
- Coin-group prices accept project-thousand, full-Toman divided once by 1,000,
  or bounded redundant zeros; never constants. Quantities, scripts and mint
  years are not prices. `رب` is quarter; `پ`/`ت پ`/`پایین` mark low-date.
- Explicit, complete commodity offers are immediately eligible; missing anchors
  never make them pending. Only decisive authoritative prior evidence may reject
  them. Unnamed offers need causal resolution: same-book anchors expire after
  2h; overlapping full-coin bands require all plausible books. Bootstrap: 30m,
  three messages, two senders, one nonconditional claim, 1.5% spread.
  Conditional claims only support. Never default Imam or restore
  `group_commodity_context`.
- Trades require a complete linked branch, isolated users/siblings, oldest root,
  and reciprocal offerer evidence. Cancellation breaks inherited terms; later
  rejection gates the fill. Keep final price/quantity, deduplicate, and link the
  opaque root. Ambiguous/overfilled/unresolved trades stay audit-only.
- Reconciliation rejects edit/reply-graph-invalid facts; unchanged decisions keep
  first availability. Projection removes absent/rejected facts but retains
  pending, conditional, or over-five-minute-late facts audit-only. Models use
  `available_at_utc`; reports retain source time.
- Reject malformed envelopes individually; inverted timestamps cannot advance
  checkpoints. Health separates heartbeat, canonical event, and eligible input.
- Private group text stays in bounded staging; authenticated review may render
  transiently. Market Store/projection stay opaque; never
  revive the legacy data plane.
  Live jobs use canonical `main`; retarget systemd before worktree removal.
- Estimator runtime state lives under
  `/srv/trading-bot/production-data/coin-intelligence/estimator-live`. Home shows
  primary CASH/TOMORROW inputs; shadow/realised detail stays on `/shadow`.
- Web UI is the parser/estimator contract: list normalized events, model/audit
  status, recorded main/shadow prices/times—never recompute. Parser review is a
  dashboard link, first on analytics, with actions by status.
- Reviews use opaque keys/reviewer digests; raw text/identity never persist.
  Each revision corrects/rejects
  the exact fact next cycle. A number-redacted syntax digest calibrates matching
  later grammar; price anchors start at review and never affect past live input.
- Input health separates heartbeat from market-hours freshness, rejects stale
  input, prefers normalized sources, and excludes live-only proxies from training.
