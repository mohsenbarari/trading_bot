# Developer Tools

This file documents low-friction commands intended for developers/operators who
need to maintain a local or deployed development instance without remembering
one-off Python scripts.

## Unified Admin CLI

Use the direct Makefile commands for day-to-day work. They open an interactive
wizard and ask for the required values:

```bash
make create-superadmin
make create-admin
make create-user
make list-users
make show-user
make change-password
make force-password-change
make set-role
make set-status
make set-max-sessions
make reset-sessions
make unlock-login
```

`make create-admin` creates a `MIDDLE_MANAGER` account. The lower-level wrapper
is still available for scripted/non-interactive usage:

```bash
make dev-admin ARGS="list-users"
make dev-admin ARGS="show-user 09120000000"
```

You can also run the script directly from inside the app environment:

```bash
python scripts/dev_admin.py --help
```

## Observability Shortcuts

Use these commands when you need logs, metrics, dashboards, or incident
investigation without remembering Docker service names:

```bash
make logs-api
make logs-bot
make logs-jobs
make logs-follow
make metrics
make sync-health
make sync-health-iran
make observability-up
make observability-down
make observability-logs
make observability-overhead
make observability-readiness
make audit-log-export
```

`make metrics` prints the local API Prometheus endpoint from
`http://127.0.0.1:8000/metrics`.

`make sync-health` prints the local/foreign sync backlog and lag. `make
sync-health-iran` runs the same check on the Iran server through SSH. Use these
before and after `make sync-recover` when Iran reconnects after an outage.

`make observability-up` starts the optional local Loki, Promtail, and Grafana
stack. Use it only on trusted operator machines and keep Grafana/Loki bound to
private access.

Incident runbooks:

```text
docs/OBSERVABILITY_RUNBOOK.md
docs/OBSERVABILITY_LOG_SEARCH.md
docs/OBSERVABILITY_DASHBOARDS.md
docs/OBSERVABILITY_ALERTS.md
docs/OBSERVABILITY_ERROR_TRACKING.md
docs/CROSS_SERVER_SYNC_OBSERVABILITY.md
```

The runbook covers failed login tracing, websocket disconnects, media upload
failures, trade actions, worker failures, and alert investigation.

For production-hardening checks:

- `make observability-overhead` measures local structured logging overhead and
  fails if the default per-event budget is exceeded.
- `make observability-readiness` runs the production P9 readiness report for
  logging overhead, metrics-target contract, audit-anchor export/ship, required
  sync sampler timers, artifact hygiene, and sync-health.
- `make audit-log-export` exports audit logs from local Loki to
  `tmp/audit-log-exports/*.jsonl`. Pass Loki/query/window overrides through
  `ARGS`, for example:

```bash
make audit-log-export ARGS="--hours 72 --limit 10000"
```

## Quick Tutorial

### 1. Make sure the app container is running

```bash
docker compose ps
```

If the `app` service is not running, deploy or start the stack first:

```bash
make foreign
```

### 2. See the available operations

```bash
make help
```

For command-specific help:

```bash
make dev-admin ARGS="create-superadmin --help"
make dev-admin ARGS="create-admin --help"
make dev-admin ARGS="reset-sessions --help"
make dev-admin ARGS="set-role --help"
```

### 3. Create the first super admin after a fresh database reset

```bash
make create-superadmin
```

The command asks for:

```text
Mobile number
Full name
Account name
Temporary/admin password
Force password change on next login
Allow creating another super admin if one already exists
```

Then verify the record:

```bash
make show-user
```

Expected result: the user has role `SUPER_ADMIN`, is `ACTIVE`, has an admin
password hash, and `must_change_password=true` unless you passed
`--no-must-change-password`.

### 4. Create normal and middle-admin test users

Normal users are OTP-based and do not need a local admin password:

```bash
make create-user
```

Middle admins need a local admin password:

```bash
make create-admin
```

List the users:

```bash
make list-users
```

### 5. Fix a user who cannot log in

First inspect the user:

```bash
make show-user
```

If the account is inactive:

