/* Compatibility glue only; SQLite core source stays unmodified.
 * SQLITE_EXTRA_AUTOEXT is upstream's per-connection built-in initializer.
 * It runs before a connection is returned and after global initialization;
 * no recursive global registration, mutable registry or Python hook is used.
 */
#include "sqlite3.h"

__attribute__((visibility("hidden")))
int sqlite3_trading_legacy_fp_init(sqlite3 *db) {
  int digits = 0;
  int rc = sqlite3_db_config(db, SQLITE_DBCONFIG_FP_DIGITS, 15, &digits);
  return rc == SQLITE_OK && digits == 15 ? SQLITE_OK : SQLITE_ERROR;
}
