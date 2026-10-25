# Datastore image stores operation03

This image-store-only adapter uses an isolated copy of the byte-pinned
Market06 engine (`1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e`).
It never calls the old engine entrypoint or changes shared module globals.
Operation02, its authorities and failed preflight history remain unchanged.

The two profiles remain the exact PostgreSQL49a and Redis98c artifacts derived
from the sealed datastore contract. Both hosts may load both images from one
authenticated retained Web TAR per profile. No App handoff, parking receipt,
container activation, database API, data migration or serving change is granted.
The wire schemas stay at version2; the fresh fixed operation03, confirmation,
engine and catalog hash bind new controls. Old controls are rejected.

Stage the new tracked adapter and catalog under the canonical names
`datastore_candidate_images.py` and `datastore_candidate_images.json` in the
fresh operation03 namespace. Stage the exact engine under
`source/market_candidate_images.py`; its tracked source is
`market_candidate_images_v2.py`. Fresh Root observations supply all actual
Native15/Web27 containers and image maps, SOURCE full metadata, daemon/store
and temporary-storage policy. No permanent marker or SOURCE payload is read.

The engine permits only its fixed Web processor CID/image health flip between
healthy and unhealthy with all lifecycle, execution, host, mount and network
fields exactly unchanged. It preserves actual snapshots and emits a closed,
operation-bound incident observation. The fixed F79 owner still requires healthy
readiness, may wait at most45seconds for health alone while its first observed
lifecycle and networks stay exact, and retains the original maximum one natural
restart from the immutable control baseline. Every other role remains exact.
The adapter forwards the optional admitted-image proof through its snapshot
wrapper so post-load readiness can verify the one authorized image-map delta.

All existing private metadata, retained archive FD and parent checks, full
physical graph validation, Config-in-RAM comparison, capacity floors, finite
scopes, exclusive attempts, framed stream bindings and unknown-RPC fences remain
inherited. Unknown operations retain ownership and history for manual
reconciliation; they are never automatically retried or adopted.

Health incidents and the capture issue remain OPEN. Image-store receipts have
`runtime_health_issues_open=true`, `capture_issue_open=true`,
`serving_validation=NOT_TESTED`, and `security_accepted=false`.
