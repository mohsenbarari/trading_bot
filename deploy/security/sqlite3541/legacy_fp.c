/* Compatibility glue only; SQLite core source stays unmodified.
 * Upstream's mutexed initialization hook runs before isInit publication, so
 * concurrent first opens cannot observe the new default before registration.
 * The official per-connection API applies to Python 3.11 as well as newer APIs.
 */
#include "sqlite3.h"

static int legacy_fp_connection(sqlite3 *db, char **message,
                                const sqlite3_api_routines *api) {
  int digits = 0;
  int rc;
  (void)message;
  (void)api;
  rc = sqlite3_db_config(db, SQLITE_DBCONFIG_FP_DIGITS, 15, &digits);
  return rc == SQLITE_OK && digits == 15 ? SQLITE_OK : SQLITE_ERROR;
}

__attribute__((visibility("hidden")))
int sqlite3_trading_legacy_fp_init(const char *unused) {
  (void)unused;
  return sqlite3_auto_extension((void (*)(void))legacy_fp_connection);
}
