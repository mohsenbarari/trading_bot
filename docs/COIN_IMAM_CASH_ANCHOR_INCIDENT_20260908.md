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

The user approved the exact, single-service activation.  At
`2026-09-08T09:45Z`, Compose recreated only `coin-estimator` with:

- source revision `f5b98fee4b7916f0aba4f4676c78a2228714957f`;
- image ID
  `sha256:a37ec78ec0977479f534d73d7228b9619362ec36bdb18ce94bebbead8d414b9a`;
- container ID prefix `4ccd4cd2bf58`;
- unchanged data, state, Market Store, model mounts, networks, runtime user,
  read-only root filesystem, and restart policy.

The rollback override is retained mode `0600` under the incident-recovery
directory and binds the prior image ID
`sha256:3c61bb28ab8d6c1b75d7c949c32c11090057e0d1ed7d1c37d5deadededf9139b`
and prior revision `945ccd67110f68d671e2b9d781c84569c7cb06c6`.

## Production proof

- Six authenticated dashboard observations over 155 seconds showed
  `COIN_IMAM:CASH=231000..231050`, continuously `ESTIMATED`, with the
  expected Herat-basis bridge method.  The last observed interval was
  `228000..233300`; snapshot age stayed below five seconds.
- The authenticated operator page rendered Imam cash as `231000` rather than
  the faulty `242500` region.  Its data endpoint identified authority as
  `PRIVATE_PRIMARY`, model `coin-rate-engine-v8`.
- The estimator remained `running/healthy`, exact-image bound, with zero
  restarts for every observation.
- The existing snapshot sender remained `running/healthy`, at container ID
  prefix `c10095776447`, with zero restarts.  The adapter and fact receiver
  likewise retained IDs `9fe6ddd4d22c` and `557b2f48932c`; therefore the
  activation did not recreate peer services.
- The Web/data host received snapshot version `197241`, snapshot ID
  `832607b25b68428a9a229e073ff77fd2964dac41e9bd778239c03a526195789c`,
  and the same Imam cash value and interval as the source.  Its receiver was
  healthy with zero restarts.
- Root-disk usage stayed at 60% (about 15 GiB available).  Docker and market
  data remained on the attached volume, which was 57% used with about 42 GiB
  available.

Product authority and every other service remained unchanged.  The incident
is closed operationally, with the regression tests and exact rollback binding
retained against recurrence.
