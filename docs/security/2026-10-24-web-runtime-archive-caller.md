# Web audit archive transport caller

This source-only caller revision has not rerun an audit or exported a live
image. The staged Web83 and Native433 invocations and every failed report remain
immutable. Actual replay/export waits until the engine maintenance is complete
and Docker is available; Native waits for Web stability and fresh admission.

The Web worker replaces its hardcoded E46 parser/eligibility with the reviewed
`runtime_image_archive_transport.py`, SHA256
`6a47f4f1352e720c8ea8fee8903e565462180ef04f62149b1e6c42437057b1ae`.
The launcher stages both public sources exclusively in the fresh nonce-owned
tools directory and verifies both source hashes before launch. The worker loads
the parser lazily, verifies a bounded retained source buffer and compiles those
same bytes. No image-supplied code or Config is compiled. Native neither stages
nor loads this module and retains its existing direct Docker-only profile.

Only an ordinary Web image `command_failed` permits one archive attempt for
that observed immutable image. Timeouts, invalid reports, DB drift/expiry,
capacity/deadline faults and source-pin faults grant no additional attempt.
The Docker failure and fallback share the existing600-second image deadline;
global checks run again before starting fallback and before accepting its row.
The first image failure stays recorded if one of those global checks aborts.
If only that image's remaining allowance expires before fallback starts, it
stays incomplete without an archive attempt and the next image gets its own
allowance; healthy global checks are not converted into a batch failure.

Before export, the caller reads only the exact public image Id/RootFS projection.
Normal `docker image save <immutable-ID>` writes into a new exclusive scratch
file through the existing512MiB stream bound. The parser validates that retained
FD's normal OCI/Docker closure, manifest/config hashes and complete ordered layer
DiffIDs, including bounded supported gzip expansion. It neither extracts nor
loads the image. The existing4GiB/CPU1/private scratch limits remain unchanged.

Trivy reads that same rewound FD via `--input /proc/self/fd/<fd>` with the original
unfiltered severities, OS/library scanners, unfixed findings and all update/
telemetry/credential isolation flags. Its untouched raw report must identify the
actual scanner config digest and full RootFS from the archive proof. A summary
keeps the actual Docker ID, scanner ID, transport and proof hashes distinct; raw
IDs and JSON bytes are never rewritten. After the scan the archive is rehashed
and pinned metadata and public image observation are rechecked. Only then is the
archive binding accepted. Its exclusive creation FD is retained from creation
through export, parsing and scan; the existing bounded command helper writes
through a duplicate FD and records client reaping and post-write identity.
A `finally` cleanup checks every client is reaped and the named file exactly
matches that retained owned FD/snapshot before unlinking it, on success and
every known failed export/parser/scan outcome. Raw reports/logs and public proof
hashes stay retained. Failed512MiB exports therefore do not accumulate within
the4GiB worker. Preexisting, foreign, replaced or changed files are not adopted
or removed. Unknown reaping/ownership retains the artifact and stops the batch
with `archive_cleanup_unknown`; it grants no cleanup success or acceptance.

Archive-format/identity failures remain image failures, preserving their original
Docker failure and continuing other images only while global gates hold. An
incomplete image or batch grants no security acceptance, waiver or deployment.
No Native inventory flag is relaxed, no image/API/SSH transport to another host
is added, and no engine/reboot/window authority is changed.

The shared inventory failure path also records bounded closed diagnostics:
which predicate/count failed and only validated public CID/image IDs and parsed
running/restarting/paused booleans. Unknown raw tokens, stdout, Config/Env,
session data and arbitrary exception attributes are excluded. A shape failure
still aborts without retry. Native01's cause remains unknown because its older
immutable worker did not capture that observation.

Web03 retained nine successful image reports and three failures. Bounded,
read-only RAM diagnostics proved F79's ordinary OCI manifest contains six OCI
raw layers followed by two Docker gzip layers. Its162,213,376-byte export has
SHA256 `f2fde4c9106f1111d83b448d798c63f98777efc956eb043e87eb0c057847db43`;
all eight stored hashes, sizes and ordered raw DiffIDs, exact config RootFS,
Docker references, fifteen-member closure and zero tail matched. The old parser
refused its layer media check because it derived allowed types from the outer
manifest family. The decoder now selects from exactly four explicit OCI/Docker
raw/gzip media types for each layer. Unknown media, including zstd, still refuse;
manifest/config/image identity, byte/expansion/deadline bounds and report binding
are unchanged. Regressions cover both cross-family directions and a synthetic
eight-layer/fifteen-member archive using the observed public format metadata.

Image994's294,853,632-byte export has SHA256
`fe6af80b4ce1c325eecedb985987deb042a6593c4873b61f3b42652cd94bd959`.
Bounded RAM diagnosis verified its authenticated OCI index, Linux/amd64 runtime
manifest, config, fourteen ordered runtime layers and separate in-toto
attestation: all sizes/hashes, the attestation subject/reference and exact
twenty-five-member closure matched. The additive index path supports only one
authenticated OCI index level. It requires the observed Docker Os/Architecture
projection and exactly one unambiguous Linux/amd64 runtime descriptor, whose
config platform and complete ordered RootFS must match. The new typed proof
keeps root-index, selected manifest and scanner-config identities distinct.

Every physically present auxiliary manifest, config and bounded in-toto blob
is hash/size checked and included in the exact member closure. The authenticated
index's attestation reference must identify a declared runtime descriptor.
The in-toto blob is opaque data: it is never parsed or executed, selected as a
runtime layer, or used as a provenance admission claim. Its statement version,
predicate and subjects are not filesystem-audit gates. The proof and raw-report
validator require `statement_semantics_validated:false`, `signatures_verified:false`
and `provenance_trusted:false`. Only the seven observed foreign Linux platform tuples
are recognized. Their absent descriptors and exactly referenced foreign
attestations are recorded separately; unknown or selected missing references,
ambiguous/variant targets, deeper indexes, unsupported media and unreferenced
physical members refuse. Present foreign runtime content is unsupported in this
bounded initial path rather than silently included in the selected runtime proof.

The fe PostgreSQL export observed before repair is667,136 bytes, SHA256
`82f7e244a71b33cef25f3a60e5f2a856368b39d766af645692363edf59be0b1e`.
Its authenticated sixteen-child index declares eight runtime/attestation pairs;
the selected Linux/amd64 manifest is present, but its9,091-byte config and all
eleven runtime blobs are absent. This archive still refuses. Restoring exact
public immutable image content is a separate root action, not a parser waiver.
Regressions cover the complete994 shape and the eleven-member incomplete fe
shape with synthetic payloads, plus full caller FD/report/cleanup integration.
The historical nine-of-twelve result stays unchanged; no new scan is claimed.

Root subsequently restored the exact public PostgreSQL index content by digest
without changing its image ID, RootFS or containers. A bounded replay then found
both its SPDX and SLSA in-toto objects contain nine subjects, all naming the same
selected manifest digest. The former single-subject gate was unrelated to the
runtime filesystem proof and refused those bytes. The opaque auxiliary policy
removes that schema dependency while preserving all descriptor hashes/sizes,
index-reference association, exact selected config/ordered RootFS, scanner-config
identity and unfiltered raw finding checks. Regressions include nine subjects,
unknown future semantics and non-JSON bytes as untrusted auxiliary data, plus
wrong attachment/hash/media and runtime-selection failures. Changing opaque bytes
cannot select another runtime or remove/waive any raw finding.
