# Imam Cash canonical estimate incident — 2026-09-08

## User-visible symptom

The operator dashboard's PRIVATE_PRIMARY rate for `COIN_IMAM:CASH` published
`241800` project-thousand Toman while the contemporaneous Legacy estimate was
about `230500` and the public physical coin reference was about `230700`.
The canonical interval (`238700..244200`) did not cover the current market.

## Confirmed cause

This was an estimator-selection defect, not a stopped capture or transfer lane.
At `2026-09-08T08:56:50Z` the snapshot was current and its underlying physical
melted-gold observation was 83 seconds old.  The coin anchor, however, was
246001 seconds old (about 68 hours).

`_coin_anchors` split the complete seven-day candidate set into all trades and
then all offers.  Consequently, a September 5 confirmed trade at `234500`
masked a September 8 eligible offer at `229000`.  The point-in-time underlying
for that old trade also came from `PUBLIC_PHYSICAL_UNSPECIFIED` at `97550`,
while the current underlying came from `PRIVATE_PHYSICAL_TODAY` around
`100775`.  Transferring the residual across both the time gap and source-basis
change produced approximately `241766`, rounded to the published `241800`.

## Corrective policy

1. A confirmed trade outranks an offer only when it is within five minutes of
   the newest eligible observation for that exact commodity, settlement, and
   physical form.  Outside that neighbourhood, economic recency wins.
2. A non-fallback physical anchor residual is transferred only when the anchor
   and current melted points use the same source kind.  Explicit paper fallback
   bridges remain allowed and continue to expose their lower confidence and
   method metadata.
3. The seven-day retention horizon is unchanged.  The fix changes selection,
   not capture, retention, timestamps, facts, or Product authority.

## Verification before activation

- All 26 focused coin-rate-engine tests pass, including new regressions for a
  days-old trade masking a same-day offer, a nearby confirmed trade retaining
  authority, and incompatible physical source transfer.
- 127 related engine, snapshot-contract, Product-reader, inference, offer-guard,
  and Stage-10 snapshot tests pass.
- A read-only replay against the live Market Store at
  `2026-09-08T09:03:02Z` produced `COIN_IMAM:CASH=230150` with interval
  `228600..232400`, a 4352-second anchor age, and a 39-second current physical
  underlying.  `COIN_IMAM:TOMORROW` remained market-anchored at `231300`.

## Activation status

Code and tests are ready locally.  Production remains unchanged until a scoped
activation replaces the `coin-estimator` runtime and proves snapshot delivery,
dashboard display, rollback identity, peer preservation, and post-activation
stability with real inputs.
