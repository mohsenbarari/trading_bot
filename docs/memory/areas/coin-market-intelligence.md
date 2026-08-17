# Coin Market Intelligence

- USD/Herat infers omitted leading digits from 3 prior same-book/source facts
  in 15m; no constants or future replay.
- In private groups, `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, or no marker mean tomorrow;
  `خ ن`/`ف ن` and `نق` mean cash. Future wins. Exclude user registration.
- Coin prices accept project-thousand, full-Toman divided once by 1,000, or
  bounded redundant zeros. Quantities/scripts/years are not
  prices. `رب` is quarter; `پ`/`ت پ`/`پایین`/`بالا 80` mark low-date.
- Named offers need no anchors; decisive prior evidence may reject. Unnamed
  offers use causal unit-safe `MAIN_ONLINE` ranges
  plus 2h same-book anchors; ranges cannot contradict. Overlaps need a decisive
  margin. Bootstrap: 30m/3 messages/2 senders/1 nonconditional/1.5% spread.
  Conditional only supports. Never default Imam or restore
  `group_commodity_context`.
- Trades require an isolated full branch, oldest root and reciprocal offerer
  evidence. Cancellation/rejection gates fills. Retain negotiated
  price/quantity and opaque root. Only a reciprocal explicit first fill may
  amend root quantity; ambiguous or cumulative overfills stay audit-only.
- Trade feedback hashes the full root-to-confirmation branch and cannot rewrite
  root commodity/side/settlement/form/conditional state; mismatches are
  audit-only. With 3 causal same-instrument offers in 5m, prices beyond max(5%,
  6 robust deviations) from median are audit-only; prefer the same settlement,
  falling back to the other physical settlement only when that book is thin.
  Historical anchors apply this scale gate before trade weighting.
- Reconciliation rejects invalid reply graphs; unchanged facts keep first
  availability. Projection removes absent/rejected facts; pending, conditional,
  or >5m-late facts stay audit-only. Models use `available_at_utc`; reports use
  source time.
- Reject malformed envelopes individually; inverted times cannot advance
  checkpoints. Separate heartbeat, event and eligible-input health.
- Private text stays in bounded staging/authenticated review. Market
  Store/projection stay opaque; never revive legacy. Live jobs use
  canonical `main`; retarget systemd before removal.
- Estimator state is under `estimator-live`. Home shows
  CASH/TOMORROW inputs; shadow/realised detail stays on `/shadow`.
- Web UI is the parser/estimator contract: list normalized events, model/audit
  status and recorded prices/times—never recompute. Parser review is first
  on analytics, with actions by status.
- Reviews use opaque keys/digests; raw text/identity never persist. Revisions
  correct exact facts. Number-redacted syntax calibrates later grammar;
  review-time anchors never affect prior input.
- Input health separates heartbeat/freshness, rejects stale/live-only training
  proxies, and prefers normalized sources.
