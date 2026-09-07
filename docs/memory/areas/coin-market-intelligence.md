# Coin Market Intelligence

- Sep07: processor `cb45aad7`, export=500, is live; backlog/latency and exact-revision research links remain open. Labels improved; private estimates=12/14. Follow-up `921b45a0`: 195 image tests pass, transfer blocked pending explicit image/destination approval. Never bypass or equate liveness with completion. Evidence: `docs/MARKET_PROCESSOR_RECOVERY_20260907.md`.
- Sender `fb95bbde` is live. Replay only retained originals with bound ACKs; never reset checkpoints. Dead letters/backlog degrade health; market silence alone does not. `docs/MARKET_TRANSFER_RECOVERY_20260907.md`.
- Account1 `a6dcd636` is live; retain replay quarantine. Docker liveness validates role/schema/source/status/freshness/PID; strict replay/promotion readiness still exposes quarantine. Never delete/synthesize evidence to pass. `docs/MARKET_CAPTURE_RECOVERY_20260905.md`.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`; future wins.
- Units are project-thousands; normalize zeros/separators and resolve tails by family (`رب`=quarter, `پ/ت پ/پایین`=low-date).
- Named anchors set scale only. Unnamed uses consistent `MAIN_ONLINE` anchors; contradiction or weak evidence abstains.
- Trades use oldest exact/root; quantity markers win; reject ambiguity/overfill. `available_at_utc` governs reconciliation; pending/conditional/late are audit-only.
- Estimator is facts-only `/shadow`; gold/Herat/XAU/G1/G2 feed it, weak evidence abstains. Staging mirrors Iran; snapshots read-only; packs are ×100/`PACK_ONLY`.
- Capture contracts are `market_channel_event/1.0` and `coin_group_event/2.0`; receipts/revisions/replies/allowlist govern. Raw is 3d; Store opaque.
- Parser v10/linker v7; promotion needs version bump+dominance; replay needs raw/Store/`human-feedback-r*`/causal `MAIN_ONLINE`; syntax needs raw, ambiguity=REVIEW.
- Private melted: immutable price/quantity, lifetime=120s, freshness=900s; lower remaining=fill, zero=full, positive close=no-trade, edit≠trade.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2 captures G1/G2. Projection health derives from results, never heartbeats.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
