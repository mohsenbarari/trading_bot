# Account1 live-capture incident, 2026-09-05

## Runtime outcome

Live input recovery is applied; deployment of the permanent code fix is pending.
Product remains LEGACY and Queue-v1 is unchanged.

Account1 on wa-fi repeatedly exited with `capture_replay_source_incomplete`.
One unavailable point-in-time historical MELTED_FLOW revision remained quarantined.
The replay completion refusal disconnected all Account1 live subscriptions.
Over 2,580 restarts were observed; a recent live-starting heartbeat incorrectly
allowed Docker health to pass despite the unresolved replay quarantine.

The existing a972e389 capture release was recreated ONLY for Account1 with
`account1-live-recovery-20260905.yml`. This clears the explicit historical backfill
cutoff and source selection for this runtime, retaining normal recent catch-up
and live subscriptions. It does not delete state, quarantine, manifests, session
files, facts, or acknowledge the unfinished historical replay.

Capture started at 07:01:37 UTC. At 07:18:05 UTC it was running, restart count 0,
with a recent heartbeat and capture sequence 2544645. Melted and Herat streams
resumed. XAU had no recent upstream events; it was not artificially refreshed.

Production's actual coin-rates snapshot at 07:17:36 UTC inferred IMAM for both
previously rejected TOMORROW prices, 233000 and 233400, via the existing ranker.
These were read-only inference probes, not published offers or trades. Price
guards, user confirmation, source-quality rules, and estimator authority were
not bypassed.

## Permanent fix prepared, NOT activated

- Code commit: 0c953335.
- Tested image source: 1f972a48ade3037d8455d574d14a598851ef5640.
- Source tree: f3f3ffcdc39bb608fb6448c5b814c155f9c73ce5.
- Image: sha256:3d8c72539565c70b416db01cc7dd9db43f5d776e90c20abf451f766745ccc6c7.
- Image input signature: 362c29d6aade7aff1102177535913528ea1ef87dc225ff6088cf0ed2d22378b3.

Only an incomplete replay COMPLETION is isolated from live capture. Source-stage,
integrity, and storage failures still fail closed. Quarantine keeps readiness
degraded. Replay records are not reset or falsely certified.

The focused capture/foundation/snapshot suite passed 100 tests. After additional
health validation, 18 focused tests passed again. The exact runtime image also
passed those 18 tests in an isolated, network-disabled container. The file-owner
contract fixture requires root; the first non-root test invocation failed that
fixture and was not reported as a passing run.

The immutable image has been loaded on wa-fi but no new image was activated.
The existing production-maintenance record is bound to 4ef8e6dc, while Account1's
authority marker remains a972e389. Do not rewrite or remove that lock, falsely
reuse the old image revision, clear replay quarantine, or start overlapping
Telegram session owners to activate the new code. Next step is a release-bound,
journaled Account1 authority handoff with exact rollback to the recovered runtime.

No Product deployment, Queue-v1 change, authority switch to PRIVATE_PRIMARY,
Git push, or database migration was performed in this incident.
