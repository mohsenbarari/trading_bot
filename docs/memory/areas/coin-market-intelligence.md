# Coin Market Intelligence

- USD/Herat repair is causal at ingest: reconstruct omitted leading digits from
  three strictly-prior same-source/book facts within 15 minutes; never add a
  fixed constant. Replay chronologically.
- In the private group feed, `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, and marker-less
  side posts mean tomorrow; `خ ن`/`ف ن` and `نق`/cash words mean cash. Future
  wins. Do not apply this feed default to user registration syntax.
- Coin-group prices may be exact project-thousand values, exact full-Toman
  values divided by 1,000, or a bounded redundant-zero candidate; never add a
  constant. Quantities, scripts and mint years must not corrupt fields. `رب`
  means quarter; `پ`/`ت پ` and `پایین` spelling variants mark low-date.
- Commodity resolution never defaults to Imam or restores `group_commodity_context`.
  Use strictly-prior same-book anchors no older than
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
- Input health separates heartbeat from market-hours freshness, excludes stale
  inputs, prefers normalized sources, and keeps live-only proxies out of training.
