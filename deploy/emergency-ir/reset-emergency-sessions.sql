-- Apply only after the production snapshot has been restored into the
-- isolated Emergency IR database, and before migration/API are started.
--
-- This deliberately preserves users, accounts, home_server values, and every
-- forensic session row.  It only makes inherited authentication state terminal
-- so the standalone runtime can issue fresh local sessions.
BEGIN;

WITH changed AS (
    UPDATE user_sessions
       SET is_active = FALSE
     WHERE is_active
    RETURNING 1
)
SELECT count(*) AS emergency_sessions_deactivated FROM changed;

WITH changed AS (
    UPDATE session_login_requests
       SET status = 'expired'
     WHERE status = 'pending'
    RETURNING 1
)
SELECT count(*) AS emergency_login_requests_expired FROM changed;

WITH changed AS (
    UPDATE single_session_recovery_requests
       SET status = 'cancelled',
           cancelled_at = now(),
           inline_action_expires_at = now(),
           chat_action_expires_at = now()
     WHERE status IN (
         'pending_admin_review',
         'identity_verification_requested',
         'identity_submitted'
     )
    RETURNING 1
)
SELECT count(*) AS emergency_recoveries_cancelled FROM changed;

COMMIT;
