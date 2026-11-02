# Native dashboard TLS transport candidate

The default read-only check passed on Native with five Ansible tasks and zero
changes. The corrected second apply and the independent Web-to-Native TLS check
also completed, as recorded below. This packet makes one explicit
transport change to the existing Native dashboard, with check as the default.
It grants no global security acceptance and changes no App/Market image,
Telegram collector, Writer, DNS, IP allowlist or authentication implementation.

The24Oct23:35/23:36 observations established the reviewed login size/deadline
patch under the normal launcher/filesystem contract. A separately authorized
unauthenticated, body-free HEAD from Web to Native's public30486 port at23:47:46
received501 over plain HTTP. This proves that peer's path, not unrestricted
Internet access. UFW was inactive; the final host-filter classification remained
UNKNOWN because the nft input rules were not reduced to a complete policy proof.

The existing nginx include closure was consumed only in RAM. The public
projection at23:54:42 showed `coin.362514.ir:443` serving the App, with no dashboard
proxy. Its public certificate covers `coin.362514.ir`, valid9Oct2026 through
7Jan2027. The existing hostname resolved only to Native's `65.109.216.187` at
25Oct00:01:19. No loaded IP-SAN certificate was established. An IP-literal HTTPS
URL is therefore not claimed to validate. No private key, private Env, auth
header, session, response body or unfiltered nginx configuration was retained.

