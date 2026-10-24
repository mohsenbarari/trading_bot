# Native reboot — actual result and remaining security scope

The native host is running `6.8.0-147-generic`. Its owned result recorded
`completed` at **2026-10-23T21:19:23.683099Z (00:49 Tehran,24October)**.
The15 original containers/images were restored, Golden SQLite quick-check was
`ok`, authority bytes remained unchanged, and the reservation was released.
This does not attest new application images or Web maintenance.

After the session reconnected, fresh read-only verification at
`2026-10-24T03:14:22.312420Z` passed:

-15 running original container identities/images, policies and unit intent;
-actual PostgreSQL/Redis probes and container-loopback API HTTP200;
-Golden active and SQLite quick-check `ok`;
-all original UUID/source/inode mount bindings;
-unchanged Market marker/journal bytes, no temporary Golden gate/token or
 maintenance reservation.

The independent continuation unit nevertheless ended with `UNREACHABLE` on
its final Ansible cleanup task, after successful reboot, restore and result
fetch. Do not relabel the unit's aggregate exit status as successful. Both
temporary Web key/pin files and the native managed authorized-key entry were
independently verified absent. Self-revocation followed by Ansible remote
housekeeping is the likely transport explanation, not a separately proved
module trace. Future final cleanup must use controller-local raw SSH so no
later remote housekeeping depends on the revoked credential. Preserve logs.

Canonical main remained `25ac5849eaa36164f4d8f9639dd5e7cb96d3305b` during the
bound operation; the user's unrelated retention-memory bytes remain unchanged.
The previous window expired while the session was disconnected. A new immediate
maximum90-minute window was explicitly approved on24October; the conservative
outer limit is `04:41Z` / `08:11Tehran`. New Web work must have explicit bounded
restore admission rather than merely changing an old deadline.

Still open: actual Web engine/kernel maintenance, built-image SCA and application
activation, native Brotli/old-runtime applicability/remediation, legacy-image
triage and outstanding real CI/browser compatibility checks. Automation remains
durably stopped. Its30 actual clean accepted units have **not** begun.
