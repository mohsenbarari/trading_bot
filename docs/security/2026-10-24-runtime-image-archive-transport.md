# Same-image archive input for runtime vulnerability collection

Status: source-only validator; not wired, scanned, deployed or accepted.
Web audit02 attempted twelve immutable active-image IDs and remained incomplete:
nine reports completed, two image readers reported an invalid TAR header, and
one failed during image initialization/layer analysis. E46 succeeded directly
on that attempt. The earlier E46-only fallback and both historical audit results
remain unchanged. A scanner transport failure is not absence of vulnerabilities.

The new `runtime_image_archive_transport.py` has no command execution, network,
downloads, extraction, image loading or inspected-code execution. It generalizes
the reviewed archive identity proof to an observed immutable image instead of
hardcoding E46 or asserting that every Docker image ID is its config digest.
It is not a replacement audit controller or arbitrary archive admission service.

Its three data APIs are:

- `observe_image(raw, image_id)`: validate the fixed public inspect projection
  `{"id":...,"rootfs":{"Type":"layers","Layers":[...]}}`. Config, Env,
  runtime mounts and sessions are not inputs. Return only the immutable ID and
  ordered complete RootFS layer digests.
- `validate_archive(stream, size, end, observation)`: borrow the caller's pinned
  descriptor, validate the archive and return public identity/hash evidence.
- `validate_report(raw, proof)`: require the untouched scanner report's config
  ID and ordered RootFS to match that proof; return only the public binding.
  The existing audit still validates report schema/results, all severity rows,
  resource and database contracts. This identity API alone cannot pass a scan.

`may_fallback(code, already_attempted)` admits only the first ordinary
`command_failed` result. A command timeout, database/inventory drift, capacity
failure, resource refusal or report-identity failure cannot qualify. The caller
must retain the original failure and one immutable per-image deadline; an
archive attempt never resets the 600-second work allowance. Existing bounded
child cleanup may follow timeout. The finite outer unit remains authoritative.

The supported normal export is an OCI layout with Docker `manifest.json`, one
manifest/config, and raw or gzip layer blobs. OCI and Docker schema2 media
types are explicit. The Docker ID must equal the exact config digest or exact
manifest digest; the returned kind distinguishes those cases and keeps both
Docker and scanner IDs. Every descriptor size/hash, config digest, Docker
manifest reference, OCI index reference and complete ordered raw-layer digest
must match. A gzip layer's stored digest is checked separately from its
uncompressed DiffID, as required by the [OCI configuration specification](https://github.com/opencontainers/image-spec/blob/v1.1.1/config.md#layer-diffid)
and [layer media types](https://github.com/opencontainers/image-spec/blob/v1.1.1/layer.md#gzip-media-types).
The decoder streams at most 64KiB output per call and checks the same deadline;
aggregate expanded layer bytes, including repeated references, are capped at
512MiB. It never extracts or rewrites the archive. Legitimate repeated layer
references use one physical blob while retaining reference order. Neither
config bytes nor Env values enter the proof.

The closed outer TAR inventory admits only those controls/blobs and optional
zero-sized blob directories. Every physical 512-byte header is checked through
the standard library's header decoder before payload access. Links, extension
records, duplicate/extra members, unknown compression and classic legacy Moby
compatibility metadata are unsupported and refuse. This intentionally narrow
format boundary must be tested against actual current Docker exports before
live wiring; unsupported input stays incomplete. It does not reconstruct or
rewrite an input archive to make a scanner accept it.

Archive bytes are capped at 512MiB, controls at 1MiB, layers/references at 128,
and raw reports at 32MiB. The borrowed reader rejects reads/seeks outside the
advertised interval and checks the common finite deadline before underlying
access. Tail reads are capped at remaining+1, detect growth, require normal zero
padding and check the deadline each iteration. All member payload and whole
archive hashing is bounded and deadline checked. Closing the view never closes
the caller-owned descriptor. Tests use valid tiny public archives/files and
ordinary metadata changes; they run no compiler, package code or subprocess.

The later audit wiring should retain the existing sequence:

1. Check the global frozen database, inventory, capacity, resources and common
   deadline; mark the one fallback attempt before issuing any daemon request.
2. Observe the immutable image's public Id/RootFS projection, then save that
   exact ID through the existing streaming cap into a new exclusive private
   scratch file. Use the existing 512MiB cap, reserve checks and private tmpfs.
3. Reobserve the same facts and pin the archive through the existing stable-file
   context. Validate those retained bytes, rewind, and pass that same descriptor
   as `/proc/self/fd/N` to the existing bounded Trivy `--input` invocation.
4. Keep every existing scanner setting: vulnerability scanner only, all
   severities/unfixed rows, empty config/ignore file, offline scan, no registry
   fallback, frozen DB, parallel 1 and existing process/cgroup/output bounds.
5. Retain the raw report unchanged. Validate its scanner config ID and full
   RootFS, combine the existing all-row summary with the public identity proof,
   rehash/recheck the retained file and reobserve the daemon image. Recheck all
   global contracts before recording the completed report.

Only a successful real report for the same image resolves its collection
failure. A fallback failure remains an unresolved image while the existing
collector may attempt the other images; global integrity/resource failures
still abort. Raw findings, original failure records and all history remain.
Security acceptance, findings waiver, deployment and runtime changes remain
false. Source tests and a successful transport do not close any advisory.
