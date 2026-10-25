# Retained legacy containers: finite reversible parking

This source packet has not parked or restored any container. Its default mode is
`check`. The next operation requires the actual successful five-target App
handoff receipt, a freshly collected public profile of both hosts, and a new
window lasting at most 900 seconds. No future App container IDs or completed
backup evidence are supplied by this packet.

Execution remains blocked until the new immutable App child candidate has
actually been built, admitted, loaded and activated for all five roles. This
source still pins the old8da/c610 App identities below; those pins must be
updated from real accepted handoff facts, never guessed or treated as current.

The closed target table in
`deploy/maintenance/security_legacy_parking.py` selects six Native staging
containers, four Web staging containers, and the Web `three-site-727c1f337b75`
orphan PostgreSQL container. It retains their container IDs, images, mounts,
networks and data. Parking sets `restart=no`, then stops each exact container.
It performs neither directory cleanup nor image, volume or container removal.
The ten parked QA containers, the stopped migration container, active Market
containers and active descendants of `staging-data` mounts remain outside the
mutation scope. Native staging writers are ordered before their datastores;
Web staging writers precede the Web staging datastores and orphan PostgreSQL.

`legacypark_FINAL`, `security_accepted` and
`clean_stopped_backup_restore_coverage_proven` remain false. Observing exit zero
without OOM or a Docker error is recorded separately from data backup/restore
proof. Fresh clean-stopped backup and restore coverage is still needed before a
later final parking decision. Existing vulnerability findings are not waived.

## Public controls and prerequisites

All input JSON is public, bounded and SHA-bound. Do not collect container Env,
session files, private credentials, raw command lines or application data for
this procedure. Produce the following controls only after the App handoff has
actually completed:

| Control | Required binding |
| --- | --- |
| `controller-result.json` or `app-controller-result.json` | Actual `security_consumer_controller_result/2`, operation `security-consumers-20261024-02`, action `apply`, status `five_targets_verified_global_gates_pending`, confirmed fence cleanup and preserved Native source mutex; global security/raw acceptance flags false. |
| `legacy-profile.json` | `security_legacy_parking_profile/1`; actual App result nonce/source commit; fresh capture no more than 300 seconds before window start; complete Native 15 and Web 27 public runtime rows. |
| `legacy-window.json` | `security_legacy_parking_window/1`, operation `security-legacy-parking-20261025-01`, fresh hex nonce, action `park` or `restore`, UTC start/deadline spanning at most 900 seconds, profile/App SHA pins and exact five source/control pins. |

The profile records each host's machine ID, permanent marker identity, store
root identities/backing UUIDs, observed systemd deployment drivers and public
runtime projection. Each runtime row has the helper's exact twenty public
fields. Native initially has 15 running containers and Web 16; a fresh restore
profile instead starts from Native 9 and Web 11 after the eleven parked
containers have been verified stopped. Native SOURCE is the existing empty
persistent mutex, recorded by its nine metadata fields. Web has no SOURCE
identity and must retain absence of the old C07 source/reservation fences.

The serving App, bot and sync-worker IDs come from the actual post-handoff
profile. They must use the measured candidate images: Native config identity
`8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672`
and Web manifest identity
`c6106a78b1ef830103fddf351635028773eb0efa59281449a3a9f062ec946e7c`.
The ten Web QA IDs must be unique, stopped and `restart=no`. The two sealed
known Market health incidents may fluctuate and are reported open, unwaived;
an unexpected unhealthy running peer refuses the operation. Store identity,
mounts, network configuration and restart intent remain strict.

The window's exact permission map is:

```json
{"restart_policy_only":true,"stop_or_owned_start_only":true,"remove":false,"volumes":false,"images":false,"qa":false,"active_market":false}
```

