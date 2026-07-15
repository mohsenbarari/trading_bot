# Writer Witness Service Runbook

Status: source and test foundation only; production deployment is not authorized

## Purpose And Boundary

`writer_witness_app:app` is a private control-plane process for the single
global WebApp writer term. It is not part of the public WebApp API, must not be
placed behind the public WebApp hostname, and must not share the WebApp product
database identity.

The process exposes only:

- `GET /health/live` with no ownership details;
- `GET /health/ready` with no ownership details;
- authenticated `GET /v1/writer-witness/status`;
- authenticated `POST /v1/writer-witness/transitions`.

The two WebApp sites use separate HMAC credentials. Each signed request binds
the physical site, key id, method, path, exact body hash, timestamp, and stable
request id. The witness database clock evaluates the timestamp and lease.
Successful and state-dependent rejected transition requests both receive a
durable receipt, so a delayed rejected packet cannot become valid after lease
expiry.

## Mandatory Isolation

Before any staging deployment:

1. Create a dedicated PostgreSQL database and apply
   `deploy/writer-witness/001_initial.sql` using a migration identity.
2. Create a runtime role with only `CONNECT`, `USAGE`, `SELECT`, `INSERT`, and
   `UPDATE` on the two witness tables and schema-version read access. It must
   not own the database or have DDL privileges.
3. Set `WRITER_WITNESS_DATABASE_URL` to that runtime role. By default the
   service refuses to start unless `WRITER_WITNESS_PRODUCT_DATABASE_USER` is
   supplied and differs from the witness connection username.
4. Store the raw-base64 Ed25519 private key in an absolute `0600` secret file.
   Only the witness process receives it. WebApp processes receive the matching
   public key only.
5. Generate independent random HMAC secrets of at least 32 bytes for
   `webapp_fi` and `webapp_ir`. Never copy one site's client secret to the other.
6. Bind the process to a private interface or loopback reverse proxy. Restrict
   ingress to the two fixed WebApp control-plane sources and use verified TLS
   or mTLS. Arvan and the public WebApp origin must not expose these routes.

## Service Settings

The witness process requires these settings in its private environment:

```text
LOGICAL_AUTHORITY=webapp
PHYSICAL_SITE=webapp_ir
WRITER_WITNESS_SERVICE_ENABLED=true
WRITER_WITNESS_DATABASE_URL=postgresql+asyncpg://<least-privilege-user>:<secret>@<db>/writer_witness
WRITER_WITNESS_PRODUCT_DATABASE_USER=<product-database-username-for-separation-check>
WRITER_WITNESS_REQUIRE_DISTINCT_DATABASE_IDENTITY=true
WRITER_WITNESS_PRIVATE_KEY_FILE=/run/secrets/webapp_writer_witness_ed25519
WRITER_WITNESS_PUBLIC_KEY=<raw-ed25519-public-key-base64>
WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID=<fi-key-id>
WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET=<fi-random-secret>
WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID=<ir-key-id>
WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET=<ir-random-secret>
```

Use `deploy/production/writer-witness-runtime.env.example` as the minimal
template and load it through the process supervisor. The witness settings class
intentionally does not auto-read the repository `.env`. After provisioning and
staging approval, the process entry point is:

```text
uvicorn writer_witness_app:app --host 127.0.0.1 --port 8011 --workers 1
```

Process supervision, restart limits, private TLS termination, and health probes
must be supplied by the deployment layer before enablement.

The service validates explicit `webapp_ir` placement, distinct database
identity, key-file permissions, public/private key correspondence, pairwise
credential strength, and safe lease timing before serving.

## WebApp Client Settings

Each WebApp site receives only its own pairwise credential:

```text
WRITER_WITNESS_REQUIRED=false
WRITER_WITNESS_AUTO_RENEW_ENABLED=false
WRITER_WITNESS_INTERNAL_URL=https://<private-witness-host>
WRITER_WITNESS_CLIENT_KEY_ID=<this-site-key-id>
WRITER_WITNESS_CLIENT_SECRET=<this-site-secret>
WRITER_WITNESS_VERIFY_TLS=true
WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem
WRITER_WITNESS_HTTP_TIMEOUT_SECONDS=3
WRITER_WITNESS_AUTH_MAX_AGE_SECONDS=15
```

Both enable flags remain false until independent-database staging drills pass.
When later enabled, the active WebApp background leader renews every 30 seconds.
An ambiguous network failure retries the exact same request id. A validated
signed proof is then imported into local writer state in one transaction. If
renewal cannot be proved, no local expiry is extended and ordinary fencing
stops authoritative writes at the safety deadline.

## Production Stop Conditions

Do not enable the service or WebApp enforcement merely because unit tests pass.
Production remains blocked until clock-offset evidence, private networking,
credential rotation, process supervision, independent three-database
partition/pause/delayed-packet tests, operator approval policy, and the higher
level recovery/Arvan orchestrator are complete.
