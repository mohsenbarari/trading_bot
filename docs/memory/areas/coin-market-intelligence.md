# Coin Market Intelligence

- USD/Herat repair is causal at ingest: reconstruct omitted leading digits from
  three strictly-prior same-source/book facts within 15 minutes; never add a
  fixed constant. Replay chronologically.
- In the bounded private coin-group feed, old `خ ن ف`/`ف ن ف`, current
  `خ ف`/`ف ف`, and marker-less/single-side posts mean tomorrow; `خ ن`/`ف ن`
  and standalone `نق`/cash words mean cash. Explicit future wins. This feed
  default must not be generalized to user offer-registration syntax.
- Coin-group numbers are contextual: separators, glued/bare/word quantities,
  Persian/Arabic letters and mint years must not corrupt price or quantity.
  Payment timing/account terms are conditional.
- Commodity resolution never defaults an omitted coin to Imam and never restores
  `group_commodity_context`. Use strictly-prior same-book anchors no older than
  two hours. In overlapping full-coin bands, every plausible book needs anchor
  coverage or the unnamed fact remains pending. A new regime may bootstrap only
  from a coherent prior 30-minute explicit cluster with at least three messages,
  two senders, one nonconditional claim and 1.5% spread. Conditional named
  claims may support that cluster but are not projected directly. Eligible
  nonconditional facts propagate only forward; only independent canonical
  evidence may reject an explicit name.
- A trade requires one structurally linked reply branch. Isolate users/siblings,
  choose the oldest ancestral offer, prefer attributable owner confirmation,
  use the latest negotiated quantity/price and deduplicate one fill. Keep
  ambiguous/overfilled facts out of the model; unresolved-root trades stay audit-only.
- Reconciliation rejects derived offer/trade facts invalidated by edits or the
  current reply graph. Idempotent unchanged decisions retain first availability.
  Estimator projection is authoritative: remove missing/ineligible prior rows,
  exclude conditional or over-five-minute-late facts, and use `available_at_utc`
  as the compatibility timeline while retaining source event time as metadata.
- Reject malformed group envelopes individually; inverted timestamps must not
  poison siblings or freeze checkpoints. Health separates heartbeat, latest
  canonical event, and actual model-eligible input; historical rows are not live.
- Private group text stays only in bounded external staging. Market Store and
  estimator projection keep opaque normalized facts; never revive the legacy
  parser/data plane. Live jobs execute from canonical `main`; verify and retarget
  every systemd reference before removing a worktree.
- Estimator runtime state lives under
  `/srv/trading-bot/production-data/coin-intelligence/estimator-live`. The home
  page shows only primary output and its exact CASH/TOMORROW inputs; shadow and
  realised outcomes stay on `/shadow`.
- Input health is data-driven: expose heartbeat separately from market-hours-aware
  freshness, exclude stale/invalid inputs, prefer direct normalized sources, and
  label any corroborated live-only proxy as degraded and never training-eligible.