Its `controls` map pins `script`, `playbook`, `helper`, `inventory` and `config`.
The script seals the normal maintenance primitive before compiling its first
buffer. It reuses only bounded public-command, exclusive-publication and
bounded subprocess primitives; old maintenance/reboot entrypoints are unused.
The existing consumer inventory supplies the established strict Native/Web
access lanes. The **separate**
`deploy/maintenance/security_legacy_parking_ansible.cfg` sets
`[ssh_connection] transfer_method = piped`. The installed Ansible SSH plugin
documents this as SSH with `dd` on either side; its default `smart` setting
would first try SFTP and SCP. Strict host-key checking, batch mode and disabled
connection multiplexing remain in place. Frozen App Ansible controls are not
changed.

## Stage, check and apply

The coordinator must execute published Main source as root. Before executing
the coordinator, its caller must verify the reviewed script and exact input
SHAs. The worker playbook additionally opens the staged source with
`O_NOFOLLOW`, validates ownership, bound, SHA and full descriptor/path identity,
then compiles that exact first buffer with its descriptor retained.

Use the same reviewed public arguments for each phase:

```text
/usr/bin/python3 -I -B deploy/maintenance/security_legacy_parking.py
  --role coordinate --mode MODE
  --window ABSOLUTE/legacy-window.json --window-sha256 WINDOW_SHA
  --profile ABSOLUTE/legacy-profile.json --profile-sha256 PROFILE_SHA
  --app-result ABSOLUTE/controller-result.json --app-sha256 APP_RESULT_SHA
  --primitives /root/trading-bot/trading_bot/scripts/validate_docker_daemon_maintenance.py
```

1. Review the complete actual controls, source pins and target table. Confirm
   the fresh window and explicit narrow permission map; do not extend an old
   deadline or copy an old nonce.
2. Execute `--mode stage` with
   `--confirm PARK-ONLY-ELEVEN-RETAIN-CIDS-DATA-IMAGES-20261025-01`. This creates
   fresh root-only directories named by the new nonce on each host, stages only
   pinned public files and publishes a complete-stage seal. It does not invoke
   a container worker. Existing or partial directories cannot be adopted.
3. Execute the default `--mode check` against the completed stage. It checks
   the full current profile, canonical Native STOP/locks and observed systemd
   driver guards. Check cannot stage missing assets.
4. After reviewing check evidence, execute `--mode apply` with the same explicit
   confirmation within that original window. The coordinator checks both
   hosts before each target, records a durable unknown-outcome journal before
   submitting one target request, validates the fetched public result, and
   checks both hosts again after all eleven results.

Native ownership is permanent M flock, then the existing empty SOURCE flock,
then an exclusively created reservation carrying this operation's context.
Neither permanent inode is created, rewritten, hashed or removed. Native
workers verify the coordinator's actual flocks. A Web target holds its existing
M plus a fresh site guard. It refuses any old C07 source/reservation fence;
those fences are never recreated. Fresh Web collector guards, absence of the
legacy authority token, Native STOP and the observed deployment-driver state
are checked throughout. This is a bounded check of those known mechanisms; it
does not claim that arbitrary external root automation is absent.

## Eight production model readers remain active

The Native `production_readers` profile map must contain exactly the service
and timer pair for each of these four existing projection roles:

- `coin-intelligence-staging-market-input-bridge`
- `coin-intelligence-staging-snapshot-publish`
- `coin-intelligence-staging-snapshot-relay`
- `coin-private-shadow-legacy-estimator-bridge`

Their names are eligibility only. Before constructing a profile, Root must
compare the complete known public unit bodies and every loaded drop-in against
the appropriate versioned definitions, rendered templates/installers and public
source classification. The relay's two units are generated by its versioned
installer. The private bridge can use a historical immutable release: record
its real full source commit and source-file SHA bindings, not a claim that its
runtime code equals current Main. This code validates that Root's fresh,
SHA-bound classification covers the exact closed source paths and states
`production_model_projection`, `legacy_container_driver=false`, reviewed public
source equivalence and absence of credential values. It does not independently
invent that equivalence proof or use a matching name as evidence.

