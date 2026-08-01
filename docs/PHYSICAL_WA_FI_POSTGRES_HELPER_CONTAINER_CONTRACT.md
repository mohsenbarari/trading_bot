# WA-FI PostgreSQL 15 helper-container contract

`core.physical_wa_fi_postgres_helper_container` is a default-disabled,
root-only *adapter seam*: it does not import Docker, spawn a process, open a
network socket, install PostgreSQL, read a credential, or contact WA-IR.  One
injected runner receives the only possible Docker argv.

The helper image is PostgreSQL 15-bookworm amd64 pinned to the resolved digest
`sha256:fafb7480959eeeb7f1e43b479e642ffef2aa0f067242a1954ab41f2d764e2786`.
The policy, separate installation attestation, rendered manifest lock, local
HBA/role preflight, fixed `/usr/bin/docker` digest, and a separate root-owned
image-runtime identity attestation are all canonical `0600` root-owned files.
The runtime-identity attestation supplies the effective non-root UID/GID;
source code does not hard-code it.

The exact helper form is local only:

```text
/usr/bin/docker --context=default run --pull=never --rm --network=none \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges:true \
  --pids-limit=64 --user=<attested-pg-uid>:<attested-pg-gid> \
  --entrypoint=pg_basebackup --tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m \
  --env=PGPASSFILE=/dev/null \
  --mount type=volume,src=<attested-fi-socket>,dst=/var/run/postgresql,readonly \
  --mount type=bind,src=<fresh-pg-owned-0700-child>,dst=/capture \
  <postgres@sha256:...> --host=/var/run/postgresql --port=5432 \
  --username=physical_backup --no-password --format=tar --wal-method=none \
  --checkpoint=fast --pgdata=/capture
```

There is no tag pull, host PostgreSQL package, TCP publication, host network,
Docker-socket mount, arbitrary environment, password field, remote peer, or
FI-to-IR database control.  `--pull=never` requires a separately installed
and attested local image.

Root creates a fresh private capture parent and one dedicated child.  Only the
child is changed to the attested PostgreSQL UID/GID and mounted into the
non-root helper.  After successful exit, root requires exactly `base.tar`,
atomically moves it into the root-only parent, and normalizes it to
`root:root 0600`; no existing root staging permission is relaxed.

The rendered PostgreSQL policy permits exactly one local WAL sender:
`max_wal_senders = 1`; remains `listen_addresses = ''`; uses the shared Unix
socket with `postgres` group/`0770` socket permissions; and has no Compose TCP
publication.  `pg_hba.conf` allows only local peer authentication for
non-superuser `physical_backup`, mapped from the image-attested `postgres` OS
identity; all other local identities and IPv4/IPv6 TCP HBA paths are rejected.
The generated role/auth descriptor is preflight-only: an installer must prove
`LOGIN + REPLICATION`, no superuser/role/database creation/BYPASSRLS/inherit,
no password authentication, actual socket ownership/mode, and actual image
UID/GID before enablement.  It cannot create the role or authorize launch.

This helper is not yet wired into the older host-`pg_basebackup` capture
boundary.  A reviewed bridge must replace that legacy host-binary identity,
preserve the atomic collection invariant, and bind its completion evidence to
this helper invocation.  It must not add TCP, a host package, or direct
FI-to-IR control.
