# Coin Market Intelligence

- Sep08 Imam CASH: a 68h trade masked today's offer and crossed melted sources. Trade priority≤5m; non-fallback transfer requires same source; retention=7d. `docs/COIN_IMAM_CASH_ANCHOR_INCIDENT_20260908.md`.
- Sep07: processor `921b45a0`, export=500, is live; peers/data preserved. Backlog/latency, one-gram and exact-revision links remain open; estimates=13/14. Liveness≠completion. `docs/MARKET_PROCESSOR_RECOVERY_20260907.md`.
- Sender `fb95bbde` is live. Replay only retained originals with bound ACKs; never reset checkpoints. Dead letters/backlog degrade health; market silence alone does not. `docs/MARKET_TRANSFER_RECOVERY_20260907.md`.
- Account1 `a6dcd636` is live; retain replay quarantine. Docker liveness validates role/schema/source/status/freshness/PID; strict replay/promotion readiness still exposes quarantine. Never delete/synthesize evidence to pass. `docs/MARKET_CAPTURE_RECOVERY_20260905.md`.

- Settlement: future=`خ ن ف/ف ن ف`,`خ ف/ف ف` or absent; cash=`خ ن/ف ن/نق`; future wins.
- Units=project-thousands; normalize zeros/separators; `رب`=quarter and `پ/ت پ/پایین`=low-date.
- Named anchors set scale only. Unnamed uses consistent `MAIN_ONLINE`; contradiction/weak evidence abstains.
- Trades use oldest exact/root; quantity markers win; reject ambiguity/overfill. `available_at_utc` governs reconciliation; pending/conditional/late are audit-only.
- Estimator is facts-only `/shadow`; gold/Herat/XAU/G1/G2 feed it, weak evidence abstains. Staging mirrors Iran; snapshots read-only; packs are ×100/`PACK_ONLY`.
- Capture: `market_channel_event/1.0`/`coin_group_event/2.0`; receipts, revisions, replies and allowlist govern. Raw=3d; Store opaque.
- Parser v10/linker v7; promotion needs version bump+dominance; replay needs raw/Store/`human-feedback-r*`/causal `MAIN_ONLINE`; syntax needs raw, ambiguity=REVIEW.
- Private melted: immutable price/quantity, lifetime=120s, freshness=900s; lower remaining=fill, zero=full, positive close=no-trade, edit≠trade.
- Cutover needs real 7d coin/gold/Herat; predated coins abstain; no synthesis.
- 2026-08-31: 22–23 Tehran uses melted/Herat fresh at 22:00 (`AFTER_CLOSE_HOLD`, +0.4%). Never revive older data; guard stays fail-open.
- Account2 captures G1/G2. Projection health derives from results, never heartbeats.
- Replay digests stream via `(run_id,account)` to avoid `/tmp` overflow. Empty retries require prior nonempty immutable manifest, attempt>1, zero quarantine. Never clear state/backfill.
