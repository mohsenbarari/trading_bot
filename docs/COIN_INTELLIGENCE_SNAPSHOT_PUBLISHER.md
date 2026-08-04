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
