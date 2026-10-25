# Fixed Market candidates: image stores only

This packet admits the actual E46 export and nine historical-product overlays
into their intended Docker image stores. It stops, creates and restarts no
container. App activation is not a prerequisite. It changes no App controller,
C/S/R or COW policy, Writer/DNS authority, reader unit, database, model or volume.
The separate `market_security_rollout.py` draft remains unapproved and untouched.

The first01 namespace completed public staging only. Its first actual Native
read-only snapshot refused because optional Docker Health metadata was absent;
no controls, attempts, guards or image operations existed. Its staged sources
and failure evidence remain unchanged. The02 namespace uses guarded optional
map access for Health/Healthcheck, preserving null values and F79's mandatory
healthy predicate. Fixed snapshot-command subphases identify future refusals
without exposing raw stderr, configuration or exception arguments.

The02 Native read-only snapshot then passed, but Web observation refused a
distinct `/tmp` tmpfs device. No controls, attempts, guards or image operations
existed. The03 namespace preserves the02 staged sources and models that actual
layout explicitly: temporary-filesystem capacity and conservative host RAM
headroom are additional requirements, rather than weakening any existing floor.

The closed catalog is
[`market_candidate_images.json`](../../deploy/maintenance/market_candidate_images.json),
55511 bytes, SHA256
`c354498c3659fdf99613753d950416f134d81e0fe61fbdc80a32e7eec29b8d12`.
It binds official GitHub metadata for runs40145507410 and40151822883, all ten
Config identities and ordered18/19-layer lists, exact image ZIP identifiers,
sizes/digests, separate public evidence ZIPs, export receipts and raw reports.
Evidence artifact identifiers are never substituted for image ZIP identifiers.
The preserved first collection has SHA256
`c63daee1e71df0cfc521b22fc59bbc51efbaa01f4ebe3371e3f95f90b7219717`.
The second, `b4b48af8be9005ccb10d023bd172b2086a6c6d517a513cb2feb89813ff848f57`,
adds nine already-recorded TAR/export-scope hashes. The third binds the reviewed
central metadata-projection correction. E46 has no such
public TAR-hash receipt: its TAR digest is measured during admission, after the
exact ZIP/gzip/size and physical graph have authenticated its bytes. No future
Config, manifest or index identity is invented.

The pure datastore validator is reused unchanged. Its archive entrypoint takes
explicit immutable data and does not look up datastore profiles. Its narrower
Config contract rejects a non-null Healthcheck and more than32 legacy layers;
all ten pinned public Market image configurations meet those limits. Datastore
evidence, export adapters and operation entrypoints are not reused. The new
adapter accepts only these fixed Market exports. Direct OCI-manifest exports
and exact Moby compatibility records are supported; an unsupported physical
layout refuses before LOAD rather than relaxing the sealed parser. Actual TAR
admission is still required; public metadata review is not an archive test.

