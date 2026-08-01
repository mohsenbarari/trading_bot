# Physical PostgreSQL deployment scaffold — **not runnable**

This directory is a default-off render target for the replacement FI/IR data
plane.  It is not a deployment runbook, a Docker command, a remote-action
authorization, or proof of replication.  Nothing here invokes Docker, SSH,
SCP, PostgreSQL, Arvan, Object Storage, or a remote server.

The governing architecture is
[`docs/THREE_SITE_DATA_PLANE_DECISION.md`](../../docs/THREE_SITE_DATA_PLANE_DECISION.md):

- WA-FI is the normal physical PostgreSQL primary and sole application writer
  under a current Witness term.
- WA-IR is an archive-recovery, hot-standby reader.  It pulls a verified base
  and ordered WAL through its own private/versioned Object Storage adapter;
  no FI-to-IR SSH, SCP, `primary_conninfo`, PostgreSQL streaming connection,
  or cross-host database-control endpoint appears in a rendered config.  The
  default-off scaffold exposes only a container-local Unix socket and an
  explicit deny-by-default standby HBA; any future reader-access surface needs
  its own reviewed, pinned admission boundary.
- Each database directory, WAL/restore spool, adapter-state area, and the
  FI-primary PostgreSQL Unix-socket directory is a separate root-controlled
  external volume.  A renderer rejects every volume reuse.  The socket volume
  is mounted only at `/var/run/postgresql` inside the FI primary; it is local
  substrate for a later digest-pinned helper, not a cross-host channel.
- WA-IR uses `archive_mode = always`, including while it is a standby.  Its
  mandatory reverse WAL-spool and reverse-uploader contracts preserve the
  *posture* required for a later witnessed IR-to-FI failback.  They do not
  promote WA-IR or authorize it to write.

## What the renderer needs

`scripts/render_physical_postgres_deployment.py` accepts only the fixed
root-only manifest location:

```text
/etc/trading-bot/security/physical-postgres/deployment-manifest.json
```

It must be canonical ASCII JSON, root-owned, mode `0600`, non-symlinked, and
inside root-controlled directories.  The manifest binds every generated role
file to one exact release SHA, PostgreSQL 15 image digest, physical base
generation/timeline/LSN/object version, current FI writer term, and
FI→Object-Storage→IR route hash.

The renderer also requires every one of these installed local contracts,
before it will generate any file:

1. WA-FI primary term guard
2. WA-FI WAL spool
3. WA-FI WAL uploader
4. WA-FI writer-ack adapter
5. WA-IR standby bootstrap
6. WA-IR WAL pull agent
7. WA-IR reverse WAL spool
8. WA-IR reverse WAL uploader

For each adapter, the binary is required at its manifest-derived fixed path
under `/opt/trading-bot/physical-postgres/adapters/`, must be root-owned mode
`0755`, and must match the configured binary SHA-256.  A separate root-only,
mode-`0600` installation-attestation file is required under
`/etc/trading-bot/physical-postgres/adapters/`; its SHA-256 is bound too.
Missing, changed, symlinked, unsafe, or incorrectly owned adapters make the
renderer return `blocked` without writing files.

The adapter descriptors deliberately contain no secrets, URL, credential, or
caller-selected command.  Their identity and hash bindings are **not** proof
that a remote replay acknowledgement occurred.

## Profiles and the strict boundary

The only profile that can currently render is an explicitly bounded-RPO archive
profile.  It still needs all eight adapters and its writer-ack contract must
state a nonzero maximum RPO budget.

A manifest may describe `strict_zero_loss` only to bind the future strict
acknowledgement identity and writer-admission integration hash.  This scaffold
then rejects rendering it.  The normal primary template has no native direct
PostgreSQL `remote_apply`/streaming identity (`max_wal_senders = 0`, no slot,
and an empty `synchronous_standby_names`) because that would violate the
pull-only FI→IR topology.  An archive cadence, an Object Storage version, an
adapter descriptor, or a manifest label cannot be called zero loss.

Before strict rendering can exist, a separately reviewed implementation must
provide a live remote durable/replay acknowledgement transport, enforce it at
every FI write acknowledgement boundary, and recheck/consume the Witness term
atomically.  That work remains outside this scaffold.

## Generated posture

On a successfully validated bounded profile, the renderer produces:

- FI `postgresql.conf` with `wal_level = replica`, `archive_mode = on`, an
  exact local WAL-spool handoff, `listen_addresses = ''`, and
  `unix_socket_directories = '/var/run/postgresql'`.  Its distinct socket
  volume is the only prepared helper substrate; no TCP, host port, direct
  replication sender/slot, or cross-host control is rendered.  The generated
  bindings still cover release/base/term/route identity.
- IR `postgresql.conf` with `hot_standby = on`, exact `restore_command` pull
  handoff, `archive_mode = always`, exact reverse-spool handoff,
  `listen_addresses = ''`, a dedicated deny-by-default `pg_hba.conf`, and no
  `primary_conninfo`/slot.  A future reviewed reader boundary is required
  before any TCP read access can be added.
- Profile-gated (`physical-postgres-primary` or
  `physical-postgres-standby`), `restart: "no"` Compose files with no host
  ports, separate external volumes (including the primary socket volume), and
  only fixed root-owned bind paths.
- Adapter descriptors and a `manifest-lock.json`, both explicitly labelled
  `default-off-not-launch-authorized` and `not_a_live_remote_ack_proof`.
- An empty `recovery.signal` seed.  Only the future verified standby-bootstrap
  adapter may copy it into a freshly restored `PGDATA` after proving the
  baseline binding.

The generated Compose files are inputs for a future reviewed root-only
coordinator; they are never executed by this script.  The only launch-related
script currently present, `scripts/guard_physical_postgres_launch.py`, always
returns a nonzero `blocked` result.

## Local-only use

The default command is a read-only check and will normally return `blocked`
until real adapters and their attestations are installed:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B \
  scripts/render_physical_postgres_deployment.py
```

`--render` is intentionally more restrictive: it writes only to a
pre-created, empty, root-owned mode-`0700`
`/etc/trading-bot/physical-postgres/rendered` tree.  It never overwrites a
prior render and never starts it.  There are no CLI switches for an arbitrary
manifest, adapter location, remote host, URL, command, secret, or output path.

Generated developer-local material at
`deploy/physical-postgres/rendered/` is ignored by Git.  It must never be
committed or treated as production evidence.

Focused local tests:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_postgres_deployment_scaffold -v
```

The next mandatory engineering work is the actual signed Object Storage
base/WAL/blob producer, pull/replay receiver, reverse uploader, live
acknowledgement path, term/Witness execution coordinator, and destructive
campaign driver.  Until those exist and pass their own fresh preflight, Full
Matrix remains not runnable.
