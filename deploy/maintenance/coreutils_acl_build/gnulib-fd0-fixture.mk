# Compile only in the admitted vendor CI; uses actual built gnulib library.
.PHONY: gold-owned-gnulib-fd0-check
gold-owned-gnulib-fd0-fixture: $(GOLD_GNULIB_FD0_FIXTURE) lib/libcoreutils.a src/libver.a
	$(CC) $(DEFS) $(DEFAULT_INCLUDES) $(INCLUDES) $(AM_CPPFLAGS) $(CPPFLAGS) $(AM_CFLAGS) $(CFLAGS) $(AM_LDFLAGS) $(LDFLAGS) -o $@ $(GOLD_GNULIB_FD0_FIXTURE) $(LDADD) $(copy_ldadd)
gold-owned-gnulib-fd0-check: gold-owned-gnulib-fd0-fixture
	./gold-owned-gnulib-fd0-fixture --run-owned-gnulib-fd0