The existing pure `expected_inspect_comment` helper follows the actual
[Moby v28.5.1 projection](https://github.com/moby/moby/blob/v28.5.1/daemon/images/image_inspect.go):
a nonempty top Comment wins; an empty top Comment uses only the last history
entry's comment. Market evidence uses that same helper when constructing its
expected Docker metadata. Config bytes, history, Author, DockerVersion, layers
and archive identity remain strict; no raw-image field is changed to pass it.

## One profile at a time

For a Native role, the actual scenario is: admit its exact archive on Web, keep
that Web TAR, stream it directly into one Native Docker stdin, verify the real
Native daemon identity and unchanged runtime, then let Root remove only the
successfully consumed public archive files by their recorded identities. Native
creates no large ZIP, gzip or TAR. For a Web role, the same admitted Web TAR feeds
one Web LOAD. Only the four Native profiles load on Native; only the six Web
profiles load on Web. Merely staging a Native source on Web does not load it
into the Web daemon.

1. Root stages the same reviewed source as `market_candidate_images.py`, the
   exact catalog, its four named helper files under `source/`, and the ten
   SHA-bound public evidence ZIPs as `evidence/<profile>.zip`. Namespace, source,
   evidence, controls and attempts directories belong to root; directories are
   0700 and staged regular files0600/single-link. Existing namespaces and files
   must never be replaced to bypass an old attempt.
2. Native namespace is
   `/root/trading-bot/trading_bot/.local/security-market-candidate-images-20261025-03`;
   Web namespace is `/srv/trading-bot/security-market-candidate-images-20261025-03`.
   Root collects a fresh read-only snapshot at each site with
   `--site bot|web --phase snapshot --source-sha256 <reviewed-worker-sha>`.
   Snapshot reads only explicit public daemon/container/store/unit metadata,
   twice checks runtime stability, and never reads container Env. It creates no
   operation guard, workspace or image. Snapshot output is not activation proof.
   The public HostConfig projection renames only `LogConfig.Config` to
   `LogConfig.options`, preserving every typed logging field/value; image
   Config and nested Env remain prohibited public fields.
3. Root writes identical SHA-bound control buffers at both sites under
   `controls/<nonce>.json`. Required schema is `market_image_store_control/1`;
   fields are exactly those checked by `Context`. The profile must be one of the
   ten catalog entries. Phase is `receive-web`, `load-web` or `load-native`.
   `not_before` and `deadline` are integer UTC epochs with at most900 seconds;
   both complete public baselines must be at most300 seconds old. Marker and
   SOURCE identities come from fresh metadata observations, never their bytes.
   `admission` is `<profile>-admission.json`, with its actual receipt SHA for a
   LOAD; the receive phase does not consume an admission receipt.
4. Root invokes Native coordination in a named finite systemd scope, with
   `--site bot --phase <phase> --control <exact-control-path>` and
   `--control-sha256 <sha> --apply MARKET-IMAGE-STORES-ONLY-20261025-03`.
   The required unit name is `market-images-bot-<phase>-<nonce>`; set CPUQuota100%,
   MemoryMax2G, MemorySwapMax0, TasksMax64, RuntimeMaxSec900 and TimeoutStopSec5.
   Normal `systemd-run --scope --quiet --collect` supplies the scope. The worker
   verifies actual cgroup limits and unit lifetime. Without `--apply` it performs
   only public preflight. Web workers use the same fixed scopes over existing
   strict-host SSH; no relaxed host-key, forwarding or Go address-space limit.
   Web SSH uses the established `id_ed25519_iran` identity, disables multiplexing
   and host-key updates, and keeps strict existing known-host verification.
5. Receive validates bounded ZIP membership and export/gzip pins, decodes with a
   size ceiling, checks full physical OCI/legacy closure, every ordered layer,
   the Config association and complete typed Config against public CI evidence
   in RAM. Root copies only the resulting public `admission.json` into each
   site's controls directory with its actual SHA. Native controls use the same
   admitted source identity and measured budget, not a reconstructed assertion.
6. Each LOAD writes a durable request before the RPC. The resulting image-map
   difference must be exactly one source-admitted immutable identity; existing
   images and tags remain exact. Classic Config identity never queries
   Descriptor. A distinct manifest/index identity must match its source-derived
   typed digest, media type and actual byte size. Whole typed Config equality
   runs in RAM; no Env/Config values are written to receipts or errors.
7. Before, during and after transfers/import waits, keep the full conservative
   `2*TAR bytes + 2*expanded layer bytes` budget on both the data store and the
   possible OS-root temporary store, above8GiB data/2GiB root plus64MiB margin
   and bounded control output. `/tmp` must independently retain its existing
   physical directory identity and the same2GiB temporary-store floor plus the
   complete prospective budget, margin and bounded output at every pulse. When
   `/tmp` is a distinct device, a bounded `/proc/meminfo` observation must also
   report `MemAvailable` at least2GiB plus that full budget, margin and output;
   this conservatively accounts for a possible tmpfs without assuming free disk
   implies available RAM. Missing, duplicate, malformed or oversized memory
   metadata refuses. Receive separately accounts for prospective ZIP/gzip/TAR
   storage. Bind root/tmp and known daemon unit/store metadata; private daemon
   override absence is explicitly unproven. These checks reserve no capacity and
   assert no hard allocator quota or shared-layer space saving.
8. Native retains existing M then the same empty SOURCE inode and its flocks;
   Web retains its M and one fresh site guard. Neither permanent payload is
   read or hashed. Source/archive FDs remain open through their final
   checkpoints; canonical protected parent directory identities/modes/owners
   also remain bound (directory mtime/nlink changes are not identity drift).
   The native stream binds header, exact body SHA/size, footer,
   EOF and both reaped clients. Fifteen Native and27 Web container contracts
   stay exact, including F79's healthy existing capture owner. No Telegram
   client, image alias, source mask, container or data change is involved.
9. Only a verified terminal result releases its exact owned temporary guard.
   Unknown requests retain guards, attempts, admitted source and failure
   evidence; absence never proves cancellation. No blind retry/new nonce may
   adopt the same profile-phase attempt. Root first reconciles the real daemon
   result and closes the old intent under separately reviewed cleanup authority.
10. After actual destination success and the final held-FD check, Root performs
    exact identity-bound cleanup of that profile's `source.zip`, `source.gz` and
    `source.tar` before starting the next profile, or within24 hours if work
    stops. Unknown outcomes retain them for reconciliation. This is an explicit
    cleanup obligation, not an automatic TTL promise; history and receipts
    remain, and no backup or private/runtime data is included. Archive deletion
    is not performed by the controller itself.

## Result and remaining gates

`market_preloaded_image/1` is emitted only after measured source/daemon binding.
It names the actual site/host/machine, catalog and artifact, Config and distinct
local daemon identity, complete layer list, real admission/control hashes, and
RAM-only Config comparison. It records no serving activation or security
acceptance. Genuine raw44HIGH findings and all caller/functional/runtime gates
remain open. This packet neither fabricates full-matrix success nor changes the
autopilot STOP state or the unstarted30-clean-unit streak.

The required genuine-root unit suite is `tests.test_market_candidate_images`.
Its owned files, pipes and mocked process clients test archive admission,
bounded I/O, identity drift, full-budget pulses, unknown outcomes and exact
cleanup without Docker, network, SSH, private data or production effects. Tests
and source coverage are source-validation evidence, never actual LOAD results.

For the02 source, Root independently passed73 genuine-root fenced tests
(1.667s, no skips).
The exact new formatter was separately executed read-only against all15 real
Native containers: exit0, no stderr, and8 null Health/Healthcheck pairs. It
read only the approved public metadata projection; no image/container mutation
or Env access occurred. This proves the previously failing optional-field
boundary, not archive admission or LOAD. The original01 staged files remain
unchanged. Neither01 nor02 had an operation control or attempt;03 uses its own
new namespace, leaving both prior staged sources and observations intact.
