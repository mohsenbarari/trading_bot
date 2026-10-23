# Docker daemon-only security maintenance

This is **not an application release**, authority repair or topology cutover.
It upgrades five pinned native Ubuntu 24.04 packages, restarts the **same**
containers, and preserves the maintenance marker and Bot journal byte-for-byte.
No reboot, container/image recreation, migration, DNS/Writer/capture transfer,
Docker prune, automatic package downgrade or stale-receipt adoption is included.
Kernel and remote Ubuntu host remediation remain separate, unresolved operations.

## Scenario and order

1. Review this source, run hermetic tests and Ansible syntax validation. Record
   the reviewed Git revision and source hashes. Readiness of this host patch is
   independent of unrelated application/browser acceptance; it activates no image.
2. Freeze the complete native container inventory (including stopped containers),
   image IDs, projects, restart policies and mounts. Record exact allowed systemd
   units, unit/drop-in configuration, enablement and active intent. A timer's
   triggered oneshot may alternate inactive/running: do not freeze that transient
   state and do not manually start it during restoration.
3. Require both production backup/restore proofs, local copies of Iran backups,
   Golden SQLite online-backup proof, five retained old `.deb` files, and strict,
   hash/size-verified offhost copies. Freeze receipts; never put database contents,
   passwords, environment files or tokens in the plan or source control.
4. Prepare one private, root-owned manifest below. The default guarded command is
   a **read-only** native preflight: it may hold existing flocks but creates no
   reservation, runs no Ansible transaction and changes no runtime state.
5. Explicit `--apply` acquires the existing release-marker and source flocks,
   atomically publishes a fully written/fsynced/flocked maintenance reservation at
   the existing private-primary mutex, and holds **all three locks throughout the
   fixed Ansible subprocess**, passing only these three locked descriptors to it.
   Their inherited open-file descriptions keep the flocks held even if the guard
   alone receives SIGKILL. Another controller cannot adopt this distinct schema.
   Existing reservations/pending two-host release state refuse the operation.
6. Download only the pinned five-package transaction before downtime. Revalidate
   the entire baseline immediately before the first runtime write. Stop exact
   timers first, then recorded services; disable restart policies on exact captured
   container IDs, gracefully stop prior-running IDs, prove quiescence, and stop
   Docker socket/daemon plus containerd.
7. Install only pinned packages with policy-rc.d=101, no module dependency installer,
   unauthenticated packages, removals, automatic cache refresh or automatic service
   starts. `NEEDRESTART_MODE=l` prevents unrelated needrestart restarts. Start
   containerd/Docker, prove no container resurrection, then start captured DB/Redis
   IDs first. Require real `pg_isready`/`redis-cli ping` and any image health checks.
8. Start only remaining prior-running IDs; require identity/mount parity, installed
   five target versions, actual Docker server29.9.2/containerd server2.4.2, and public
   API200 from its explicitly frozen loopback surface. Only then restore restart policies, previously active daemons
   and timers. Originally stopped IDs remain stopped; oneshots are not double-run.

## Private manifest contract

Schema is `docker_daemon_security/1`; JSON rejects duplicate keys. Root-owned,
single-link, non-symlink manifest and evidence must have no group/world permissions.
The SHA256 passed on the command line binds **the same bytes that are parsed**.
Source/public executable/package bindings may explicitly set `private:false` but
must still be root-owned, single-link and not group/world writable. Source and
receipt parents must not be symlinked or writable by another identity.