Each binding contains exact loaded `FragmentPath`, all `DropInPaths`, Type,
enablement and trigger relationships, `Transient=no` and `NeedDaemonReload=no`.
Services must remain static oneshots; timers must remain enabled and active.
Every fragment/drop-in is bound to its full nine metadata fields, SHA and
previously admitted public bytes. Both systemd observations and all retained
file descriptors must stay stable. Changed unknown bytes, including an
unexpected Environment directive, are compared in RAM to those known public
bytes **before any actual-byte hashing**, and are neither copied nor logged.
Root must never populate this public profile from an unclassified/private unit
or an EnvironmentFile/session/key. Web's `production_readers` map is empty and
its two existing capture guards retain their original strict checks.

Natural oneshot `MainPID`, `Job` and nonfailed activation/inactivation transitions
are normalized in the public driver projection; failure, loaded policy, source,
drop-in, enablement, timer or trigger changes refuse. Other discovered staging,
deploy, autopilot, legacy, three-site and cutover drivers still require the
original inactive/disabled/fenced conditions. No reader is stopped, disabled,
masked or fenced by this implementation. All eleven exact container stop-only
targets,42 retained container IDs, data and active Market paths stay unchanged.

## Unknown outcomes and later owned restoration

A failed target RPC, timeout, lost client, unexpected runtime change or unknown
reap is not retried. Preserve the nonce's journal, site guard, reservation and
all results. Inspect actual state read-only before a separately reviewed
reconciliation. Do not delete a guard, retry an operation, resume a partial
stage or infer daemon cancellation from a client timeout. A failure before any
target request can release only the exact newly owned reservation/site guard;
foreign or replaced files are left untouched. Final receipt publication
failure reports `known_outcome_receipt_pending` without another mutation or
automatic rollback.

Restoration is a distinct fresh window/profile/nonce with `action=restore` and
`parent_park_sha256` bound to the completed `reversibly_parked` receipt. Supply
`--parent ABSOLUTE/parent-parking.json --parent-sha256 PARENT_SHA` in every
phase. The new profile must show the same eleven retained identities stopped
with `restart=no`; their static configuration must match the owned parking
receipt. Restore reverses the parking order so all five datastores start
before writers, using the original restart policies and retained container
IDs. PostgreSQL `pg_isready` or Redis `PING` readiness is required before
advancing past each restored datastore. This verifies listening readiness,
not business transactions or a data restore. No unknown forward outcome
authorizes this restore path.

## Source validation

`tests/test_security_legacy_parking.py` exercises the exact eleven-target
ordering, dynamic future App IDs, strict public control types, QA preservation,
known health allowance, retained empty SOURCE inode/flock, real guard/journal
replacement refusal, failed-stage cleanup, unknown-RPC retention, per-target
policy/lifecycle ordering, owned datastore restoration, public-only outputs,
first-buffer worker syntax and the dedicated pinned `piped` configuration.
The45 focused cases include genuine active reader pairs, natural oneshot
transitions, missing/forged classification, an enabled real deployment driver,
unit/drop-in/source and loaded-policy drift, retained-FD replacement and
unknown-byte-before-hash refusal. This module requires the genuine-root isolated
CI lane once committed; its earlier nonroot filesystem UID disguise has been
removed. Do not replace that lane with a UID spoof, skipped assertion or waiver.
Run with umask077 and ambient native-command/network boundaries denied before
import. These tests demonstrate the source contract; actual profiles, window,
SSH compatibility, clean shutdown and backup coverage require separate actual
evidence.
The separate completion suite adds exact Web collector guard bytes, public
runtime/store observations, three real owned flocks, all eleven owned restoration
intents, readiness bounds, unknown mutation/client handling, durable receipt
failure and public-only CLI diagnostics. Synthetic fixtures are explicitly inert;
their coverage is not actual shutdown, backup, restore or security-acceptance proof.
