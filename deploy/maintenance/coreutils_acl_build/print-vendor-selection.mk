# Read-only target: never assigns TESTS/TEST_LOGS/TEST_EXTENSIONS or filters them.
.PHONY: gold-print-vendor-selection
gold-print-vendor-selection:
	$(info GOLD_VENDOR_SELECTION_V2)
	$(info suite_log=$(TEST_SUITE_LOG))
	$(info collect_skipped_logs=$(if $(IGNORE_SKIPPED_LOGS),no,yes))
	$(foreach extension,$(TEST_EXTENSIONS),$(info extension=$(extension)))
	$(foreach test,$(TESTS),$(info test=$(test)))
	$(foreach testlog,$(TEST_LOGS),$(info log=$(testlog)))
	@:
