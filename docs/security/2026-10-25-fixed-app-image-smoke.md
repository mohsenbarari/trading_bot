# Fixed App image: isolated hosted smoke

This workflow tests the actual ed59 image with disposable PostgreSQL/Redis and
public synthetic fixtures. It does not replace or activate production services.
The installed `/app` remains unmasked. Image code, dependencies and frontend are
not rebuilt from the controller checkout.

The manual workflow `app-image-runtime-smoke.yml` accepts `run_smoke=true` on
main or the security branch. It admits the exact original App artifact and its
33 ordered layers, plus the pinned patched PostgreSQL49a/Redis98c artifacts.
Measured Docker identities are bound to admitted Config/manifest/index objects;
the classic Config branch never queries Descriptor. Complete image Config is
compared with type-sensitive JSON semantics in RAM. It is never published.

The controller creates an owned internal network, two disposable datastores and
one image container. Only a read-only public `/proof` capsule and temporary
storage are mounted. No production Env, key, session, data, Docker socket or
host network enters the containers. Source/helper/test bytes are sealed from
the exact workflow commit. Logs, output, resources, deadlines and archive
expansion are bounded. Cleanup follows prior intents and verifies exact names,
image identities and ownership labels before removing disposable resources.

The runtime capsule uses the real installed FastAPI lifespan and ASGI transport,
production environment/API role and normal authorization/provider guards.
Normal SMS HTTP requests terminate at an internal fake endpoint with public
placeholder credentials. It enables no dev-login or test-provider authority.

Its eight scenarios cover startup/auth negatives, invalid/expired OTP, normal
OTP/session refresh/logout, real multipart image processing/access control,
resumable upload guards, direct/group album ordering/idempotency and cancel
cleanup. Real synthetic PostgreSQL migration/persistence and Redis operations
are required. A separate component group runs six form-parser regressions and
18 existing Redis tests; its existing test doubles are not represented as
runtime provider proof. Redis componentDB1 is separate from APIDB0.

The required result is exactly8 runtime and24 component passes, with no selected
skips. Root independently passed38 controller tests under genuine UID1001,
capabilities-none/no-new-privileges and pre-import ambient fences, using only13
public copied files. Focused controller coverage is270/270 statements, zero
excluded; branches33/34. This is source validation; the32 actual image tests
remain unproven until a completed workflow artifact records them.

This slice does not test Uvicorn/network serving, browser/PWA, Bot delivery or
polling, two-host sync/outages, backups/restores, real SMS/Telegram, TLS or
background scheduler continuity. `full_matrix_accepted` and `security_accepted`
remain false even after smoke success. Raw vulnerability reports, ACL caller
assessment and actual serving/configuration gates remain separate obligations.

## First actual run and bounded failure diagnostics

Run40173368466 on08bc9849 failed before dependency admission completed and
before creating the disposable network/containers. Its actual App archive and
daemon admission passed, with the expected33 layers and full typed Config.
Cleanup completed without reported causes. Artifact12591620054 is1998bytes,
SHA256 `631c7f749458ad5b553a2d5cd6bf8bdc56b528ae62d055874d752bf4fc9e1c32`.
No runtime/component scenario ran; the failed artifact remains retained.

Local genuineUID1001/fenced reproduction passed selection of actual PostgreSQL
metadata/artifact12579072479 and its pinned529854byte evidence ZIP through the
unchanged evidence validator. This proves those public inputs parse; the first
run's actual archive/import cause remains unknown because its report omitted
the reason.

The follow-up records only catalogued enum reasons, whitelisted exception class
and a fixed per-image subphase. Unknown/private/unhashable exception arguments
never become output. This distinguishes metadata, evidence, download, archive,
LOAD and daemon comparison failures without publishing response bodies, Config,
tokens or raw HTTP logs. Root passed42 independent genuineUID1001 fenced tests;
focused controller coverage is298/298 statements, zero exclusions, branches35/36.
These tests validate diagnostics, not the still-unexecuted32 image scenarios.

Run40174234435 on840010d4 then identified `postgres_archive_admission` /
`archive_image_metadata` (`Refusal`). Artifact12591412110 is2053bytes, SHA256
`ec4caf65d1ce4bb68485e1a82ed89b81aafd8b991033a727e21251510ad3a4ae`.
App admission again passed; no runtime/component case or container/network ran,
and cleanup completed. The original failed report stays unchanged.

