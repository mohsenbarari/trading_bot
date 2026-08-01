# Release-0 immutable candidate contract

Status: source-only, non-authorizing foundation.  It does not start a
container, acquire a Writer Witness term, alter a host, upload/download an
artifact, call Object Storage, change DNS/CDN, or promote either WebApp site.

`core.release0_immutable_candidate` verifies one canonical Ed25519-signed
descriptor with schema:

```text
gold-trade-release0-immutable-candidate-v1
```

It is deliberately separate from the historical fenced-FI identity.  The
historical application SHA
`2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` is rejected for both the
application and control release fields, even if the descriptor is otherwise
correctly signed.

## Closed descriptor content

The descriptor has no extensible fields.  It binds:

- deterministic candidate id derived from application and control release SHA;
- application and control Git commit/tree/root identities;
- immutable app and bot repository digests plus local Docker image IDs;
- SHA-256 for the closed set of term-bound API, bot, database, and external
  egress source files;
- SHA-256 for two **future** term-bound Compose files:
  `docker-compose.webapp-fi-writer-release0.yml` and
  `docker-compose.webapp-ir-promoted-release0.yml`;
- a fixed initial Writer Witness profile: 60-second lease, 15-second safety
  margin, 10-second renewal, single-writer enforcement enabled, schema
  bootstrap disabled, and API background jobs disabled.

The future Compose file names are intentionally not the current `2c08` names.
They do not exist in the historical release, so an actual candidate cannot
pass local-root validation until a separately reviewed Compose slice creates
and validates both files.

## Local verification

`scripts/verify_release0_immutable_candidate.py` accepts only a root-owned
descriptor, root-owned authority key, and root-controlled expected descriptor
SHA-256.  Descriptor verification returns
`verified-descriptor-non-authorizing`.  With `--check-local-roots`, it also
requires clean root-owned Git release roots, matching commit/tree IDs, exact
critical source bytes, and exact future Compose bytes.  That result is still
`verified-local-non-authorizing`.

Neither result authorizes a writer, promotion, execution, routing, or a
full-matrix run.  A later dedicated admission/cutover change must consume the
verified object explicitly and retain all existing `2c08` hard fences until
the new candidate's Compose, image, provenance, and host checks are complete.
