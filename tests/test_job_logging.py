import logging
import unittest
from unittest.mock import MagicMock

from core.job_logging import RepeatedErrorLogger, job_context
from core.request_context import get_request_context, set_request_context


class JobLoggingTests(unittest.TestCase):
    def tearDown(self):
        logging.getLogger().handlers.clear()

    def test_job_context_sets_and_restores_context(self):
        set_request_context(request_id="outer")

        with job_context("offer_expiry", iteration=3) as run_id:
            context = get_request_context()
            self.assertEqual(context["request_id"], "outer")
            self.assertEqual(context["log_class"], "job")
            self.assertEqual(context["job_name"], "offer_expiry")
            self.assertEqual(context["iteration"], 3)
            self.assertEqual(context["run_id"], run_id)

        self.assertEqual(get_request_context(), {"request_id": "outer"})

    def test_repeated_error_logger_logs_first_and_every_nth_repeat(self):
        logger = MagicMock()
        limiter = RepeatedErrorLogger(every=3)
        exc = RuntimeError("boom")

        for _ in range(5):
            limiter.log(logger, "job failed: %s", exc, job_name="worker")

        self.assertEqual(logger.error.call_count, 2)
        first_extra = logger.error.call_args_list[0].kwargs["extra"]
        second_extra = logger.error.call_args_list[1].kwargs["extra"]
        self.assertEqual(first_extra["repeat_count"], 1)
        self.assertEqual(second_extra["repeat_count"], 3)
        self.assertEqual(second_extra["suppressed_repeats"], 2)


if __name__ == "__main__":
    unittest.main()
