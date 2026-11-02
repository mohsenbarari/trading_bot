/* Disposable CI only: no file database, network or operational data. */
#include "sqlite3.h"
#include <pthread.h>
#include <stdio.h>

static pthread_barrier_t gate;
static void *open_connection(void *unused) {
  sqlite3 *db = 0;
  int digits = 0;
  int rc;
  (void)unused;
  pthread_barrier_wait(&gate);
  rc = sqlite3_open_v2(":memory:", &db,
                      SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, 0);
  if (rc == SQLITE_OK)
    rc = sqlite3_db_config(db, SQLITE_DBCONFIG_FP_DIGITS, 0, &digits);
  if (rc == SQLITE_OK && digits != 15) rc = SQLITE_ERROR;
  if (db && sqlite3_close(db) != SQLITE_OK) rc = SQLITE_ERROR;
  return rc == SQLITE_OK ? 0 : (void *)1;
}

int main(void) {
  pthread_t threads[8];
  int cycle, i;
  void *failed;
  for (cycle = 0; cycle < 4; ++cycle) {
    if (sqlite3_shutdown() != SQLITE_OK) return 1;
    if (pthread_barrier_init(&gate, 0, 8)) return 2;
    for (i = 0; i < 8; ++i)
      if (pthread_create(&threads[i], 0, open_connection, 0)) return 3;
    for (i = 0; i < 8; ++i) {
      if (pthread_join(threads[i], &failed) || failed) return 4;
    }
    if (pthread_barrier_destroy(&gate)) return 5;
    if (sqlite3_shutdown() != SQLITE_OK || sqlite3_initialize() != SQLITE_OK)
      return 6;
  }
  if (sqlite3_shutdown() != SQLITE_OK) return 7;
  puts("PASS: 32 simultaneous first connections; four shutdown/reinitialize cycles; FP_DIGITS=15");
  return 0;
}
