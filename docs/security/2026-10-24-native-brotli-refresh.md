# Native Brotli: bounded consumer refresh

The original source-only packet was subsequently executed as described below;
source publication alone never grants a fresh outage window.
Native stage02/install01 actually installed only the two patched Brotli packages.
The immutable installation receipt remains
`on_disk_patched_consumers_pending`; this helper never changes that receipt.
The previous maintenance window expired. Activation requires a newly approved
concrete UTC start/end, at most ten minutes, passed explicitly to this helper.

The fixed units are `coin-public-market-telegram.service` and `golden_bot.service`.
The discovery probe found MainPIDs524702/524800, both active/enabled with
KillMode=control-group and Restart=always. These PIDs are observations, not future
authority: every attempt records fresh MainPID, start ticks, policies and mappings.
Native Docker's retained fifteen containers, their images/mounts/restart policies,
installed packages, role/configuration, Writer/DNS and protected data are outside
the action scope.

Actual read-only preflight passed on24Oct: both units still require refresh,
no unknown old Native consumer was found, the fifteen containers match the
installation receipt, and the existing Golden DB passed mode=ro quick_check.
Both current PIDs have the exact public startup marker; the collector's public
heartbeat also passed the source-defined HEALTHY/connected/freshness rule.
These are pre-refresh baseline observations. They do not satisfy the future
replacement-PID/post-request readiness checks or authorize any restart.

`brotli_native_refresh.yml` defaults to `check`, including Ansible check mode.
The Python helper imports exactly the SHA-bound reviewed installer buffer
`1fbcadd259911575c1538e44e9336a34aebf822f5736af9863254f8f53ad4625`.
It reuses that helper's native-host/package/receipt checks, safe bounded command
runner, existing marker/source locks and atomic private receipt writes.

Before any action, require both NEW package versions and fresh installed API,
the real installation receipt/boot, the unchanged fifteen-container inventory,
loaded active units and no pending unit reload. Four current public code-file
identities are resolved: system Python's `_brotli` module plus decoder, encoder
and common libraries. A bounded /proc scan examines all visible process maps.
Only target code paths, device/inode and start ticks survive the RAM buffer.
No Env, command line, Telegram session, raw log, account row or database digest
is read into evidence. A same-named library in another container root is kept
outside the Native package scope by checking the public file metadata in that
process's root. Unknown old Native consumers or incomplete scans refuse.

The chosen window is clamped to both UTC and a fixed monotonic end. It cannot
be extended by a clock rollback. Current systemd stop/start timeout budgets,
stability observation and scan allowance must fit before action. With the
discovered90-second start timeouts and30/90-second stop timeouts, reserve a new
eight-to-ten-minute window; a shorter insufficient window refuses before writes.

After acquiring the same existing M (at most five seconds) and legacy source
flocks, repeat package/consumer/container/target-file checks. The marker's inode
and bytes remain unchanged. Only then create one private refresh journal under
`/srv/trading-bot/backups/security-brotli-native-refresh-20261024-01`.

For each required unit, persist `restart_requested` before issuing **one**
`systemctl --no-block restart <fixed-unit>`. Check the untouched pre-action PID
and policies immediately before that request. Polling cannot enqueue a retry.
The next unit waits until the previous replacement has a different stable PID,
the original process is ended, its patched module mapping matches the real
new on-disk inode and there are no old target mappings. Ten seconds of stable
PID/start ticks/restart count and legitimate fresh application readiness are
required. No package downgrade or container restart is performed.

## Existing application readiness signals

The collector's canonical source emits `telegram_live_ready` only after
authorization, entity discovery and initial backfill. Its public heartbeat is
`/srv/trading-bot/production-data/coin-intelligence/estimator-live/public-market/public-telegram-health.json`.
The source entry PUBLIC_MARKET_TELEGRAM must be HEALTHY, connected, have a
positive channel count and a successful heartbeat newer than the request and
at most45 seconds old. The ready journal record must belong to the replacement
PID and have a monotonic timestamp after the request. No new market message is
required during a quiet market.

Golden's public source uses Python Telegram Bot `ApplicationBuilder.run_polling`,
with the existing logging formatter. A read-only, server-filtered journal probe
confirmed the exact public `telegram.ext.Application - INFO - Application started`
record for the current PID. Post-refresh this same ready record must belong to
the new PID and follow the request. Server filtering excludes unrelated HTTP
logs, which can contain Telegram token URLs. Raw journal messages stay in RAM.

