# Web audit archive transport caller

This source-only caller revision has not rerun an audit or exported a live
image. The staged Web83 and Native433 invocations and every failed report remain
immutable. Actual replay/export waits until the engine maintenance is complete
and Docker is available; Native waits for Web stability and fresh admission.

The Web worker replaces its hardcoded E46 parser/eligibility with the reviewed
`runtime_image_archive_transport.py`, SHA256
`361b18af25db047007696e6bbf918932379c02656c95e18172aba1e6c2d77e51`.
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