The proposed canonical URL is `https://coin.362514.ir:30486`, with the existing
dashboard path and normal form authentication unchanged. A new public nginx
server uses the existing certificate pair normally, accepts TLS1.2/1.3, and
redirects ordinary HTTP on this port to the canonical hostname with status308.
The internal497 condition is nginx's
[documented plain-HTTP-on-HTTPS-port handling](https://nginx.org/en/docs/http/ngx_http_ssl_module.html#error_processing).
This redirect is an entrypoint convenience; it is not a claim that credentials
sent deliberately over plaintext could be protected after transmission.
The original Host including port is forwarded because the existing origin
check compares it with Origin/Referer. Only `coin_session_token` receives the
Secure proxy flag; existing HttpOnly/SameSite values and all auth code stay.
Host-only cookies are not transferred from an IP origin to the canonical
hostname; that origin uses the normal existing login. No credentials are migrated.

One public systemd drop-in launches the exact existing `run_live.sh` through
`/usr/bin/env COIN_RATE_ESTIMATOR_HOST=127.0.0.1 COIN_RATE_ESTIMATOR_PORT=30487`.
These argv assignments override already loaded EnvironmentFile values without
reading, copying or hashing that private file. A bare systemd Environment=
assignment would not supply the same override guarantee. The public launcher
SHA remains `fbeb8309dc2d3cb188c4a6b4b94500f76ecb83a52767100c372e38be90c84be2`;
the entire dashboard source remains
`82fc2a7b742fbfa1147d997ca5914e740b58f68489e8bfd17bbb814e46ac86e8`.
No current-main feature update or bridge restart is part of the change.

Use the existing `security_consumers_inventory.yml` Native `security_bot` entry
with `native_dashboard_tls.yml`. The default check reads public source/service/
socket/certificate metadata, validates current DNS and nginx syntax, verifies
30487 is unused and reports the observed public machine ID. Apply requires that
exact machine ID, a fresh32-hex nonce and a new UTC deadline between2 and30
minutes ahead. It never borrows an expired engine/consumer window. It retains
and flocks the existing production-release.lock inode without reading or
changing its payload. It also holds the existing empty immutable-source mutex
on the same inode, without reading/writing its payload or replacing/unlinking
it. A busy, nonempty, linked or changed mutex refuses; an existing controller
reservation still refuses. File presence alone is not an active flock.
Normal commands are bounded; a process alarm also bounds resolver and TLS work.

Apply publishes only the two exact new0644 public config files, recording their
inodes and hashes in a fresh0600 operation receipt. It validates nginx syntax
before interrupting the current dashboard, then reloads unit definitions,
restarts only the dashboard onto loopback30487 and reloads nginx gracefully.
Forward and rollback readiness allow up to45 seconds for an explicitly empty
listener after restart: systemd Type=simple can report active before Python
binds its socket. Foreign binds, launch/configuration drift and certificate
errors fail immediately. No user request or failed check is called successful.
Local verification requires the expected backend PID/bind,
normal certificate-chain/hostname validation, body-free TLS HEAD501 and plain
HTTP redirect308. No authenticated response is requested. Its result remains
`tls_local_verified_external_peer_pending`; root must independently verify the
normal Web-to-Native HTTPS path before any final transport closure.

Failure attempts bounded rollback while retaining the same permanent lock.
Only files whose complete stored identities and exact reviewed public bodies
still match can be removed. Existing or foreign config is never adopted or
deleted. Rollback reloads nginx before restoring the original unit invocation,
then observes the original public listener. The original error still fails the
operation, even after observed recovery. Interrupted/changed ownership or an
unresolved recovery leaves a manual-recovery receipt rather than acceptance.
An explicit later rollback requires the same owned nonce/machine and a fresh
finite rollback deadline. Existing credentials, sessions and data are neither
read diagnostically nor directly changed by the helper. The existing service
performs its normal startup; no session/database bytes enter this controller.
Runtime source memory is not
attested, and the normal login size/deadline evidence remains separate from
this transport proof. Recovery is an observed service/configuration state, never
a daemon-cancellation proof. A killed/interrupted helper leaves its owned receipt
for root to reconcile before later host maintenance; the permanent lock alone
does not prove completion. Actual host check/apply still requires root review.

The first actual apply, using the earlier5a9 helper and nonce
`1613bddf58927dd0f310d3a73e965597`, refused `other_controller_present` before
publishing any file or restarting a service. Read-only metadata established an
existing root0600, single-link, zero-byte source mutex (device2064/inode1835555),
with no flock owner in that observation. This is the persistent protocol used
by `production_deploy_online.sh` and `ImmutableSourceLock`, not a JSON operation
token or evidence of cancellation. The corrected helper preserves and flocks
that inode, rather than deleting it or treating an old file as fresh authority.
Real-FD regressions cover retained bytes/inode, busy ownership, nonempty data,
hardlinks and replacement. The failed attempt remains failed; any new attempt
requires the reviewed corrected source and a new nonce/window.

The second apply used helper `e01a57d1…`, committed in
`9cbe05d6f0fb210cf8c7ad3be694282e78f5d751`, and fresh nonce
`6b660890ba72635bb5ff55345c363b98`. All five Ansible tasks passed, with one
changed task. The local result was `tls_local_verified_external_peer_pending`.
An independent body-free HEAD from Web then verified the normal trust chain and
hostname, TLS501 and plaintext308 to the canonical HTTPS URL. Its public receipt
SHA256 is `34d3107df5156803e5ba448f232b4c6d476f586ddebbd554efb92eec02883002`.

A separate read-only reconciliation verified both exact config bodies and
nine-field file identities, unchanged launcher/source bytes, replacement PID
1743965 and its loopback30487 listener, nginx30486 and the unchanged persistent
source-mutex metadata. The original PID was524703. The actual operation record
SHA256 is `d954ceb343cf94b0c547370565772b6f94f009dae9e5a136af496fb8512257a4`;
the independently fsynced public verification receipt is8633 bytes, SHA256
`d03485a8e8c7a0018f9af58f5c098b0b930424bcc46e055c7a524d43e38171e4`.
Existing collector829565 and Golden973595 still mapped the patched Brotli
module, with no old mapping; the replacement dashboard currently mapped no
target Brotli file. This is a current PID-specific observation, not a future
absence or synthetic module-execution claim. No private Env/session/key/body
diagnostics or additional runtime changes were used. The defined dashboard
transport item is verified; authentication/business flows and global security
acceptance remain separate, and no blanket network-exposure claim is made.