Golden's public `db.py` defines the existing `/root/golden_bot/trades.db`.
Readiness also requires SQLite URI `mode=ro`, query_only and a bounded quick_check
of that existing database. It cannot create a missing DB, import the app, select
accounting rows or make business writes. This proves startup/polling and existing
database integrity; it does not perform live accounting transactions.

## Failure and completion

After each replacement and at the end, preserve exact policies and consumer
identity, repeat the broad old-inode scan, require fresh application signals,
verify the fifteen containers and installed receipt unchanged, and preserve M.
Only then may the separate journal say `native_brotli_consumers_verified`.
Its component field `native_brotli_runtime_verified` becomes true. Global
`security_transition_completed` stays false while other security work is open.

A failure/timeout/interruption remains `partial_refresh_incident_open` or the
pre-request journal state if the controller was killed. Already accepted changes
are recorded. There is no restart loop or blind package rollback. A submitted
systemd job can outlive its client; an incomplete journal must be inspected
read-only before any separately reviewed recovery/window. The old two debs and
installation rollback proof remain retained, but automatic downgrades would
reintroduce the defect and are outside this refresh. All new public control
evidence has a ten-day cleanup-review deadline; nothing deletes live data.

## Actual first attempt and Golden-only continuation

The user approved24Oct11:05:14–11:15:14 UTC. Exactly one restart request was
issued for the public collector at11:05:41 UTC; Golden was never requested.
The collector stayed active but remained STARTING longer than the original
bounded observer, so the controller stopped with `partial_refresh_incident_open`.
That historical receipt SHA-256 is
`5955ba42d551f85bfdd1d4637240b7e06c68d34e1a4956cd7cc42b6adce441c7`
and must never be rewritten as a successful first attempt.

By11:13:10 UTC, a separate read-only observation proved old PID524702 ended,
new PID829565 mapped the patched code, and the replacement-PID startup marker
and fresh HEALTHY heartbeat were both present. All15 container identities
still matched the installation receipt. Golden PID524800 remained unchanged
and still needs refresh. High collector CPU and completed backfill events were
observed during startup; no collector-code/database change or restart retry
was made. The remaining time did not fit Golden's full stop/start/scan budget.

`brotli_native_golden_resume.py/.yml` is a separate default-check continuation.
It requires the exact failed-parent receipt, ended old collector, patched/ready
current collector, original unchanged Golden, no pending unit jobs and a new
explicit window no earlier than the previous end. It reuses the SHA-pinned
original refresh/installer, including stable-PID/application/inode checks and
the same M/source locks; only the fixed new journal root is substituted.
Before any Golden request, every journal write records its immutable parent
binding. A command guard permits exactly one Golden restart and denies any
collector restart; out-of-scope writes and repeat attempts refuse. Parent
evidence stays unchanged. No earlier deadline is extended and no Web/container
action is authorized. The new receipt may verify both consumers only after
real post-request readiness, while global security completion stays false.

## Actual Golden-only completion

The user approved a separate24Oct13:22:47–13:32:47 UTC window, without
extending the historical collector attempt. Exactly one Golden restart was
requested at13:23:59 UTC. Old PID524800 ended; new PID973595 demonstrated
fresh application readiness, a patched module mapping and ten seconds of
stable identity. The final process scan found zero old target mappings and
no unknown old Native consumers. Collector PID829565 stayed unchanged with a
fresh HEALTHY/connected heartbeat. All15 container IDs, images, mounts, running
states and restart policies matched the installation receipt. No Web/container/
collector restart was requested.

The separate receipt is
`/srv/trading-bot/backups/security-brotli-native-golden-resume-20261024-02/receipt.json`,
SHA-256 `cb985a4cd62566c7e97e34475375d4fa95926d6b189abeec8303ae9b4f4e3e18`.
It records `native_brotli_consumers_verified`, component runtime verification
true and completion-unverified false. The failed parent receipt still has its
original `5955...41c7` digest; neither it nor the original installation receipt
was rewritten. Global security completion remains false because other image,
backup and Web-host gates are still open. No business transactions were used
as test data and refactor automation remains stopped.

## Current-boot readiness guard for future use

The reviewed helper now restricts journal queries and accepted records to the
validated current boot ID, in addition to the PID, unit and monotonic timestamp.
Missing or foreign boot IDs refuse readiness, preventing an earlier boot's
startup marker from passing after PID reuse. Golden continuation pins the updated
refresh source. This source correction grants no execution window and changes no
historical receipt or execution claim above.
