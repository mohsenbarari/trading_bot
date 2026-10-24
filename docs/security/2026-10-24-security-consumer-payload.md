# Security-only App payload

Status: source recipe only. No payload, image, CI result or activation is claimed
by this document. In particular Bookworm SQLite CVE-2025-7458 remains open until
an actual patched candidate passes; `apt upgrade` alone is not that proof.

## Why the deployed product baseline is retained

Both current App images identify baseline
`ba1f7616ade57558f51bb3a7fbb903243a33aab4`, tree
`5d2ff35831696a3886372e4388f2a769a3594a8a`. Publishing newer main history did
not authorize activation of its unrelated Market code. The preparer therefore
uses that entire Git tree and replaces only the 24 literal security paths in
`scripts/prepare_security_consumer_payload.py:OVERLAY`. Those are the compatible
dependency/Docker recipes, upload/static/JWT fixes, credential-safe identity and
WebSocket frontend code, and their security build helpers, including the mandatory
SQLite runtime compatibility verifier. Calendar/UI changes,
all `core/market_intelligence/`, new incident/watchdog scripts, Bot logic,
models, migrations and all other baseline blobs remain unchanged.

The calculation hashes real Git tree entries without writing an index, object,
ref or checkout. It cross-checks the original baseline tree. Source blobs are
size-bounded and streamed through a bounded disk buffer, not a whole in-memory
archive. Only closed public Docker/frontend inputs are copied into a new 0700
workspace; no working-tree caches, `.env`, runtime mounts or credentials enter
it. Existing destinations, links, writable parents, publication drift and wrong
blob bytes refuse. Interrupted output is not accepted or automatically adopted.

## Preparation and evidence

Use a clean checkout of the actual published main, tracking the real GitHub
`origin/main`. Do not stash or overwrite the user's dirty canonical checkout;
prepare from a separate clean clone if necessary. A shared clone's origin must
be the real repository, not a local path. Read-only default:

```sh
python3 -I -B scripts/prepare_security_consumer_payload.py --source-commit FULL_MAIN_SHA
```

Explicit public artifact preparation, with a new absent destination below a
secure private parent:

```sh
python3 -I -B scripts/prepare_security_consumer_payload.py \
  --source-commit FULL_MAIN_SHA --output /ABSOLUTE/PRIVATE/PARENT/new-security-payload
sha256sum /ABSOLUTE/PRIVATE/PARENT/new-security-payload/payload.json
```

Dispatch **Pre-release Gate** on that main commit with `security_payload=true`
and its exact `security_payload_manifest_sha256`. The optional job prepares the
same Git-only context, refuses any digest drift, builds its actual frontend and
uses the existing App Dockerfile. It runs three focused security suites plus the
two exact baseline Market Store/coin-rate suites inside the actual candidate
with no network, no live data and a read-only root; it
does not replace complete API/Bot compatibility, import-boundary or browser
proofs. The current CI job builds the App recipe, not the Iran offline-cache
variant; an Iran-specific candidate still needs its own build/linkage proof.

The optional job first creates the absent checkout-local `.local` parent with
mode 0700. If it already exists, it must be a canonical real directory owned by
the CI user with exactly that mode, below non-writable canonical ancestors.
It never changes permissions or adopts an unsafe existing path. A refusal emits
only a bounded JSON status and static reason code. The preparer likewise exposes
only its explicit allowlisted refusal codes; unexpected exceptions return
`unexpected_failure` without paths, exception text or tracebacks.

Before npm executes, the sealing step records the preparer's SHA-256 in its
completed CI step output, after published-source and caller-manifest checks.
The later build step receives that previously stored digest, reads the helper
with bounded stable-file checks, and compiles that same verified byte buffer.
It never imports a potentially changed helper by reopening its pathname. A
changed, linked, writable or oversized post-npm helper refuses before validation
or Docker execution; synthetic regressions also change the path after verification
to prove that only the already-verified bytes execute.

The five test files are read from exact Git blobs **before npm**: security tests
come from the published recipe commit, and Market tests come from the deployed
`ba1f7616...` baseline rather than newer main behavior. The existing public
`config/unit-test.env.example` is sealed separately as test configuration, never
a live env file. `test-sources.json` records commit, blob ID, size and SHA-256;
its digest is stored in the completed sealing step output. Inside the offline
candidate, the runner validates the complete fixture inventory and every byte
against that proof, then compiles those same verified buffers. It never imports
unverified post-npm test files from the checkout. No skipped or empty suite can
pass. The only pre-fence import is the trusted `magic` native-loader bootstrap;
all application/test imports afterward retain absolute subprocess and network
denial. This avoids libmagic's legitimate `ldconfig` discovery being confused
with an application host command; it does not permit later host commands.

The image has truthful labels: OCI source is the exact
`https://github.com/mohsenbarari/trading_bot`; OCI revision is the deployed product baseline;
release tree is the actual mixed payload tree. Separate
`io.gold-trade.security.overlay.revision/tree` bind the published main recipe,
`security.baseline.tree` binds the old product, and `security.payload.sha256`
binds exact public manifest bytes. A mixed product must never advertise current
main as its complete source revision. Generated dist/dependencies are tested
inside and bound to the resulting immutable image ID in `image-build.json`;
the source-tree identity is not a reproducible-binary claim.

The completed build step publishes that immutable image ID as its CI output.
Candidate tests, Trivy, SBOM generation and export all use that ID, not a tag
that could move. Export streams `docker image save` directly through gzip, with
a five-minute deadline, at most 2 GiB of uncompressed data and at most 2 GiB plus
16 MiB compressed; no whole image is accumulated in Python memory. The private
archive and fsynced `image-export.json` bind its actual SHA-256/size, image ID and
the independently hashed build provenance. Their separate CI artifact lasts
seven days and may be retained even when a vulnerability blocks acceptance.
Its status is explicitly `exported_not_accepted`: existence of an archive is
not a passing security gate or permission to activate it.

Transfer this exact archive to verified attached storage, not a space-constrained
root filesystem. Recheck its bound size/SHA and the loaded image ID before using
it. A local rebuild is a different image and cannot reuse the original image's
test/SBOM/security proof. No registry credentials, runtime configuration, mounted
test fixtures or operational data are included in the image archive.

Trivy v0.76.1 through the immutable
[official v0.36.0 action](https://github.com/aquasecurity/trivy-action/commit/ed142fd0673e97e23eac54620cfb913e5ce36c25)
produces actual image findings and SBOM. Unfixed HIGH/CRITICAL findings remain
visible and fail the job; there is no waiver, fake PASS or automatic promotion.
An open SQLite finding is expected to block until actually fixed. Scanner
download/DB outages fail closed. Public reports have 30-day artifact retention.

## Existing activation gate, unchanged authority

The five-consumer seam still requires a clean published-main control checkout,
all eight actual accepted evidence reports, both patched hosts, backups,
configuration/role/snapshot parity, the held source/operation fences and a valid
fresh maintenance window. Its `payload` acceptance binding contains exactly
`baseline_commit`, `baseline_tree`, `payload_tree`, `manifest_sha256`; the final
reviewer must derive these from the authentic CI/public manifest and bind each
evidence report to the actual candidate image. The worker checks all seven image
provenance labels instead of falsely equating product source with main.

This is artifact preparation, not a new deployment route. Normal main release
and its existing gates are unchanged. No Market producer, legacy collector,
Queue, Writer, DNS, schema migration or runtime configuration is changed.
Old expired acceptance/operation identities are not extended by this recipe.
