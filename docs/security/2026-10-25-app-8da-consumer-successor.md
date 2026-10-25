# Finite App 8da consumer successor

This is a source packet. No transfer, image LOAD, container handoff, migration,
host change or security acceptance was performed by this packet. The old
`security-consumers-20261023-01` window and receipts remain historical and expired.
Global security eligibility remains false and the clean refactor streak remains 0.

The existing six consumer controls now accept only
`security_consumer_window/2`, operation `security-consumers-20261024-02`.
A root-reviewed immutable window, its SHA256, explicit confirmation, published
committed control buffers and the unchanged authority marker are prerequisites.
There are no default dates or generated acceptance credentials. Forward activity
is limited to 90 minutes, with at least 20 minutes between the final activation
cutoff and the forward deadline. A separately purposed recovery envelope is
prearmed in that same immutable document: owned rollback only, at most 30 minutes
after the forward deadline. It never extends forward authority.

The five handoffs are Web App, Web sync worker, Native App, Native Bot, then
Native sync worker. Each Compose command names one service and retains
`--no-deps --no-build --pull never`. Before each handoff, peers may remain on their
verified baseline contracts. The immediate target postcheck must pass before the
next handoff. Final verification requires all five actual candidate roles; its
result is `five_targets_verified_global_gates_pending`, not security acceptance.

The source image is exactly
`sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672`.
The controller recipe revision is independent of the product baseline `ba1f7616…`
and payload overlay `19d4c2da…`. The payload manifest remains `cd2e6457…`.
The complete ordered 32-layer RootFS has compact-JSON SHA256
`6762bf592f90b011d8c356fa58051654234aeff72b33f0d2d825f525c3d4c203`.
Docker history timestamps are not a provenance source.

Artifact admission alone cannot satisfy the loaded-image gate. The existing
Web02 admission receipt SHA256
`515d583a175a1b3fc07ccc59f329053936af741ef240d5f12921e1f7a19144fe`
proved the unchanged ZIP `3424f8f0…` (183,811,369 bytes), the closed 69-member
TAR `7337d473…` (451,747,328 bytes), and manifest `c6106a78…` to config 8da,
without LOAD. No Native ZIP copy is requested or permitted by this controller.

Root must separately supply a filtered `security_consumer_loaded_candidate/2`
record after actual normal LOAD and measurement on **both** hosts. It contains
the common source/config identity, ZIP size/SHA, Web admission receipt SHA,
source TAR size/SHA, RootFS digest and `security_accepted:false`. Its `sites`
mapping contains exactly `bot` and `web`, each with exactly `host`, `port`,
`image_loaded`, `stable_before_after`, `daemon_image_id`,
`configuration_values_disclosed:false` and `binding`. Direct config identity
requires measured daemon ID 8da. A different daemon ID needs the reviewed typed
manifest/index-to-config archive proof, retaining config 8da and every ordered
DiffID. The window's `candidate_source_image_id` stays 8da while `images` records
the measured per-host daemon IDs. Neither equality nor a Web proof standing in
for Native is assumed. Consumer code has no download or LOAD action.

The filtered `image_security` control uses schema
`security_consumer_image_evidence/2` and names the existing small CI evidence ZIP
on Native. It binds image 8da, evidence ZIP `d05da4a9…`, raw report `77e85632…`,
SBOM `5f05e26c…` and composition `8fbcad3c…`, with both acceptance/raw-gate flags
false. The native validator verifies the actual immutable ZIP members in RAM
and reconciles all 44 HIGH row digests with the unchanged
[disposition dossier](2026-10-24-app-8da-serving-role-disposition.md).
It does not require a five-key severity Counter: legitimate omitted zero counts
are not converted into fabricated findings or missing nonzero counts. The raw
report and sparse original export receipt remain unchanged.

Web OP08's completed plan `7d55ccac…`, engine completion `bf805d5d…`, kernel
postboot `caf747e2…` and completion `42331354…` are facts only. The derived
current-profile digest `c8f95e30…` binds 27 containers, 16 running, ten parked,
six datastores and the retained f79 functional repair. The fresh Native public
snapshot `ee9f8fe0…` binds its 15 containers. Only explicitly owned selected App
identities may change in the phase projection. Other IDs, images, restart intent,
mounts and complete freshly captured network policy remain unchanged. Known
Market health incidents stay open. Web data-root device/inode/UUID checks run
again; no engine, reboot, capture, Writer or DNS action is included.

Resolved Compose/Docker configuration and Env are consumed only by trusted
RAM-only readers with anonymous bounded output. They are never printed, copied
into receipts or hashed as values. The measured Native old/new image defaults
differ only in Python version/source checksum; PATH, LANG and GPG metadata agree,
and neither image has SSL certificate environment overrides. Web has the same
five default keys and the known PATH/LANG values; its complete comparison runs
locally after admission. The validator preserves the resolved business Env and
rejects unapproved PATH, Python startup/import or LD overrides. It checks both
configured private PID mode and the actual kernel PID namespace.