The shared validator now implements [Moby's documented inspect Comment
projection](https://github.com/moby/moby/blob/v28.5.1/daemon/images/image_inspect.go#L41):
a nonempty top-level comment wins; an empty value falls back only to the final
history comment. The actual public PostgreSQL evidence has no top-level comment
and its inspect Comment matches that final history entry. An earlier matching
entry cannot satisfy the guard. Malformed types/history are refused. Author,
DockerVersion, Config SHA, full typed Config, ordered layers and physical graph
checks remain strict; raw scanner gates are unchanged.

Root independently passed85 affected datastore tests and42 genuineUID1001 smoke
controller tests after the pin update. The shared validator has327/327 statements
and56/56 branches covered, zero exclusions. Current helper consumers use the
new exact bytes; historical sealed operations are unchanged. Actual PostgreSQL
archive/import and the32 image scenarios still require successful CI evidence.

## Third actual run: image admissions passed, dependency readiness failed

Run `40175137576`, source `df85708f85dc264344230afe3bca2ddddb1b5302`,
finished with failure. Public artifact `12591798478` is4466 bytes, SHA256
`92b7ba0f13a82cdab7cd4e0ceb0ee392601c5e91ce7a077333e62cd8c7a93959`.
All three physical archive admissions and actual daemon measurements passed:
App `ed59`/33 layers, PostgreSQL `49a8`/12 layers and Redis `98c4`/8 layers.
Whole typed image configuration was compared in RAM without disclosure. This
actual result verifies the previous Moby Comment correction at its failing
PostgreSQL archive boundary; the two earlier failed results remain preserved.

The next boundary, `disposable_dependency_readiness`, failed with the closed
code `dependency_readiness`. This artifact does not identify which dependency
failed or why. No installed-application or selected component test ran; all32
remain unproved. Exact owned cleanup completed with no reported causes and
retained the primary failure. No production runtime was involved.

The next source change adds separate PostgreSQL/Redis readiness subphases,
strictly typed owned-dependency state, and fixed startup marker categories.
At most8192 log bytes are classified in RAM; no raw log, Config, Env, command
arguments or credentials enter artifacts. Diagnostic failure cannot replace
the readiness failure or prevent owned cleanup. Root independently ran47
tests as genuine UID1001 with all capabilities dropped and ambient effects
fenced (1.083s, no skips). Startup users, capabilities, entrypoints and readiness
thresholds are unchanged; the diagnostic is not evidence of a repaired
dependency or successful32-case smoke.

## Fourth actual run and fixture socket correction

Run40175972718 on216d41a1 failed specifically at `postgres_readiness`:
PostgreSQL exited2, without OOM/restarting/dead; Redis was still running.
All three archive/daemon admissions passed again and owned cleanup completed
with no causes, preserving the failure. Artifact12592540955 is4893 bytes,
SHA256 `b956ee1ce2a119f731e5c4deab175baf240d0c239fd48a1d1893b895cb1d7ce1`.
Its closed startup classification was insufficient to reconstruct the discarded
log buffer; arbitrary text is not recovered or invented. No32-case result exists.

The exact admitted official entrypoint (SHA256
`9c440299ae04a0a79d55b8bf03307036d890a40979d2fb698073c9050d4b20a5`)
clears PGHOST/PGHOSTADDR for its setup psql calls. Our disposable fixture had
overridden the temporary server socket to `/tmp`, making that normal client
path inconsistent. The fixture now uses the standard `/var/run/postgresql`
socket and a16MiB volatile directory owned by admitted postgres UID/GID70.
Its nonroot user, dropped capabilities, no-new-privileges, actual immutable
image and official entrypoint remain unchanged. A PGHOST-only workaround would
not satisfy that entrypoint. The correction needs actual hosted verification.

Root independently passed50 genuineUID1001 fenced tests (1.086s, no skips).
Closed startup observations now also include byte/line counts and at most64
ordered fixed process/reason enums; no raw words, paths, Config or Env escape.
Controller source coverage is337/337 statements with zero exclusions,41/42
branches; the remaining arc is the existing main cleanup exception path.

Separately, full CI40174926884 for the earlier exact SHA df85708f succeeded:
hosted8093 (350 existing skips), genuine-root366 (no skips), frontend2037;
all errors/failures zero and all three exits0. Changed-code gates and13 mutation
targets passed. Artifact12593205178 is17208135 bytes, SHA256
`9841306807a501fb4b515e444ae146e352d41a7c18340729401bb9157dee00c1`.
This result proves that earlier SHA, not the later socket correction, serving
activation, full architecture matrix or overall security acceptance.

## Fifth actual run: readiness passed, mount representation refused

Run40176921194 onff5a2e5e passed all three archive/daemon admissions and both
dependency readiness checks. This verifies the socket correction at its actual
previously failing boundary. The App container was then created, but refused
before start with `installed_app_must_not_be_masked`; no capsule or32-case result
ran. Owned cleanup completed with no causes and retained the primary failure.
Artifact12592437369 is4476 bytes, SHA256
`3d7c1a62fad94b7df75b45927e9449e3d32a92fd194b9b980d39fa0aeafdfc2e`.

The hosted smoke had assumed legacy `--tmpfs` appeared as another `.Mounts`
row. [Moby inspect](https://github.com/moby/moby/blob/v28.5.1/daemon/inspect.go#L48)
and its [mount registration](https://github.com/moby/moby/blob/v28.5.1/daemon/volumes.go#L70)
separate that option in `HostConfig.Tmpfs`. The revised hosted check admits
exactly one readonly `/proof` bind with the actual owned Source, and separately
the complete typed `/tmp` tmpfs map with its exact original options. Missing,
extra, writable, foreign-source or `/app` mounts remain refused. This changes
only metadata interpretation for the same disposable mount policy; no serving
controller, COW or runtime policy changes. A closed counts/booleans-only receipt
records actual mount admission without retaining arbitrary paths or values.

Root independently passed52 genuineUID1001 fenced tests (1.305s, no skips).
Source coverage342/342 statements, zero exclusions,41/42 branches. Actual
mount admission and the32 selected image checks still require a new hosted
result; source regressions do not turn the fifth failed run green.
