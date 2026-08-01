# Physical Arvan S3 client-factory contract

`core.physical_arvan_s3_client_factory` is the root-owned credential-to-client
seam for physical Object Storage. Importing it performs no SDK import,
credential-file read, environment or metadata lookup, network action, bucket
operation, or deployment action.

## Fixed policy and credentials

`RootOwnedArvanS3ClientFactoryConfig` pins one canonical
`https://s3.<region>.arvanstorage.ir` origin, matching region, and one private
bucket. It is default-disabled and preserves
`direct_site_control=forbidden` plus `destination_object_ingest=pull-only`.
It has no credential, token, proxy, presigned URL, endpoint override, or
arbitrary client field.

The only credential location is the fixed
`/etc/trading-bot/security/arvan-s3-credentials.json`; no config or caller can
replace it. Before SDK import, the factory requires that file and its immediate
parent to be root-owned and non-symlinked. The file must be a single-link
regular file, exactly `0600`, bounded in size, in a root-controlled directory,
and contain exactly this JSON shape:

```json
{"access_key":"…","secret_key":"…"}
```

Duplicate fields, session tokens, extra fields, invalid UTF-8/non-finite JSON,
control characters, unsafe metadata, and open/read races fail closed. Secrets
are transient private implementation values and never occur in public config,
return types, reprs, or error text.

## Scoped factories

`exact_pull_client_factory()` returns the keyword-only factory for
`ArvanExactVersionPullReader`; caller endpoint/region drift is rejected before
credentials are read or boto is imported. `physical_publish_client_factory()`
returns the zero-argument factory for physical WAL/Blob publishers.

Both return a bucket-scoped client surface limited to
`get_bucket_versioning`, `get_bucket_acl`, `list_object_versions`,
`put_object`, `head_object`, and `get_object`. Each delegated operation must
use the configured bucket. Construction itself makes no S3 operation and the
wrapper exposes no generic `__getattr__` escape hatch.

## SDK posture

Only after policy and credential checks, the factory lazily imports `boto3`
and `botocore.config`, creates a session with explicit access/secret keys and
the pinned region, then creates one path-style S3 client with the exact
endpoint, `use_ssl=True`, `verify=True`, SigV4, five-second connect timeout,
sixty-second read timeout, at most two standard retries, and an explicit empty
proxy map. It supplies no profile/session token, default environment or
metadata credential chain, arbitrary proxy, URL, or caller endpoint.

SDK/session/client and delegated-operation failures become fixed non-sensitive
codes. This factory only constructs injected clients: it does not list, GET,
HEAD, PUT, decrypt, restore, acknowledge, fence, query Witness, or promote.
