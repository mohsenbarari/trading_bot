# Coin Market Intelligence

- USD/Herat abbreviation repair is an ingest-time causal decision: use at
  least three strictly-prior observations from the same source/book in a
  bounded 15-minute range, reconstruct omitted leading digits from that range,
  and never add a fixed price constant. Historical replay must be chronological.
- Coin-group settlement supports both generations: old `خ ن ف`/`ف ن ف` and
  current `خ ف`/`ف ف` mean tomorrow; old `خ ن`/`ف ن`, current single `خ`/`ف`,
  and standalone `نق` mean cash. Explicit future delivery wins over `ن`.
- A group trade requires one structurally linked reply branch. Keep users and
  sibling branches isolated, prefer attributable owner confirmation, use the
  latest negotiated quantity/price on that branch, deduplicate declarations
  from one fill, and keep ambiguous/overfilled facts out of model eligibility.
- Do not restore `group_commodity_context` or silently default an omitted coin
  to Imam. Commodity resolution uses strictly-prior same-book Market Store
  anchors and fails closed when context is insufficient or conflicting.
