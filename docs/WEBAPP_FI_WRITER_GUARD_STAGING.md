# WebApp-FI Writer Guard Staging

This is an admission gate for the existing WebApp-FI writer.  It is not a
deployment procedure and it does not start a service.  Its only remote
connection is the existing Writer Witness HTTPS API used by the lease agent;
it has no WebApp-IR host, SSH, SCP, rsync, or Object Storage transfer path.

## What It Proves

Before the systemd guard can start, the preflight requires all of the
following to match a root-only, operator-reviewed expectation file:

- an immutable release directory named by the exact 40-character release SHA;
- fixed hashes for `main.py` and `core/background_job_authority.py` in that
  release directory;
- the rendered Compose service, container name, local image reference, image
  ID, Docker container ID, Compose project label, and service health for only
  `app` and `sync_worker`;
- `restart: "no"`, `pull_policy: never`, and no `build` for both managed
  services in the rendered Compose configuration;
- an installed systemd unit byte-for-byte rendered from the release template;
- a root-only lease-agent config whose Witness timing exactly equals the
  separately recorded intended Witness timing.

The unit repeats only the immutable/runtime admission check as `ExecStartPre`.
`--phase guard-start` remains an explicit diagnostic for an already-issued
local lease, but it is intentionally not an `ExecStartPre` condition: requiring
an existing lease there would deadlock initial guard startup after a reboot or
lease expiry.  The guard's own lifecycle/fencing logic remains responsible for
the Writer Witness term.  The preflight never creates or starts a container. A
missing, replaced, unhealthy, or stopped container therefore blocks the guard
instead of allowing an unattended Docker or Compose restart.

## Current Status

The current WA-FI runtime is intentionally not eligible for this guard yet:

- it is rooted at mutable `/srv/trading-bot/current` rather than an immutable
  release path;
- the legacy Compose file has `restart: always` for `app` and `sync_worker`;
- the reviewed running image identity must be copied into the root-only
  expectation file only after a fresh local inventory, rather than inherited
  from an old tag or environment example.

Those conditions make preflight fail closed.  Do not install, enable, start,
or bootstrap the guard against that legacy runtime.  A separate, authorized
adoption change must first stage an immutable release and an exact guarded
Compose definition with Docker restart policies disabled.

## Staging Inputs

The templates are intentionally incomplete:

- `deploy/production/production-writer-lease-agent.webapp-fi.json.example`
- `deploy/production/webapp-fi-writer-lease-guard-preflight.json.example`
- `deploy/systemd/trading-bot-production-writer-fi-lease-guard.service.template`

The operator must enter the reviewed current container IDs, local image
references, image IDs, Compose project, and the exact Witness lease duration,
safety margin, and renewal interval into root-only files.  The preflight does
not learn a value from Docker and accept it as trusted input.  When a fresh
Witness policy uses a 60-second term, its corresponding timing must be written
identically to both root-only config files; it is not implied by this template.

After the immutable release, configs, and candidate unit are staged, run only
the read-only stage check:

```bash
python3 /srv/trading-bot-three-site/releases/<release-sha>/scripts/preflight_production_writer_lease_guard.py \
  --config /etc/trading-bot-three-site/webapp-fi-writer-lease-guard-preflight.json \
  --phase stage
```

`status: ready` is necessary but is not authorization to enable or start the
unit.  Starting it, acquiring a Writer Witness lease, and replacing the legacy
runtime are a separate cutover with its own rollback decision.  No file in
this staging path is transferred directly from Finland to Iran; the existing
snapshot/release transport remains Object Storage-only.
