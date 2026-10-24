# Web boot guards — source only

This packet is not an applied receipt and does not authorize a reboot. It may
run only after actual CE task07 completes, releases its nonce-bound fences and
restores the exact original27 container IDs/images/mounts/restart intent, with
26 running. A retained failed/aborted operation cannot satisfy this admission.
The exact task07 plan SHA is required as an explicit input.

## Fixed scope

Only these four root-owned persistent drop-ins are admitted, under
`/etc/systemd/system/UNIT.d/90-security-20261024-web-boot.conf`:

| Unit | Persisted configuration |
| --- | --- |
| `coin-capture.service` | Same currently active runtime condition and manual-start refusal |
| `market-channel-capture.service` | Same currently active runtime condition and manual-start refusal |
| `docker.service` | Require and assert the real attached primary mount |
| Primary attached `.mount` unit | Use the already verified filesystem UUID, not unstable `/dev/sdb` naming |

The actual attached primary is
`/srv/trading-bot-three-site-staging-data`, UUID
`e7f47458-ffc1-4181-81e1-58c6fc4519a2`. Existing five Docker-managed bind-volume
submounts are validated against that primary; they are not made Docker startup
dependencies. Native declared consumer paths are rechecked against the root
filesystem. A newly introduced attached-data consumer refuses, rather than
silently receiving an incomplete boot policy.

Scenario: an enabled shadow-input timer wants the two disabled legacy native
collectors. Existing conditions under `/run/systemd/system` suppress their
activation now but disappear at reboot. This packet preserves the SAME
`ConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled`
and `RefuseManualStart=yes` under `/etc`. The authority token must remain absent.
No timer, collector, Writer, DNS or capture-owner role is changed.

## Transaction and verification

The source-controlled Ansible play invokes the public helper in one command;
no additional helper is installed on the host. Default/check mode performs
bounded public-metadata checks only. It never reads an env file, private key,
session, database or mount payload.

Explicit setup holds the CURRENT original authority-marker inode's kernel
flock throughout all writes, PID1 daemon-reload and post-verification. The old
marker bytes remain unchanged. No private-primary or two-host operation may
be pending. A single private D-Bus connection pins all relevant units with
non-starting RefUnit references, preventing inactive oneshot garbage collection.
D-Bus path arrays preserve the mount unit's literal escaped name.

Each missing destination is published atomically with NOREPLACE and fsync.
Existing destinations must have exact owned content and0644 mode. Foreign
files, links, unsafe ancestry, changed UUID/device/inode, changed container
identity or a missing effective condition refuse. Only configuration reload
is permitted: there is no service start/restart/enable, package action,
unmount/remount or reboot. An already complete setup is verified without
rewriting files or reloading again.

Postchecks require actual Docker29.9.2, containerd2.4.2 and runc1.5.2; the same
27 containers and all original mount/network/restart intent; loaded persistent
conditions/assertions; and the primary mount still on the same actual filesystem
and inode. A false D-Bus Boolean is rejected, not mistaken for Python's distinct
`False` singleton.

Failure is not acceptance. A partial write leaves exact owned files for
inspection; no automatic evidence deletion or broad rollback occurs. If
daemon-reload is pending, investigate before further action. Reboot remains
blocked until all four effective guards and the separate reboot baseline pass.

## Exact owned cleanup

Cleanup is separate, explicit and only removes exact validated owned files.
Both original runtime collector inhibitors must still exist. After a reboot,
when `/run` copies have vanished, cleanup refuses to remove the only remaining
collector protection. No directory tree, other drop-in, mount or data is pruned.
Required permanent safeguards are not temporary backups subject to ten-day
deletion.

The unit tests are synthetic: they deny every host/network command except
explicit fixture stubs and touch only owned private test files. They are not
proof that the actual host guards are installed.
