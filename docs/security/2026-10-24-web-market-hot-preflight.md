# Real Market hot-backup diagnostic before further downtime

Source-only packet; no operation or successful backup is claimed here.

`web_market_hot_preflight.yml` defaults to staging/checking only. It stages
exactly two public scripts from published commit
`43ae1eae96b9bb54b0aaffae53aafcd473c89670` into a new private directory:
`/root/.local/security-market-hot-preflight-20261024-01/source/scripts`.
The backup script and release validator are verified against literal reviewed
SHA-256 values before and after staging. Existing partial/drifted staging or an
old attempt is refused. A real bounded `--help` invocation tests CLI import and
argument parsing without reading the operational env or invoking Docker.
Ansible check mode performs neither staging nor a backup.

Explicit `market_hot_action=hot` requires confirmation
`RUN-REAL-MARKET-HOT-PREFLIGHT-20261024-01`. A single durable unit invokes the
**real** backup CLI `create --allow-running-writers --bluegreen-source-env`
with its HOT confirmation. It uses the original C07 env path and
image/release/tree/input-signature anchors. The new
backup target must be absent:
`/srv/trading-bot/backups/security-market-hot-preflight-20261024-01`.

The wrapper never reads, copies or hashes env/key contents. The existing
trusted backup implementation consumes its inputs internally and performs its
normal release validation, consistent dump, isolated labelled restore and
cleanup. This is not a replacement for that implementation or a custom env
diagnostic. Its normal receipt remains private; console output contains only
filtered static pass/refusal codes. A source-validation error now produces its
stable real CLI code, preserving the reason previously hidden by stderr loss.

The same three original C07 marker/source/reservation inodes are flocked for
the whole operation. The exact failed owned-intent PID, terminal status 3,
original plan/nonce/journal and linked audit are proved. Existing fence PIDs,
payloads and original audits are **not** rewritten or adopted. The sealed C07
bundle is untouched. Source/role/DNS/Writer/packages/containers and service
intent are not changed by this diagnostic; only the trusted CLI's newly owned
isolated restore resources are created and cleaned up.

Before an actual backup, root free space must be at least **44 GiB**. The new
backup location must use the verified root filesystem, and PostgreSQL's numeric
database-size metadata must fit a 16 GiB restore budget. Child regular-file
output is limited to **16 GiB**, diagnostic output is bounded while streaming
at **2 MiB**, and execution is capped at **30 minutes**, including a cleanup
reserve. Failure retains its own attempt/backup evidence and refuses automatic
reuse; it does not remove another transaction's fences or claim cleanup after
an unverified interruption. Killing a timed-out CLI process cannot by itself
remove Docker-daemon-owned isolated restore resources. Failure therefore sets
`completion_unverified=true` and requires proof of the exact backup journal's
owned restore-resource cleanup and absence of active backup descendants before
any further downtime; no automatic guessed-name cleanup is included.

The current ordinary `verify` command requires quiesced inventory parity,
whereas HOT `create` explicitly allows concurrent row/sequence growth. This
packet therefore performs **HOT CREATE ONLY**: that real operation already
includes isolated restore-smoke verification and cleanup. It does not invoke
or weaken ordinary `verify`. Success is explicitly
`hot_create_restore_smoke_passed` with
`quiesced_upgrade_backup_accepted=false`. Early env/CLI failures retain their
real static codes, which is the immediate diagnostic purpose.

This is an explicitly reviewed HOT diagnostic with running application
writers. It does not stop production, resume C07, waive its failed health gate,
extend its maintenance deadline, release its retained fences, or authorize a
new downtime/engine/kernel operation. A hot backup's passing receipt cannot be
substituted for the quiesced proof required by a later upgrade transaction.
