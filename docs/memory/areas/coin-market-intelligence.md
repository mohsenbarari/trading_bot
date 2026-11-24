# Coin Market Intelligence

- 2026-11-23 | Web disk exhaustion stopped six Market roles; stale PID-based temporaries blocked restart. Byte-verified OP08 archive relocation preserved its path and freed 10.9 GiB; quarantining 30 empty temporaries restored existing containers/checkpoints. Web snapshot 1459344 OK with both groups fresh; dashboard AVAILABLE. Historical gap remains before Nov 23 05:30 UTC. Unique same-directory temporary patch verified locally, pending release.
- Estimator `f5b98fee`: trade≤5m; non-fallback anchors same-source; retention7d/rollback. Old trades must not mask fresh offers.
- Processor `aeaa3f0c`: numeric poison atomically FILTERED; preserve raw/exact lineage, other contracts fail closed. Fair live/history export with bounded cyclic discovery; keep indexes/timings. `docs/MARKET_PROCESSOR_NUMERIC_ISOLATION_20261018.md` links prior recovery.
- Sender/adapter `532a9804`/`7aa96bdd`: bound ACK, fair streams, trigger counters; no resets. Release-bound endpoint/progress watchdog:15s/two failures/cooldown5m/deploy mutex; silence/catch-up never restart.
- Account1 `a6dcd636`: retain replay quarantine. Liveness validates role/schema/source/status/freshness/PID; readiness exposes quarantine. No deleted/synthetic evidence.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`.
- Units=project-thousands; normalize zeros/separators; `رب`=quarter and `پ/ت پ/پایین`=low-date.
- Named anchors set scale only. Unnamed uses consistent `MAIN_ONLINE`; contradiction/weak evidence abstains.
- Trades use oldest exact/root; quantity markers win; reject ambiguity/overfill. `available_at_utc` governs reconciliation; pending/conditional/late are audit-only.
- Estimator is facts-only `/shadow`; gold/Herat/XAU/G1/G2 feed it, weak evidence abstains. Staging mirrors Iran; snapshots read-only; packs are ×100/`PACK_ONLY`.
- Capture: `market_channel_event/1.0`/`coin_group_event/2.0`; receipts, revisions, replies and allowlist govern. Raw=3d.
- Parser v10/linker v7; promotion needs version bump+dominance; replay needs raw/Store/`human-feedback-r*`/causal `MAIN_ONLINE`; syntax needs raw, ambiguity=REVIEW.
- Private melted: immutable price/quantity, lifetime=120s, freshness=900s; lower remaining=fill, zero=full, positive close=no-trade, edit≠trade.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2=G1/G2. Display `7aa96bdd`=canonical Bot, not legacy/backfill. Conversation lock/gates; indexed causal5m books; timeout120s/timer15s. Bridge same source/version; result-health and transaction/config rollback.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
