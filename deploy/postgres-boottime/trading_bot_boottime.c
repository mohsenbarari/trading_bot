#include "postgres.h"
#include "fmgr.h"
#include "utils/builtins.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

PG_MODULE_MAGIC;

PG_FUNCTION_INFO_V1(trading_bot_boottime_seconds);
PG_FUNCTION_INFO_V1(trading_bot_boot_id);

Datum
trading_bot_boottime_seconds(PG_FUNCTION_ARGS)
{
    struct timespec value;

    if (clock_gettime(CLOCK_BOOTTIME, &value) != 0)
    {
        int saved_errno = errno;
        ereport(ERROR,
                (errcode_for_file_access(),
                 errmsg("could not read CLOCK_BOOTTIME: %s", strerror(saved_errno))));
    }

    PG_RETURN_FLOAT8((double) value.tv_sec + ((double) value.tv_nsec / 1000000000.0));
}

Datum
trading_bot_boot_id(PG_FUNCTION_ARGS)
{
    FILE *file;
    char value[64];
    size_t length;

    file = fopen("/proc/sys/kernel/random/boot_id", "r");
    if (file == NULL)
    {
        int saved_errno = errno;
        ereport(ERROR,
                (errcode_for_file_access(),
                 errmsg("could not read host boot identity: %s", strerror(saved_errno))));
    }
    if (fgets(value, sizeof(value), file) == NULL)
    {
        int saved_errno = errno;
        fclose(file);
        ereport(ERROR,
                (errcode_for_file_access(),
                 errmsg("could not read host boot identity: %s", strerror(saved_errno))));
    }
    if (fclose(file) != 0)
    {
        int saved_errno = errno;
        ereport(ERROR,
                (errcode_for_file_access(),
                 errmsg("could not close host boot identity: %s", strerror(saved_errno))));
    }

    length = strcspn(value, "\r\n");
    if (length != 36)
        ereport(ERROR, (errmsg("host boot identity has an unexpected format")));
    PG_RETURN_TEXT_P(cstring_to_text_with_len(value, (int) length));
}