```bash
make set-status
```

If login throttles or pending login/recovery requests are stuck:

```bash
make unlock-login
```

If the user must be logged out from every device:

```bash
make reset-sessions
```

### 6. Change an admin password safely

```bash
make change-password
```

This only applies to `SUPER_ADMIN` and `MIDDLE_MANAGER` users. Normal users use
the OTP/session login flow and do not have a local admin password.

### 7. Promote or downgrade a user

Promote a normal user to middle admin:

```bash
make set-role
```

Downgrade a middle admin to a normal user:

```bash
make set-role
```

Downgrading from an admin role clears the local admin password hash and
`must_change_password` flag.

### 8. Common identity formats

Most commands accept any of these values as `identity`:

```text
database id
mobile number
account_name
username
```

Examples:

```bash
make show-user
```

When prompted, enter one of:

```text
12
09120000000
مدیر_ارشد
```

## Non-Interactive Examples

Use `make dev-admin ARGS="..."` when you need an exact one-line command for
automation or repeatable setup.

### User Creation

Create a normal OTP-based user:

```bash
make dev-admin ARGS="create-user 09120000002 'کاربر تست' --role standard"
```

Create a middle admin with a temporary local admin password:

```bash
make dev-admin ARGS="create-admin 09120000001 'مدیر میانی' --password 'TempPass123'"
```

Create a super admin:

```bash
make dev-admin ARGS="create-superadmin 09120000000 'مدیر ارشد' --password 'TempPass123'"
```

By default, only one super admin is allowed. For development-only cases where a
second super admin is intentional:

```bash
make dev-admin ARGS="create-superadmin 09120000003 'مدیر دوم' --password 'TempPass123' --allow-multiple-superadmins"
```

Useful creation flags:

```text
--account-name <value>
--username <value>
--telegram-id <id>
--home-server foreign|iran
--max-sessions 1
--max-accountants 3
--max-customers 5
--bot-access / --no-bot-access
--must-change-password / --no-must-change-password
```

### User Inspection

List users:

```bash
make dev-admin ARGS="list-users --limit 20"
make dev-admin ARGS="list-users --role middle --status active"
make dev-admin ARGS="list-users --search 0912"
```

Show one user by id, mobile, account name, or username:

```bash
make dev-admin ARGS="show-user 09120000000"
```

### Password and Role Maintenance

Change a super/middle admin password:

```bash
make dev-admin ARGS="change-password 09120000000 --password 'NewPass123'"
```

Force an admin to change password on next login:

```bash
make dev-admin ARGS="force-password-change 09120000000"
```

Change role:

```bash
make dev-admin ARGS="set-role 09120000002 middle --password 'TempPass123'"
make dev-admin ARGS="set-role 09120000001 standard"
```

When a user is downgraded from an admin role to a non-admin role, the local admin
password hash and forced-password flag are cleared.

### Status and Session Maintenance

Activate/deactivate an account:

```bash
make dev-admin ARGS="set-status 09120000000 active"
make dev-admin ARGS="set-status 09120000000 inactive"
```

Set the allowed concurrent session count:

```bash
make dev-admin ARGS="set-max-sessions 09120000000 2"
```

Reset a user's sessions:

```bash
make dev-admin ARGS="reset-sessions 09120000000"
```

This revokes active sessions through the application session service, removes
pending login/recovery requests for the target user, deletes target user session
rows by default, and clears Redis login/OTP keys scoped to the target
user/mobile.

Optional reset flags:

```text
--keep-session-rows
--keep-login-limits
```

Clear login throttles and pending login/recovery requests without revoking
active sessions:

```bash
make dev-admin ARGS="unlock-login 09120000000"
```

## Legacy Scripts

The older one-purpose scripts are still present for compatibility:

```bash
python scripts/create_superadmin.py <mobile_number> <account_name> <temporary_password>
python scripts/reset_sessions.py <mobile_number>
```

Prefer `make dev-admin ARGS="..."` for new operational work because it has a
single command surface and keeps user/session cleanup behavior consistent.