The one separately reviewed runtime delta is
`PYTHONDONTWRITEBYTECODE='1'`, merged through the selected owned override. It is a
no-op if already present. Rollback uses the original Compose/Env files and an
image/restart-only override, removing this addition. No test or product body is
changed. Normal imports, TLS trust setup, latency, API readiness and datastore
readiness are checked without user fixtures or application data diagnostics.

The public source probe reads only the 857 fixed Python paths (13,194,027 bytes)
through retained FD-relative no-follow traversal, with CPU/wall/byte/memory
bounds. It imports no application code and never traverses uploads, audit data,
certificates or sessions. Its production caller fixes expected UID to 0. The
filesystem fixture can supply its actual local UID to test the same scanner
without fake stat results. All unexpected public code/interpreter/library/cache
COW changes are refused. Only approved mutable data/temp locations and an
independently checked `/app` directory metadata change are recognized.

Complete mount inventories identify every possible uploads/audit writer.
Retained-FD directory traversal checks root-owned, non-022, non-symlink ancestors
and two matching namespace observations. Symlinks, hardlinks, nonregular entries,
unknown writers or changed identities refuse the operation. File contents and
private child names are neither read nor emitted. Only counts/booleans leave
that validator. This is the conditional five-serving-role ACL case; the legacy
library behavior and installed CLI consumers remain outside a blanket FIXED claim.

The source mutex is an existing permanent empty file, preserved by the normal
deployment protocol. The closed window's `source_mutex` binds its complete
nine-field device/inode/mode/UID/GID/link-count/size/mtime/ctime identity. The
coordinator locks the unchanged marker, opens that exact empty private root-owned
source inode without following symlinks, locks it, then creates only a new
exclusive nonce-bound reservation. Busy, missing, nonempty or changed source
metadata refuses. No path creates, writes, hashes as private content, or unlinks
the permanent source mutex. The read-only check also holds its flock throughout
private configuration validation. The Native worker verifies the reservation's
nonce/source/window context and the same coordinator PID's three real flocks;
it never interprets the empty source mutex as JSON. Known terminal cleanup
validates exact FD/name/payload identity, removes only the new reservation and
fsyncs its parent. The source inode, marker and historical journals remain intact.

After the exact reservation is removed and its parent fsynced, the coordinator
still holds the unchanged marker and source flocks while exclusively writing
`controller-result.json` (or `controller-stage-result.json` for staging). That
public receipt binds the nonce, source, window hash, known outcome, original
failure, `source_mutex_preserved` and `reservation_removed`; all global
acceptance flags remain false.
If final receipt publication fails, the command returns failure with
`known_outcome_final_receipt_pending` and its separate verified outcome. It
preserves the per-target results and journal, performs no further handoff or
rollback, and refuses to adopt/replay an existing controller attempt. A partial
reservation cleanup never publishes a final receipt or removes another fence.

A known completed write followed by a failed postcheck triggers only its owned
rollback, then reverse rollback of earlier verified targets. Recovery still
requires the same nonce, successful-write journal, exact actual CID/image/config
and held fences. Forward expiry does not authorize more forward handoffs; the
explicit recovery envelope permits only those restorations. A timed-out or
malformed/missing-result Docker write is outcome unknown. No cancellation is
inferred from client death/reaping, and no rollback or later mutation follows
that ambiguity. Canonical fences and reports remain for root review and bounded
read-only observation. A worker returning 0 is not target validation success;
the coordinator validates the separate public result. After a known handoff,
retained public-source, PID namespace and writer-boundary checks run before
normal candidate import probes; a failed guard triggers owned recovery before
those probes can execute.

Publication validation compares the exact six consumed control buffers and both
main Compose files with committed Git bytes, current main/tree and the real
published origin. The sole allowed preexisting dirty document is
`docs/memory/areas/coin-market-retention.md`, exact unstaged status ` M`, SHA256
`5fbbf84dfec99a504a048a5c1df81e353417f57cdd7b2d7281c8a3aac28ec4e3`.
Its contents remain untouched. Any other dirty/untracked file, status or byte
drift refuses; this is not a general dirty-checkout exception.

Validation uses real temporary files/flocks and ordinary metadata fixtures,
while global guards deny ambient commands, network, native library loads,
signals and resource changes before imports. Named fixtures cover exact target
order, reverse/expired-forward recovery, unknown outcomes, source publication,
the preserved user document, typed loaded IDs, all-layer order, source FD reads,
metadata-only upload checks, actual PID namespace checks and private-data refusal.
Actual handoffs and the full API/Bot/browser/backup matrix remain separate root
review and execution gates. No scanner exclusion, raw-report rewrite, library-wide
ACL acceptance or new deployment authority is minted by these tests.
