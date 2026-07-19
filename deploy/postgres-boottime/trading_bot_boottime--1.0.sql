CREATE FUNCTION trading_bot_boottime_seconds()
RETURNS double precision
AS 'MODULE_PATHNAME', 'trading_bot_boottime_seconds'
LANGUAGE C VOLATILE STRICT PARALLEL SAFE;

CREATE FUNCTION trading_bot_boot_id()
RETURNS text
AS 'MODULE_PATHNAME', 'trading_bot_boot_id'
LANGUAGE C VOLATILE STRICT PARALLEL SAFE;

REVOKE ALL ON FUNCTION trading_bot_boottime_seconds() FROM PUBLIC;
REVOKE ALL ON FUNCTION trading_bot_boot_id() FROM PUBLIC;
