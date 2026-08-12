# Coin Intelligence — Explicit Shadow Snapshot Publisher

`publish_rate_ready_snapshot()` is the single local boundary that turns an
existing Market Store into the atomic Snapshot consumed by the shadow preview.
It is not a worker and registers no scheduler, lifespan hook, collector,
network client, or deployment path.

The publisher opens the SQLite store using `mode=ro`, verifies the exact schema
and contract version without applying upgrades, builds a point-in-time Snapshot,
and atomically replaces the target only when at least one canonical coin rate is
estimated. Empty or unready evidence returns `NOT_RATE_READY`; it never
overwrites the last valid artifact. Missing or invalid stores, build failures,
and atomic-write failures fail closed.

An operational owner may invoke this library after its protected volume paths,
single-writer ownership, health reporting, retention policy, and replay gate are
approved. None of those runtime actions are enabled by this change.

## Guarded manual command for staging

`scripts/publish_coin_intelligence_snapshot.py` is the only executable wrapper
for the library. It starts no daemon and accepts only a `Market Store` and a
Snapshot target located inside the same pre-existing protected runtime root.
It never creates the root, the Store, or an absent Snapshot directory.

```bash
python scripts/publish_coin_intelligence_snapshot.py publish \
  --runtime-root /app/coin_intelligence \
  --market-store market/market.sqlite3 \
  --snapshot snapshots/coin-rates.json

python scripts/publish_coin_intelligence_snapshot.py check \
  --runtime-root /app/coin_intelligence \
  --snapshot snapshots/coin-rates.json \
  --maximum-age-seconds 120
```

The command prints one privacy-safe JSON line. `PUBLISHED` and `FRESH` exit
with `0`; unavailable, stale, or not-rate-ready evidence exits with `3`;
misconfiguration exits with `2`; a concurrent writer exits with `75`.

Before using it in staging, the deployment owner must mount the same protected
root read-only for API/Bot consumers and read-write only for the market writer
and publisher. The publisher holds a non-blocking lock beside the target
Snapshot, so parallel publishers cannot race. This command does not add a
collector, cron, container profile, runtime setting, or feature-flag change.
