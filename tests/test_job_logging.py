import logging
import unittest
from unittest.mock import MagicMock, patch

from core.job_logging import RepeatedErrorLogger, job_context, mark_current_job_failed
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

    def test_job_context_can_be_marked_failed_without_raising(self):
        with patch("core.job_logging.record_job_run") as record_job_run:
            with job_context("offer_expiry", iteration=3):
                mark_current_job_failed("handled-error")

        record_job_run.assert_called_once()
        self.assertEqual(record_job_run.call_args.kwargs["job_name"], "offer_expiry")
        self.assertEqual(record_job_run.call_args.kwargs["result"], "failure")

    def test_repeated_error_logger_marks_active_job_failed_once_per_iteration(self):
        logger = MagicMock()
        limiter = RepeatedErrorLogger(every=10)

        with patch("core.job_logging.record_job_run") as record_job_run:
            with job_context("session_expiry", iteration=1):
                limiter.log(logger, "job failed: %s", RuntimeError("handled"))

        record_job_run.assert_called_once()
        self.assertEqual(record_job_run.call_args.kwargs["job_name"], "session_expiry")
        self.assertEqual(record_job_run.call_args.kwargs["result"], "failure")
        logger.error.assert_called_once()

    def test_repeated_error_logger_logs_first_and_every_nth_repeat(self):
        logger = MagicMock()
        limiter = RepeatedErrorLogger(every=3)
        exc = RuntimeError("boom")

        with patch("core.job_logging.record_job_run") as record_job_run:
            for _ in range(5):
                limiter.log(logger, "job failed: %s", exc, job_name="worker")

        self.assertEqual(logger.error.call_count, 2)
        self.assertEqual(record_job_run.call_count, 5)
        for call in record_job_run.call_args_list:
            self.assertEqual(call.kwargs["job_name"], "worker")
            self.assertEqual(call.kwargs["result"], "failure")
        first_extra = logger.error.call_args_list[0].kwargs["extra"]
        second_extra = logger.error.call_args_list[1].kwargs["extra"]
        self.assertEqual(first_extra["repeat_count"], 1)
        self.assertEqual(second_extra["repeat_count"], 3)
        self.assertEqual(second_extra["suppressed_repeats"], 2)

    def test_repeated_error_logger_can_skip_metric_when_failure_already_recorded(self):
        logger = MagicMock()
        limiter = RepeatedErrorLogger(every=1)

        with patch("core.job_logging.record_job_run") as record_job_run:
            limiter.log(logger, "job failed: %s", RuntimeError("boom"), job_name="worker", metric_recorded=True)

        record_job_run.assert_not_called()
        logger.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
