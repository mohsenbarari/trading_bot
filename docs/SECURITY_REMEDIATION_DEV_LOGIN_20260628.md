# Dev Login Production Gate Remediation

- Date: 2026-06-28
- Branch: `candidate/sync-parity-hardening`
- Scope: `POST /api/auth/dev-login`

## Production Invariant

`/api/auth/dev-login` is a staging-only helper. Production must not allow this route to create or reuse the developer super-admin account, even when a request appears local, includes spoofed forwarding headers, or provides `X-DEV-API-KEY`.

The backend is the source of truth for this boundary:

- allowed only when `settings.environment == "staging"`;
- blocked before any DB read/write/session creation in all other environments;
- returns `404 Not found` outside staging to avoid advertising the helper route.

## Staging Behavior

The existing staging access rules remain unchanged after the environment gate:

- local/private-network requests may use the helper;
- remote requests must provide the configured dev key;
- the helper still clears old dev-bypass sessions and creates a one-year development session only in staging.

## Verification

Focused coverage was added in `tests/test_auth_router_special_logins.py`:

- production rejects loopback, spoofed `X-Forwarded-For`, and valid dev-key requests before DB work;
- staging still rejects remote requests without a valid key;
- staging still bootstraps and reuses the dev user/session through the existing path.
