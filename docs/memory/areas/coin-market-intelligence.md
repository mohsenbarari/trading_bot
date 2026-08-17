# Coin Market Intelligence

- USD/Herat restores omitted leading digits from 3 prior same-book/source facts
  per 15m; no constants/future replay.
- In private groups, `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, or no marker mean tomorrow;
  `خ ن`/`ف ن` and `نق` mean cash. Future wins. Exclude user registration.
- Coin prices accept project-thousand, full-Toman /1,000 once, or bounded extra
  zeros; quantities/scripts/years are not prices. `رب` is quarter;
  `پ`/`ت پ`/`پایین`/`بالا 80` mark low-date.
- Named offers need no anchors; decisive prior evidence may reject. Unnamed use
  causal unit-safe `MAIN_ONLINE` ranges plus 2h same-book anchors. Ranges cannot
  contradict; overlaps need a decisive margin. Bootstrap:
  30m/3 messages/2 senders/1 nonconditional/1.5% spread. Conditional only
  supports. Never default Imam or restore `group_commodity_context`.
- Trades require an isolated reply branch, oldest root and reciprocal offerer.
  Cancellation/rejection gates fills. Keep negotiated price/quantity and opaque
  root. Only an explicit reciprocal first fill may amend root quantity;
  ambiguous/cumulative overfills stay audit-only.
- Trade feedback hashes the full root-to-confirmation branch and cannot rewrite
  root commodity/side/settlement/form/conditional state; mismatches are
  audit-only. With 3 causal same-instrument/5m offers, prices beyond max(5%,
  6 robust deviations) are audit-only; prefer same settlement, else the other
  physical settlement only when thin.
  Historical anchors apply this gate pre-weighting.
- Term structure cannot move tomorrow from <=30m consistent evidence
  (>=1 trade or >=3 offers); cap unsupported cash.
- Reconciliation rejects invalid reply graphs; unchanged facts keep first
  availability. Projection removes absent/rejected facts; pending, conditional
  or >5m-late facts stay audit-only. Models use `available_at_utc`; reports use
  source time.
- Reject malformed envelopes separately; inverted times cannot advance
  checkpoints. Health separates heartbeat, event and eligible input.
- Private text stays in bounded staging/authenticated review; Store/projection
  stay opaque. Live jobs use `main`; retarget systemd before removal.
- Estimator state: `estimator-live`; home: CASH/TOMORROW inputs; `/shadow`:
  shadow/realised detail.
- Web UI is the parser/estimator contract: show normalized events, model/audit
  status and recorded prices/times—never recompute. Analytics-first status actions.
- Reviews use opaque keys/digests; never persist raw text/identity. Revisions
  correct facts; number-redacted syntax calibrates later grammar. Review-time
  anchors never affect prior input.
- Input health separates heartbeat/freshness, rejects stale/live-only proxies,
  and prefers normalized sources.
