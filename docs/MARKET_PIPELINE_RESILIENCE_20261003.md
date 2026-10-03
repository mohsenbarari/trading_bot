# Market pipeline receiver stall — 2026-10-03

## Confirmed causes

Capture and parsing continued while the sender accumulated 142,762 deliveries,
with the oldest about 3.5 days old. The receiver emitted fresh heartbeats and
Docker reported healthy, but private-peer and loopback HTTPS requests timed out.
No dead letters explained this interruption.

- Receiver initialization ran `INSERT OR IGNORE … SELECT COUNT(*)` on every
  connection. SQLite evaluates the SELECT even when the insert is ignored,
  rescanning millions of permanent history rows per request.
- TLS wrapped the listening socket. A silent/partial TLS handshake blocked
  the accept loop without a timeout, preventing subsequent valid clients.
- Health inspected a heartbeat, not an authenticated HTTP exchange. Docker
  does not restart a running-but-unresponsive unhealthy container.
- Sender metrics summed attempts over all permanent acknowledged rows after
  every batch. Batch construction also repeatedly hashed and validated each
  growing prefix, making catch-up construction quadratic.

## Corrections

`59e2461d` initializes receiver schema/counters once at boot and never recounts
existing history on reopening. Its bounded TLS server accepts raw sockets and
performs TLS/HTTP in at most 24 workers with a five-second socket timeout. Both
receivers use it; idle clients cannot block the accept loop.

Receiver health performs an actual CA-verified, client-authenticated loopback
HTTPS request and exact server-certificate pinning. The snapshot receiver's
health response identifies its schema (`75db7dc7`). Private peer, HMAC, nonce,
payload, and receipt validation remain unchanged.

Sender metrics inspect only unacknowledged work; `send_attempt_count_scope` is
explicitly `UNACKNOWLEDGED`. Historical retry evidence is not reset. `532a9804`
sizes each serialized delivery once and hashes/validates the final batch, bounded
by the existing 500-item contract and 768-KiB document limit. Small live batches
are sent immediately. Stream sequences remain contiguous; G1/G2 retain priority
and other sources remain fair.

Long-running Market Compose roles and the archive database use `unless-stopped`,
including restart after host reboot. Intentional operator stops remain respected;
migration remains an explicitly non-restarting job.

## Independent recovery supervisor

`scripts/install_market_pipeline_watchdog.py` installs a root-owned systemd
timer on each existing host. It supervises four Bot and six Web roles through
their existing containers; it never creates another Telegram session owner.

- Every 15 seconds check role/schema-bound heartbeats, real receiver mTLS HTTP,
  adapter progress when receiver streams are ahead, and estimator/snapshot sender
  version progress when new work is required.
- Allow 180 seconds of startup grace. A heartbeat or demanded progress stalled
  for 180 seconds is a liveness failure; endpoint failure is independent of a
  fresh heartbeat.
- Require two failed observations; restart at most one role per timer run, with
  a five-minute per-role cooldown. Persist cooldown before restart.
- Acquire the production deployment lock nonblocking and skip during handoff.
  Never overlap deployment or create a new session/store owner.
- Keep the same container, mounts and checkpoints. Never clear queues, fabricate
  prices, rearm poison facts, change Writer/DNS, or start an intentionally stopped
  container. Source silence, parser review and catch-up alone do not restart.

The unit is `market-pipeline-watchdog.timer`. Value-free state/reports live under
the protected Market operations root. Verify `systemctl is-active`, `is-enabled`,
report freshness and per-role reason codes. Green heartbeats alone are not proof
that a model is receiving current data.

## Activation, rollback and verification

`scripts/handoff_market_receiver_resilience.py` supports only the two receivers
and fact sender. It checks the full active Compose chain, immutable release label
and portable image-content digest, mounts, exclusive owner lock, and unchanged
production bystanders. It holds the deployment mutex and retains a journal and
exact rollback chain. Failed activation restores runtime, not database state.

Bot fact receiver runs `59e2461d`; WA-FI snapshot receiver runs `75db7dc7`; sender
runs `532a9804`. Image/script transfer used the existing explicit authorization
and user-confirmed WA-FI identity. Iran, the emergency standby path, application
Writer authority and DNS were not changed.

The first snapshot-receiver attempt rolled back when strengthened health exposed
a missing schema field. The corrected response passed a fresh guarded handoff.
Another initial rollback exposed an ephemeral test-container false positive;
the bystander guard now checks actual Compose services and excludes one-off
tests. Failed journals remain available.

88 receiver/watchdog/transport/foundation/recovery/snapshot tests passed in an
isolated network-disabled container; another 52 covered sender, adapter and
snapshot publication. Coverage includes silent TCP, partial TLS/HTTP, historical
reopening, exact byte limits, sequence gaps, 500-item batches, two-observation
recovery, cooldown persistence, worker-saturation recovery, real HTTP schema
and deployment-lock exclusion.

Closeout checks source → receiver → adapter watermarks, increasing model versions,
fresh WA-FI published views, and authenticated HTTP of the actual dashboard and
activity pages. Diagnostic sessions log out afterward. Print timestamps/statuses/
counts, never credentials, private routes or prices.

Recovery is automatic for detected hang/crash conditions, not a promise of
uninterrupted operation during network/provider outages, unavailable/corrupt
storage, missing credentials, or intentionally stopped services. Financial
freshness and abstention gates remain intact during recovery.
