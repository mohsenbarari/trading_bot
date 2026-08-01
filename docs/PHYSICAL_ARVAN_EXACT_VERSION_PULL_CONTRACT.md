# Physical Arvan exact-version pull contract

`core.physical_arvan_exact_version_pull` is the narrow receiver-side
S3-compatible read adapter for encrypted physical WAL and Blob objects.  It
is deliberately local-testable: importing it neither loads credentials nor
constructs an S3 client, opens a network connection, lists a bucket, reads an
environment variable, invokes `age`, or starts PostgreSQL.

## Required explicit inputs

A future root-owned receiver bootstrap must inject all three inputs:

1. a `RootOwnedArvanExactVersionPullConfig` containing only a canonical
   Arvan endpoint, its matching region, a bucket, a bounded ciphertext size,
   the fixed `gold-trade-physical-arvan-exact-version-pull-v1` schema, and
   the explicit `enabled=True`/pull-only direction flags;
2. a scoped `ArvanExactVersionPullClientFactory`, which receives only that
   already validated canonical endpoint and region; and
3. one or more `ArvanExactVersionPullExpectation` values derived from a
   receiver-verified signed WAL manifest or Blob receiver mapping.

The config has no access key, secret, session token, proxy URL, presigned
URL, client, or credential file field.  The adapter never supplies a default
factory or reads configuration from the environment.  Deployment code is
responsible for installing live scoped S3 credentials and for ensuring the
config/factory are root-owned; those runtime concerns are intentionally
outside this module.

The expected object binds an exact Object `Key`, exact `VersionId`, encrypted
SHA-256 and byte count, and an exact metadata dictionary.  Metadata must at
least bind `encryption=age-v1`, `ciphertext-sha256`, and
`ciphertext-bytes`; it cannot carry URL- or credential-like values.

## Exact GET behaviour

`ArvanExactVersionPullReader.read_exact_to_fd(...)` has the same surface as
`PhysicalWalExactVersionReader` and returns the exact
`PhysicalWalExactVersionReadback` dataclass expected by WAL receiver staging.
Its fixed expectation set means a caller cannot substitute a mutable selector
after construction.

Before calling the injected client factory, it rejects:

- any endpoint other than `https://s3.<region>.arvanstorage.ir` (only one
  optional trailing slash is normalized); endpoint paths, ports, credentials,
  queries, fragments, arbitrary hosts, and region drift are invalid;
- malformed bucket names, boolean-as-integer byte limits, disabled state, or
  a direction other than Object-Storage pull only;
- a non-pinned key/version, `latest`/`current`/`head`/`alias`/`pointer` or
  `null` version aliases, and key components that enable traversal (`.` or
  `..`) or mutable aliases; and
- an invalid destination file descriptor.

The injected S3-shaped client exposes only `get_object(Bucket, Key,
VersionId)`.  The adapter neither has nor calls a listing, head, presign,
redirect, URL, or “latest” API.  It refuses a response with a changed version,
changed optional key, boolean or mismatched content length, different
metadata, non-byte stream chunks, excess/short bytes, or a different streamed
SHA-256.

It also fails closed on visible redirect/location fields, non-200 status,
content/transfer encoding ambiguity, and provider-side encryption/KMS/SSE
fields (including nested HTTP headers).  This prevents a provider-side
encryption response from being mistaken for the application-pinned `age-v1`
ciphertext contract.

The response body is closed on both success and failure.  If a body or local
FD fails mid-stream, a partial destination file can remain; the caller must
discard/quarantine that candidate and never use it as a receipt.  Exceptions
contain only fixed reason codes, never endpoint text, object selectors, SDK
exception text, credentials, or token material.  The success receipt likewise
contains only key, version, ciphertext digest, and ciphertext byte count.

## What it does not prove

An exact GET proves only that the locally written encrypted bytes matched the
already pinned Object version/metadata/hash/size.  It does **not** decrypt
`age`, validate plaintext, recover or replay PostgreSQL, restore Blob files,
prove remote durability, query Witness, change writer term, promote a node,
install S3 credentials, validate live bucket policy/versioning, or orchestrate
the Full Matrix campaign.  Those steps remain separate fail-closed runtime
boundaries.
