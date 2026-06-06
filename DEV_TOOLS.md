# Developer Tools

This file documents low-friction commands intended for developers/operators who
need to maintain a local or deployed development instance without remembering
one-off Python scripts.

## Unified Admin CLI

Use the Makefile wrapper when the Docker app container is running:

```bash
make dev-admin ARGS="list-users"
make dev-admin ARGS="show-user 09120000000"
```

The wrapper runs:

```bash
docker compose exec -T app python scripts/dev_admin.py ...
```

You can also run the script directly from inside the app environment:

```bash
python scripts/dev_admin.py --help
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
make dev-admin ARGS="--help"
```

For command-specific help:

```bash
make dev-admin ARGS="create-superadmin --help"
make dev-admin ARGS="reset-sessions --help"
make dev-admin ARGS="set-role --help"
```

### 3. Create the first super admin after a fresh database reset

```bash
make dev-admin ARGS="create-superadmin 09120000000 'مدیر ارشد' --password 'TempPass123'"
```

Then verify the record:

```bash
make dev-admin ARGS="show-user 09120000000"
```

Expected result: the user has role `SUPER_ADMIN`, is `ACTIVE`, has an admin
password hash, and `must_change_password=true` unless you passed
`--no-must-change-password`.

### 4. Create normal and middle-admin test users

Normal users are OTP-based and do not need a local admin password:

```bash
make dev-admin ARGS="create-user 09120000001 'کاربر تست' --role standard"
```

Middle admins need a local admin password:

```bash
make dev-admin ARGS="create-middle-admin 09120000002 'مدیر میانی' --password 'TempPass123'"
```

List the users:

```bash
make dev-admin ARGS="list-users --limit 10"
```

### 5. Fix a user who cannot log in

First inspect the user:

```bash
make dev-admin ARGS="show-user 09120000000"
```

If the account is inactive:

```bash
make dev-admin ARGS="set-status 09120000000 active"
```

If login throttles or pending login/recovery requests are stuck:

```bash
make dev-admin ARGS="unlock-login 09120000000"
```

If the user must be logged out from every device:

```bash
make dev-admin ARGS="reset-sessions 09120000000"
```

### 6. Change an admin password safely

```bash
make dev-admin ARGS="change-password 09120000000 --password 'NewPass123' --must-change-password"
```

This only applies to `SUPER_ADMIN` and `MIDDLE_MANAGER` users. Normal users use
the OTP/session login flow and do not have a local admin password.

### 7. Promote or downgrade a user

Promote a normal user to middle admin:

```bash
make dev-admin ARGS="set-role 09120000001 middle --password 'TempPass123'"
```

Downgrade a middle admin to a normal user:

```bash
make dev-admin ARGS="set-role 09120000002 standard"
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
make dev-admin ARGS="show-user 12"
make dev-admin ARGS="show-user 09120000000"
make dev-admin ARGS="show-user مدیر_ارشد"
```

### User Creation

Create a normal OTP-based user:

```bash
make dev-admin ARGS="create-user 09120000002 'کاربر تست' --role standard"
```

Create a middle admin with a temporary local admin password:

```bash
make dev-admin ARGS="create-middle-admin 09120000001 'مدیر میانی' --password 'TempPass123'"
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
