# Actual patched cp frontend plus its configured ordinary copy sources.
# Isolated vendor CI only; no alternate syscall or loader implementation.
.PHONY: gold-owned-parent-acl-check
gold-owned-parent-acl-fixture: $(GOLD_PARENT_FIXTURE) $(src_cp_SOURCES) lib/libcoreutils.a src/libver.a
	$(CC) $(DEFS) $(DEFAULT_INCLUDES) $(INCLUDES) $(AM_CPPFLAGS) $(CPPFLAGS) $(AM_CFLAGS) $(CFLAGS) $(AM_LDFLAGS) $(LDFLAGS) -o $@ $(GOLD_PARENT_FIXTURE) $(filter-out src/cp.c,$(filter %.c,$(src_cp_SOURCES))) $(src_cp_LDADD)
gold-owned-parent-acl-check: gold-owned-parent-acl-fixture
	./gold-owned-parent-acl-fixture --run-owned-parent-acl