| Field | Required meaning |
| --- | --- |
| `operation_id` | Unique public incident label; never reuse a completed/failed ID. |
| `deadline_utc` | UTC `Z` timestamp; all invariant checks refuse after it. Use mutation cutoff00:30Z, retaining the approved01:30Z outer buffer. |
| `machine_id` | Exact native `/etc/machine-id`; root/systemd/native pid/user/mount namespaces and Ubuntu24.04 are mandatory. |
| `source_root`, `source_revision` | Reviewed checkout and exact HEAD, not an application activation instruction. |
| `files` | `{path,size,sha256,private?}` for helper, playbook, inventory, ansible.cfg, exact scoped ansible-playbook executable, marker, journal, snapshot and both evidence receipts. |
| `runtime_snapshot` | Existing `security_runtime_snapshot/1.0` JSON with **all** containers. |
| `backup_receipt` | Combined `status:ok`, roles foreign+iran, four files each, both restore+cleanup passed. Local backup files must still exist with recorded sizes. |
| `backup_file_paths` | Optional mapping of a receipt's original remote path to the already SHA-verified local Iran copy. Do not rewrite the backup receipt. |
| `offhost_receipt` | `security_maintenance_offhost_sources/1.0`, passed, strict host verification, fsync completed, ten verified files including old five packages, Iran pull passed. |
| `rollback_packages` | Exactly five `{name,version,path,size,sha256,private?}` records. Bytes and actual Debian package identity must match. Initial installed versions must equal this frozen old inventory. |
| `managed_project_labels` | Explicit project allowlist matching the entire captured container inventory. Unknown/new/missing containers block. |
| `units` | Exact `{name,kind,enabled,active,fragment_path,fragment_sha256,dropins:[{path,sha256}]}`; kind timer/daemon/oneshot. Timer targets must be recorded; actual Type=oneshot is checked. No wildcard/failed-accounting-unit cleanup. |
| `absent_units` | Exact private-primary runtime units that must remain inactive/failed; independent mutex and two-host-state checks still apply. |
| `docker_root`, `mounts` | Actual DockerRootDir and active directories `{path,device,inode,mount_id}`; minimum coverage includes checkout, secure-envs, `/srv/trading-bot/production-data`, DockerRootDir, backup directory and verified Golden SQLite parent `/root/golden_bot`. `mount_id` is native `findmnt -n -o ID -T PATH` output. Never replace or relocate the active attached-volume bind. |
| `datastore_ids` | Unique, previously running PostgreSQL/Redis IDs; never container names or newly created substitutes. |
| `datastore_probes` | Exactly one per ID: `{id,kind:redis}` or `{id,kind:postgres,user,database}`; public identifiers only, no password or environment reads. |
| `health_urls` | Optional host-loopback public `http://127.0.0.1:PORT/path` URLs, no credentials, queries, redirects or response logging. |
| `health_probes` | Optional `{id,url}` probes inside exact prior-running frozen containers: `/usr/local/bin/python -I -B` uses only stdlib urllib, explicit no-proxy/no-redirect, five-second timeout, status200 only, and never reads/logs response body or runtime environment. At least one host or container probe combined is mandatory. |

The protected paths are hardcoded in the guard. **Do not repair** stale marker or
journal inode data, renew receipts, adopt an old reservation or run inode recovery
as part of this maintenance. The current marker/journal bytes are bound as-is.

The foreign API intentionally rejects a published-host-port request that arrives
from the Docker bridge rather than trusted loopback. Its `/api/config`404 is not
patched or bypassed: bind the exact existing application container and probe its
own `127.0.0.1:8000/api/config`, which returns200. This is container-local health,
not a claim that the host-published endpoint must return200; role/auth guards and
headers remain unchanged.

## Invocation and validation

Use the reviewed checkout, explicit private manifest path and externally reviewed
SHA. Never invoke the playbook directly: it requires the live parent-held fences.
No `HOME` override or global Ansible installation is needed. The guarded parent
clears inherited environment, fixes native Docker socket, Ansible config and
scoped `ANSIBLE_HOME`/`ANSIBLE_LOCAL_TEMP`, and uses `/usr/bin/python3` inventory.

```sh
APP_ENV_FILE=config/unit-test.env.example PYTHONWARNINGS=error \
  python3 -B -m unittest tests.test_docker_daemon_maintenance -v

# Read-only eligibility; replace the two public placeholders deliberately.
/usr/bin/python3 -I -B scripts/validate_docker_daemon_maintenance.py run \
  --manifest /root/secure-envs/trading-bot/maintenance-PLAN.json \
  --manifest-sha256 REVIEWED_MANIFEST_SHA256

# A separate, explicitly reviewed maintenance action, not part of source tests:
# same command plus --apply
```

## Failure and honest completion

Before quiescence, refusal leaves runtime untouched. If a transaction fails but
the daemon responds and every exact ID/image/project/mount remains owned, the
playbook restores captured running IDs, probes DB/Redis/API, and restores policies
and unit intent; it still exits failed. The parent emits `failed_recovered`, never
claims security patched, and only releases its reservation after complete parity.

If Docker is unavailable, identity drift occurs, a health check fails, the window
expires or restoration fails, leave the reservation and an explicit `failed_closed`
receipt. Do not automatically downgrade containerd state, recreate containers or
declare success. Reviewed native recovery is required. Successful completion emits
`completed`; result JSON is private and no-replace at
`release-control/OPERATION.daemon-maintenance-result.json`.

A hard-killed guard cannot write a final receipt or enforce its own timeout.
Ansible still retains inherited locks and repeats the UTC/source/ownership gates;
after it exits, the persistent reservation remains for explicit recovery. A
simultaneous crash of controller and Ansible is not claimed automatically repaired.
Never remove/adopt that reservation solely because its recorded PID has exited;
inherited flock metadata may still show the original owner PID.

This source package itself performs no host maintenance until separately reviewed
and invoked with `--apply`. Kernel reboot remains deliberately unimplemented:
the current controller lives on the host, and external-controller authentication/
postboot ownership must be resolved first.
