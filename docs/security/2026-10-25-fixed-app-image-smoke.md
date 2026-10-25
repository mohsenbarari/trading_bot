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
