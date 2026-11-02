# Native Bot boot mount gates — finite source-only package

This package adds **eleven owned drop-ins**, not a deployment/reboot framework.
It does not reboot, restart, start, stop, enable or disable services or timers;
it only reloads systemd definitions after explicit setup/cleanup. Writer/DNS,
capture ownership, application code, images, data and original unit files stay
unchanged. Golden Bot, SSH, agents and unrelated units are outside its scope.

## Frozen public scope and reason

`deploy/maintenance/boot_mount_inventory.yml` is the human-readable exact
service → mount map. Its mapping must also match the validator's frozen map:

| Consumer | Required mounted paths |
| --- | --- |
| Docker | production-data, secure-env, staging-data |
| Group projector | production-data, staging-data |
| Legacy estimator bridge | production-data, staging-data |
| Private-gold collector | production-data |
| Public-market collector | production-data |
| Estimator dashboard | production-data |
| Production snapshot relay | production-data |
| Staging market-input bridge | production-data |
| Staging snapshot publisher | production-data |
| Staging snapshot relay | production-data, staging-data |
| Market pipeline watchdog | staging-data, secure-env |

Here `production-data` means `/srv/trading-bot/production-data`, `secure-env`
means `/root/secure-envs/trading-bot`, and `staging-data` means
`/srv/trading-bot/staging-data`. Current unit `ReadWritePaths`/`ReadOnlyPaths`
prove eight native consumers' paths; public `ExecStart` path arguments prove
the additional snapshot relay and watchdog inputs. The validator rechecks this metadata
without reading environment files or values. Timers themselves are not edited.
Only path tokens are extracted from public unit `ExecStart` lines; full command
lines and Environment values are never returned. The sync sampler has no
demonstrated direct attached-data path: its public source defaults to relative
`.env`, and native metadata confirms that path is a nonsymlink on the root
filesystem (no content read). It remains outside this package.

Native read-only inspection proved the three exact mounts live on attached
ext4 UUID `9d19fc8e-f71a-4089-b826-5dddfe83ea28`. Existing secure-env and staging
bind units are enabled and depend on production-data, with sources under
`production-data/host-retained/rootfs-20260906/`. They are not recreated.
The existing backup bind is also persistent but **not a dependency of an
unrelated consumer**; this package does not edit a backup service.

Docker source coverage is checked for **all existing containers, including
stopped ones**, through only `.Mounts[].Source` strings on the local Unix
socket. Neither `.Config.Env`, a full inspect payload nor source-file contents
are retrieved. The source-root union must be covered by the frozen Docker map;
unknown mounted roots fail closed. Only canonical Docker-owned
`docker-data/volumes/<name>/_data` submounts fold into
production-data instead of becoming cyclic Docker-before-Docker dependencies.
The data-root must remain `production-data/docker-data`. Inspect counts and
public mounted-root names are returned; no container secret content is returned.

Important: `/srv/trading-bot/secure/market-data` is a regular directory on root
ext4 UUID `64f6fb02-4fed-4836-a33b-86e8993afdfa`, **not** a separate mount.
Its file names may appear in Docker's public source-path metadata, but the
files are never opened. It must not receive `AssertPathIsMountPoint`, which
would permanently prevent Docker startup. Root filesystem dependencies already
exist. Changes to this filesystem arrangement require a fresh scope review.

## What changes and what does not

Each target receives only
`/etc/systemd/system/<exact-service>.d/90-security-20261023-mount-gates.conf`:

```ini
[Unit]
RequiresMountsFor=<only the frozen consumer paths>
AssertPathIsMountPoint=<first path>
AssertPathIsMountPoint=<next path, if any>
```

`RequiresMountsFor` supplies required ordering; assertions make failure visible
instead of silently skipping a missing mount. The original normal-boot ordering
already waits for mount attempts, but a failed merely-wanted bind need not stop
its consumers. This closes that failure path without changing successful
application behavior. See the [systemd unit documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html).

The validator requires native root on Bot `65.109.216.187`, systemd PID1 and
matching namespaces; it rejects container execution and namespace-inode magic.
Exact findmnt target, UUID, device, bind subdirectory and enabled unit sources
are rechecked, including no unreviewed mount-unit drop-ins/pending reload.
Destination parents/files must be root-owned, non-writable by group/others,
regular where appropriate, single-link and no-follow. An existing owned file
is accepted only with exact reviewed bytes/mode/owner; foreign bytes cause
refusal. Builtin copy uses `force:false`, never overwriting a foreign file.
Only missing directories are created; existing directory modes are not changed.
Serialize this operation with other root editors of these units/drop-in paths.
Public control reads are bounded to 64 KiB and recheck open-FD/path identity and
timestamps after reading. Metadata commands have a 15-second timeout and a
child-enforced 2 MiB file-output limit, capture stderr to `/dev/null`, disable
core dumps and return static failure codes rather than raw command arguments.

## Operator sequence after source approval

Run on native Bot, after the daemon/package transaction has completed and the
local Docker socket is healthy. The default is read-only (including source-path
metadata queries); `--check` also ends before managed writes or daemon reload:

```sh
ansible-playbook -i deploy/maintenance/boot_mount_inventory.yml deploy/maintenance/boot_mount_gates.yml
```

Reviewed setup requires exact explicit action/confirmation:

```sh
ansible-playbook -i deploy/maintenance/boot_mount_inventory.yml deploy/maintenance/boot_mount_gates.yml -e boot_mount_action=setup -e boot_mount_confirmation=BOT-MOUNT-GATES-20261023
```

After copying, `daemon-reload` is followed by real read-only verification that
every expected drop-in is loaded, `NeedDaemonReload=no`, required mount paths
are present, and D-Bus `Asserts` contains the exact positive non-trigger
`AssertPathIsMountPoint` entries. Assertions are **not executed by reload**;
this verifies loaded definitions, not a completed reboot or fault drill.
Record the native result and the all-container source-root/count evidence
before independently authorizing reboot. Do not unmount live data to test it.

Cleanup/rollback removes only an existing exact owned file, leaves directories
and all other drop-ins intact, reloads definitions, and verifies owned references
are gone. Healthy expected mounts remain a prerequisite; there is no force bypass:

```sh
ansible-playbook -i deploy/maintenance/boot_mount_inventory.yml deploy/maintenance/boot_mount_gates.yml -e boot_mount_action=cleanup -e boot_mount_confirmation=BOT-MOUNT-GATES-20261023
```

An interrupted partial setup may be resumed with setup or reverted with cleanup
when prerequisites hold; no original config was overwritten. If an owned file
has changed or mount identities drifted, stop and investigate rather than delete
or rewrite it. Source tests use synthetic public fixtures with no network and
do not execute Ansible/SSH/Docker or access production env/data. Syntax checking
and source tests are not evidence of native apply, service restart or reboot.
