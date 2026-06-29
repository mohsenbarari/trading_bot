# Security Release Preflight - 2026-06-29

## Scope

This preflight checked the non-messenger security remediation state after the
Claude security re-review follow-up. No database rows, env files, Docker
containers, or production services were modified during the checks.

Checked surfaces:

- Runtime `TRUSTED_PROXY_CIDRS` on foreign and Iran app containers.
- Read-only production data hygiene guard.
- Production `dev-login` exposure.
- Production CORS behavior for localhost origins.

## Summary

Result: **not ready to mark complete on both production hosts**.

The foreign host is running the expected security-remediated behavior. The Iran
host is still running an older app image/source surface for the security fixes:

- Iran runtime still reports loopback-only `TRUSTED_PROXY_CIDRS`.
- Iran app container does not contain `scripts/check_production_data_hygiene.py`.
- Iran production still allows localhost CORS.
- Iran `POST /api/auth/dev-login` returns `403` instead of the expected
  production `404`, which indicates the staging-only dev-login gate has not
  reached the running Iran app container.

This is a deployment/sync state problem, not a new messenger or business-logic
code problem.

## Evidence

### Runtime Trusted Proxy CIDRs

| Host | Runtime environment | Runtime `TRUSTED_PROXY_CIDRS` | Status |
| --- | --- | --- | --- |
| Foreign | `production` | `127.0.0.1/32,::1/128,10.10.0.0/24` | Pass |
| Iran | `production` | `127.0.0.1/32,::1/128` | Fail |

The Iran value is still loopback-only. With host Nginx proxying into the Docker
container, that can collapse IP attribution to the Docker gateway instead of the
real client IP.

### Production Data Hygiene Guard

| Host | Command | Result |
| --- | --- | --- |
| Foreign | `make production-data-hygiene ARGS='--fail-on high'` | Pass, `finding_count=0` |
| Iran | `make production-data-hygiene-iran ARGS='--fail-on high'` | Fail, app container missing `scripts/check_production_data_hygiene.py` |

Foreign scanned:

- `users`: 37 rows
- `invitations`: 46 rows
- `customer_relations`: 1 row
- `accountant_relations`: 0 rows

No suspicious findings were reported on foreign.

Iran failed before scanning because the currently running app container image
does not include the hygiene guard script.

### `dev-login` Production Smoke

| Host | Endpoint | Expected | Observed | Status |
| --- | --- | --- | --- | --- |
| Foreign | `POST /api/auth/dev-login` | `404` | `404` | Pass |
| Iran | `POST /api/auth/dev-login` | `404` | `403` | Fail |

The Iran result is not the expected final behavior from the remediation commit.
It indicates the running Iran app has not loaded the staging-only dev-login gate.

### Localhost CORS Smoke

Preflight sent an `OPTIONS` request with:

- `Origin: http://localhost:5173`
- `Access-Control-Request-Method: GET`

| Host | Expected `Access-Control-Allow-Origin` | Observed | Status |
| --- | --- | --- | --- |
| Foreign | Absent | Absent | Pass |
| Iran | Absent | `http://localhost:5173` | Fail |

The Iran result matches the older CORS behavior where local development origins
were always included.

### Iran Runtime Age Indicator

Read-only container inspection on Iran showed the app container was created and
started from an image created on `2026-06-28T17:45:23Z`, before the later VF4-VF9
and follow-up fixes had all reached production runtime.

## Required Remediation

After explicit approval for production deployment:

1. Run the normal production deployment flow so the foreign and Iran runtime
   surfaces are both rebuilt/synced from the current approved branch.
2. Ensure Iran `.env` contains a non-loopback `TRUSTED_PROXY_CIDRS` value that
   includes the actual Nginx-to-container proxy hop.
3. Re-run this preflight:
   - Foreign and Iran runtime trusted-proxy check.
   - `make production-data-hygiene ARGS='--fail-on high'`.
   - `make production-data-hygiene-iran ARGS='--fail-on high'`.
   - Production `dev-login` smoke on both hosts.
   - Production localhost CORS smoke on both hosts.

## Release Decision

Do not consider the security remediation fully deployed until the Iran host
passes the same runtime checks as foreign.

Production deploy was intentionally not run during this preflight because the
current workflow requires explicit user approval for production deployment.
