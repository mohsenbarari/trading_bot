# Include AFTER the regenerated ordinary Coreutils Makefile, in isolated vendor
# CI only.  GOLD_OWNED_FIXTURE is the admitted public ordinary_fd_acl.c path.
# This fragment has not been executed or compiled by the SOURCE preparer.
.PHONY: gold-owned-fd-acl-check
gold-owned-fd-acl-fixture: $(GOLD_OWNED_FIXTURE) $(gold_acl_sources) lib/libcoreutils.a src/libver.a
	$(CC) $(DEFS) $(DEFAULT_INCLUDES) $(INCLUDES) $(AM_CPPFLAGS) $(CPPFLAGS) $(AM_CFLAGS) $(CFLAGS) $(AM_LDFLAGS) $(LDFLAGS) -o $@ $(GOLD_OWNED_FIXTURE) $(filter %.c,$(gold_acl_sources)) $(LDADD) $(copy_ldadd)
gold-owned-fd-acl-check: gold-owned-fd-acl-fixture
	./gold-owned-fd-acl-fixture --run-owned-fd-acl
