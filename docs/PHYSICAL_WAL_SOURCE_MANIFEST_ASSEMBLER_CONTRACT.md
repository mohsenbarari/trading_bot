# Physical WAL source-manifest assembler contract

`core.physical_wal_source_manifest_assembler` is a pure, default-off source
boundary for the physical PostgreSQL/Object-Storage plane. It does not read a
path, capture PostgreSQL data, call a database, encrypt, publish, contact
Object Storage, restore, promote, or issue writer authority.

It deliberately splits a new baseline into two non-circular steps. The
adapter that performs I/O must persist the returned immutable bytes/hash at
each hand-off; this module does not claim that it did so.

## 1. Bootstrap the signed base before WAL uploads

`bootstrap_physical_wal_base_backup_manifest` accepts only:

- one bounded canonical base-backup completion record; and
- `PhysicalWalSourceBaseManifestBootstrapBinding`, which pins the route,
  campaign, release, source-held Writer-Witness term projection, baseline
  geometry, destination age recipient, source public key/signer, base-capture
  route hash, and exact SHA-256 of that completion record.

It validates the completion record's complete object descriptor and exact
deterministic base object key, then emits:

- canonical source-signed base-manifest bytes; and
- their SHA-256.

The returned hash is the only baseline hash that a subsequent WAL uploader may
place in a `baseline_manifest_sha256` receipt. This removes the former
circularity where a caller had to precompute a low-level base manifest merely
to create a WAL receipt.

The bootstrap and assembly bindings also pin the canonical Object Storage
namespace. `webapp_fi → webapp_ir` records must use `physical-wal/`, while
`webapp_ir → webapp_fi` failback records must use `physical-failback/`. The
namespace is checked as an explicit record field and again through the exact
derived object key; a cross-direction record fails before a signed source
manifest is emitted.

The bootstrap API is still metadata-only. A real source adapter must durably
record the emitted raw bytes/hash before it enables the WAL archive upload
stage, and use the exact recorded hash in every receipt. Replacing that trusted
static pin is a separate controlled baseline-rotation operation, not an
implicit assembler action.

## 2. Assemble the initial genesis WAL link

`assemble_physical_wal_source_manifest_chain` accepts:

- the exact raw canonical signed base manifest emitted in step 1;
- `PhysicalWalSourceManifestAssemblerBinding`, including an explicit
  `base_backup_manifest_sha256` pin and the expected hashes of the supplied
  WAL upload receipts; and
- the ordered bounded canonical WAL upload receipts.

Before it parses any WAL receipt it checks the raw base hash, re-verifies the
Ed25519 signature against the pinned public key, and matches the whole static
route, term, campaign, release, baseline generation, system identifier,
timeline, WAL geometry, baseline/chain/start LSNs, end LSN, and destination
recipient. A byte-altered, independently rebuilt/predicted, re-signed, or
foreign-route base therefore cannot replace the recorded bootstrap output:
the base hash pin and source-key/lineage checks fail closed.

Only then are WAL receipts accepted. Each one must bind to that exact emitted
base hash, match its own SHA-256 pin and static route/term/baseline, and use an
exact deterministic encrypted Object Storage key. The initial API creates
only the genesis WAL link. It has no caller-supplied mutable current LSN,
ordinal, or predecessor frontier.

## WAL geometry and object binding

Every input record is exact-field, duplicate-free canonical JSON with bounded
bytes and strict scalar types. The assembler rejects foreign campaign, route,
release, recipient, term, schema, mutable object version, object hash, and
object key. Base keys must be the canonical route/timeline
`base-backup/<snapshot-sha>.age` location; WAL keys must be the canonical
route/timeline `<wal-segment-name>/<snapshot-sha>.age` location.

`segment_ordinal` is PostgreSQL's zero-based absolute
`start_lsn / 16 MiB` ordinal. It is not reset per baseline. The initial genesis
link derives its predecessor from `wal_chain_start_lsn / 16 MiB - 1`; `-1` is
valid only for the genesis predecessor of absolute segment `0`. Receipt ranges
must begin at the signed base's chain start, be contiguous, and cover the
base-backup stop LSN.

## Append-only continuation

`append_physical_wal_source_manifest_chain` remains the only continuation
API. Its typed append binding repeats and cross-checks the exact base hash,
pins one raw signed predecessor WAL manifest by SHA-256, re-verifies both
artifacts, and derives the next end-LSN and absolute ordinal only from that
predecessor. It never accepts a mutable caller-provided frontier.

A future durable source cursor/CAS adapter must select the published
predecessor and atomically advance it after publication. This pure boundary
does not read or update such a cursor, publish a manifest, or claim a receiver
has staged, replayed, recovered, or acknowledged it.

## Deliberate blob-frontier gap

Both initial and append results state
`separate-signed-blob-frontier-required`. A signed blob inventory/frontier is
still required; base backup plus WAL continuity is not a complete receiver
bundle and proves neither remote apply nor strict acknowledgement.
