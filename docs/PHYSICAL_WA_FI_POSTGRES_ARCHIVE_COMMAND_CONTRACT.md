# WA-FI PostgreSQL archive-command adapter

`core.physical_wa_fi_postgres_archive_command` is the concrete, root-owned
boundary behind the rendered WA-FI primary PostgreSQL contract:

```text
wal-spool --config /etc/trading-bot/physical-postgres/primary/wal-spool.json \
  --wal-file %f --wal-path %p
```

It is default-disabled. Importing the module does nothing. It becomes
effectful only when a separately installed, root-owned archive binary invokes
`execute_wa_fi_postgres_archive_command` with explicit age-encryptor and
S3-client factories. No deployment in this change installs that binary,
creates the runtime config, changes PostgreSQL, or calls the function.

## Fixed invocation and config boundary

The invocation accepts exactly six arguments in this order:

```text
--config <fixed path> --wal-file <24-uppercase-hex segment> --wal-path <path>
```

The config pathname cannot be selected through an environment variable,
alternate CLI form, relative path, symlink, or caller input. It is fixed to
`/etc/trading-bot/physical-postgres/primary/wal-spool.json` and must be a
canonical root-owned, single-link `0600` regular file below root-controlled
ancestors. The parser rejects duplicate/non-canonical JSON and requires a
self-pinned `configuration_sha256` over every other field.

The runtime config is one normal-direction `webapp_fi -> webapp_ir` archive
binding. It contains no credentials, endpoint URL, arbitrary command, or
peer-control field. It pins:

- the physical archive manifest identity and derived route-binding hash;
- a fresh signed Writer Witness proof and pinned Witness public key;
- the exact WAL source root, private spool root, and supported WAL geometry;
- a root-only Object-Storage uploader policy with direct site control
  forbidden and destination ingest pull-only.

The adapter requires root at runtime. A distinct IR-to-FI failback adapter is
not accepted by this WA-FI entrypoint.

## Exact WAL and uploader sequence

1. The fixed CLI shape is parsed before the config is opened.
2. The root-only config, self-pin, normal FI-to-IR binding, fresh signed
   Witness term, and route hash are verified.
3. `%f` must be a canonical PostgreSQL WAL segment name. `%p` must be the
   exact direct child of the configured source root with that same basename;
   its resolved path cannot escape the root or traverse a symlink. A regular,
   single-link file with the configured exact segment size is required before
   an uploader is constructed.
4. Only then does the adapter instantiate the existing
   `PhysicalWalObjectStorageUploader`, passing the explicit age and S3
   factories unchanged, and call the existing `archive_physical_wal_segment`.
5. The existing spool captures the immutable local snapshot, invokes the
   uploader, validates its immutable encrypted Object receipt, and rechecks
   the live signed Witness term after the uploader returns before it writes a
   completed upload manifest.

Invalid CLI/config/self-pin/path/term input cannot construct the uploader or
invoke an age/S3 factory. There is no default factory and no implicit network
client, credential lookup, or environment fallback.

## Redacted status only

Success reporting contains only the WAL segment name, local content hashes,
and Object version ID. It omits source/spool/config paths, bucket, region,
recipient, raw descriptor, credentials, and exception text. Failure reporting
contains only a fixed error code.

## Non-authority boundary

An archive success is encrypted recovery-material publication only. It is not
a PostgreSQL synchronous acknowledgement, remote durable/replay proof, strict
acknowledgement, writer permit, route change, promotion, or Full-Matrix
authorization. WA-IR remains a private Object-Storage pull consumer; this
adapter provides no direct FI-to-IR control path.
