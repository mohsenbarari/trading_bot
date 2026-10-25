"""Public byte-stream fixtures only; no app, subprocess, network or file reads."""
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/extract_browser_ci_backend_exceptions.py'
SPEC = importlib.util.spec_from_file_location('browser_closed_exception_filter', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
HEADER = b'Traceback (most recent call last):\n'


def frame(path='api/routers/offers.py', line=1660):
    return f'  File "/app/{path}", line {line}, in PRIVATE_FUNCTION_NAME\n'.encode()


def json_event(stack, **extra):
    # core.logging_config.JsonLogFormatter's actual public exception envelope.
    value = {'ts': '2026-10-25T00:00:00Z', 'level': 'ERROR', 'service': 'api',
             'logger': 'uvicorn.error', 'message': 'PRIVATE_MESSAGE',
             'exception': {'type': 'RuntimeError', 'message': 'PRIVATE_SQL',
                           'stacktrace': stack.decode('utf-8')}, **extra}
    return (json.dumps(value, separators=(',', ':')) + '\n').encode()


class BoundedInput(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.requests = []
        self.observed = 0

    def readline(self, size=-1):
        if not 0 < size <= M.MAX_LINE_BYTES + 1:
            raise AssertionError('unbounded read')
        self.requests.append(size)
        raw = super().readline(size)
        self.observed += len(raw)
        if self.observed > M.MAX_INPUT_BYTES + 1:
            raise AssertionError('total input exceeded')
        return raw


class BoundedOutput(io.BytesIO):
    def write(self, raw):
        if self.tell() + len(raw) > M.MAX_OUTPUT_BYTES:
            raise AssertionError('output exceeded')
        return super().write(raw)


class BrowserBackendExceptionFilterTests(unittest.TestCase):
    def invoke(self, raw, argv=()):
        stream = BoundedInput(raw)
        output = BoundedOutput()
        rc = M.main(list(argv), stream, output)
        value = json.loads(output.getvalue())
        self.assertLessEqual(len(output.getvalue()), M.MAX_OUTPUT_BYTES)
        return rc, value, output.getvalue(), stream

    def test_actual_handler_chain_keeps_class_paths_lines_and_drops_every_message(self):
        raw = (b'ERROR: Exception in ASGI application PRIVATE_HEADER\n' + HEADER
               + frame('api/routers/accountants.py', 234)
               + b'    public_webapp_url_for_links(settings=PRIVATE_CONFIG)\n'
               + frame('core/public_webapp_url.py', 58)
               + b'core.public_webapp_url.PublicWebAppURLConfigurationError: PRIVATE_URL\n')
        rc, value, body, _ = self.invoke(raw)
        self.assertEqual(rc, 0)
        self.assertEqual(value['reports'], [{
            'exception_class': 'PublicWebAppURLConfigurationError',
            'frames': [{'path': 'api/routers/accountants.py', 'line': 234},
                       {'path': 'core/public_webapp_url.py', 'line': 58}],
            'frames_omitted': 0,
        }])
        self.assertTrue(value['input_complete'])
        self.assertNotIn(b'PRIVATE', body)

    def test_chained_and_exception_group_tracebacks_remain_separate_reports(self):
        raw = (HEADER + frame() + b'sqlalchemy.exc.IntegrityError: PRIVATE SQL\n'
               + b'During handling of the above exception, another exception occurred:\n'
               + b'  + Exception Group Traceback (most recent call last):\n'
               + b'  | File "/app/api/deps.py", line 92, in SECRET_FUNCTION\n'
               + b'  | ExceptionGroup: PRIVATE BODY\n'
               + b'  | Traceback (most recent call last):\n'
               + b'  | File "/app/core/db.py", line 66, in SECRET_FUNCTION\n'
               + b'  | asyncpg.exceptions.UniqueViolationError: PRIVATE PARAMS\n')
        _, value, body, _ = self.invoke(raw)
        self.assertEqual([r['exception_class'] for r in value['reports']],
                         ['IntegrityError', 'ExceptionGroup', 'UniqueViolationError'])
        self.assertEqual(value['reports'][-1]['frames'], [{'path': 'core/db.py', 'line': 66}])
        self.assertNotIn(b'PRIVATE', body)
        self.assertNotIn(b'SECRET', body)

    def test_unapproved_paths_and_classes_cannot_become_output_channels(self):
        raw = HEADER
        for path in ('api/PRIVATE_TOKEN.py', 'core/../PRIVATE_CONFIG.py',
                     'models/user.py/PRIVATE_BODY.py', '../api/routers/offers.py'):
            raw += frame(path)
        raw += b'  File "/private/PRIVATE_SESSION.py", line 88, in SECRET\n'
        raw += b'PRIVATE_TOKEN_Error: PRIVATE_MESSAGE\n'
        _, value, body, _ = self.invoke(raw)
        self.assertEqual(value['reports'], [{'exception_class': 'other', 'frames': [], 'frames_omitted': 5}])
        for token in (b'PRIVATE', b'SECRET', b'/private', b'..'):
            self.assertNotIn(token, body)

    def test_headers_jwt_sql_json_and_fake_metadata_outside_traceback_are_ignored(self):
        raw = (b'Authorization: Bearer PRIVATE_JWT\nCookie: PRIVATE_SESSION\n'
               b'SELECT PRIVATE_SQL FROM PRIVATE_TABLE\n'
               b'{"Config":{"Env":["PRIVATE_KEY"]},"body":"PRIVATE_BODY"}\n'
               + frame() + b'ValueError: PRIVATE_NOT_A_TRACEBACK\n'
               + HEADER + frame('api/routers/customers.py', 421)
               + b'    request.headers["PRIVATE_HEADER"]\n'
               + b'TypeError: PRIVATE_HEADER PRIVATE_JWT PRIVATE_SQL\n')
        _, value, body, _ = self.invoke(raw)
        self.assertEqual(len(value['reports']), 1)
        self.assertEqual(value['reports'][0]['exception_class'], 'TypeError')
        for token in (b'PRIVATE', b'Authorization', b'Cookie', b'Config', b'Env', b'SELECT', b'headers'):
            self.assertNotIn(token, body)

    def test_non_utf8_exception_message_is_never_decoded_or_emitted(self):
        _, value, body, _ = self.invoke(HEADER + frame() + b'ValueError: \xff\xfePRIVATE\n')
        self.assertEqual(value['reports'][0]['exception_class'], 'ValueError')
        self.assertNotIn(b'PRIVATE', body)

    def test_actual_json_formatter_shape_has_same_closed_report_as_plain_traceback(self):
        stack = HEADER + frame() + b'RuntimeError: PRIVATE_SQL PRIVATE_JWT\n'
        _, plain, _, _ = self.invoke(stack)
        _, value, body, _ = self.invoke(json_event(stack, headers={'Authorization': 'PRIVATE_JWT'},
                                                  body='PRIVATE_BODY', Config={'Env': ['PRIVATE_ENV']}))
        self.assertEqual(value['reports'], plain['reports'])
        self.assertEqual(value['structured_records_refused'], 0)
        self.assertEqual(value['input_lines'], 1)
        self.assertTrue(value['input_complete'])
        for token in (b'PRIVATE', b'Authorization', b'Config', b'Env', b'headers', b'stacktrace'):
            self.assertNotIn(token, body)

    def test_json_group_frames_and_unknown_class_still_use_only_closed_projection(self):
        stack = (HEADER + frame('api/PRIVATE.py') + frame('core/db.py', 3)
                 + b'PRIVATE_Exception: PRIVATE_MESSAGE\n' + HEADER
                 + frame('api/routers/offers.py', 7) + b'IntegrityError: PRIVATE_SQL\n')
        _, value, body, _ = self.invoke(json_event(stack))
        self.assertEqual([r['exception_class'] for r in value['reports']], ['other', 'IntegrityError'])
        self.assertEqual(value['reports'][0]['frames'], [{'path': 'core/db.py', 'line': 3}])
        self.assertEqual(value['reports'][0]['frames_omitted'], 1)
        self.assertNotIn(b'PRIVATE', body)

    def test_json_records_cannot_complete_each_others_or_surrounding_partial_tracebacks(self):
        raw = HEADER + frame() + json_event(HEADER + frame()) + json_event(b'ValueError: PRIVATE\n')
        _, value, body, _ = self.invoke(raw + b'RuntimeError: PRIVATE\n')
        self.assertEqual(value['reports'], [])
        self.assertEqual(value['incomplete_tracebacks'], 2)
        self.assertNotIn(b'PRIVATE', body)

    def test_malformed_duplicate_nonfinite_or_wrong_json_exception_records_are_closed(self):
        examples = [b'{PRIVATE\n', b'{"exception":null,"exception":{}}\n',
                    b'{"private":NaN}\n', b'{"exception":true}\n',
                    b'{"exception":{"type":"RuntimeError","message":"PRIVATE","stacktrace":false}}\n',
                    b'{"exception":{"type":"RuntimeError","message":"PRIVATE","stacktrace":"\\ud800"}}\n',
                    json_event(HEADER, exception={'type': '', 'message': 'PRIVATE', 'stacktrace': ''}),
                    json_event(HEADER, exception={'type': 'PRIVATE', 'message': 'PRIVATE', 'stacktrace': '', 'body': 'PRIVATE'})]
        for raw in examples:
            with self.subTest(length=len(raw)):
                rc, value, body, _ = self.invoke(raw)
                self.assertEqual(rc, 0)
                self.assertEqual(value['reports'], [])
                self.assertEqual(value['structured_records_refused'], 1)
                self.assertTrue(value['input_complete'])
                self.assertNotIn(b'PRIVATE', body)
        for raw in (b'{"message":"PRIVATE"}\n', b'{"exception":null}\n'):
            _, value, body, _ = self.invoke(raw)
            self.assertEqual(value['structured_records_refused'], 0)
            self.assertEqual(value['reports'], [])
            self.assertNotIn(b'PRIVATE', body)

    def test_json_line_overflow_is_detected_before_json_decoder_or_tail_accumulation(self):
        raw = json_event(HEADER + b'X' * M.MAX_LINE_BYTES)
        with patch.object(M, 'structured_traceback', side_effect=AssertionError('must not decode')):
            _, value, _, stream = self.invoke(raw)
        self.assertEqual(value['truncation'], ['line_bytes'])
        self.assertEqual(stream.observed, M.MAX_LINE_BYTES + 1)

    def test_json_report_and_frame_caps_remain_global_with_bounded_unread_tail(self):
        one = HEADER + frame(line=1) * (M.MAX_FRAMES + 2) + b'RuntimeError: PRIVATE\n'
        raw = json_event(one) * (M.MAX_REPORTS + 2)
        _, value, body, stream = self.invoke(raw)
        self.assertEqual(len(value['reports']), M.MAX_REPORTS)
        self.assertEqual(len(value['reports'][0]['frames']), M.MAX_FRAMES)
        self.assertEqual(value['reports'][0]['frames_omitted'], 2)
        self.assertEqual(value['truncation'], ['report_count'])
        self.assertLess(stream.observed, len(raw))
        self.assertNotIn(b'PRIVATE', body)

    def test_json_decoding_and_decoded_stack_cannot_exceed_the_same_physical_bound(self):
        with self.assertRaises(ValueError):
            M.structured_traceback(b'[]')
        with patch.object(M, 'MAX_LINE_BYTES', 32), self.assertRaises(ValueError):
            M.structured_traceback(json_event(b'X' * 33))

    def test_line_overflow_stops_before_reading_or_accumulating_the_rest(self):
        payload = HEADER + b'X' * (M.MAX_LINE_BYTES + 100000)
        _, value, _, stream = self.invoke(payload)
        self.assertEqual(value['status'], 'truncated')
        self.assertEqual(value['truncation'], ['line_bytes'])
        self.assertFalse(value['input_complete'])
        self.assertEqual(stream.observed, len(HEADER) + M.MAX_LINE_BYTES + 1)
        self.assertEqual(value['incomplete_tracebacks'], 1)

    def test_total_input_overflow_reads_only_one_detection_byte(self):
        payload = b'public noise\n' * (M.MAX_INPUT_BYTES // 13 + 100)
        _, value, _, stream = self.invoke(payload)
        self.assertEqual(value['truncation'], ['input_bytes'])
        self.assertEqual(stream.observed, M.MAX_INPUT_BYTES + 1)
        self.assertFalse(value['input_complete'])
        self.assertEqual(value['reports'], [])

    def test_report_limit_stops_without_echoing_the_unread_tail(self):
        one = HEADER + frame() + b'ValueError: PRIVATE\n'
        _, value, body, stream = self.invoke(one * (M.MAX_REPORTS + 5))
        self.assertEqual(len(value['reports']), M.MAX_REPORTS)
        self.assertEqual(value['truncation'], ['report_count'])
        self.assertLess(stream.observed, len(one) * (M.MAX_REPORTS + 5))
        self.assertNotIn(b'PRIVATE', body)

    def test_frame_limit_keeps_bounded_nearest_app_frames_and_omission_count(self):
        raw = HEADER + b''.join(frame(line=n) for n in range(1, M.MAX_FRAMES + 5)) + b'RuntimeError: PRIVATE\n'
        _, value, _, _ = self.invoke(raw)
        report = value['reports'][0]
        self.assertEqual(len(report['frames']), M.MAX_FRAMES)
        self.assertEqual([r['line'] for r in report['frames']], list(range(5, M.MAX_FRAMES + 5)))
        self.assertEqual(report['frames_omitted'], 4)

    def test_maximum_report_population_stays_within_physical_output_bound(self):
        longest_path = max(M.APPROVED_PATHS, key=len)
        longest_class = max(M.CLOSED_CLASSES, key=len)
        one = HEADER + frame(longest_path, 9999999) * M.MAX_FRAMES + longest_class.encode() + b': PRIVATE\n'
        rc, value, body, _ = self.invoke(one * M.MAX_REPORTS)
        self.assertEqual(rc, 0)
        self.assertEqual(len(value['reports']), M.MAX_REPORTS)
        self.assertLessEqual(len(body), M.MAX_OUTPUT_BYTES)

    def test_incomplete_traceback_is_honest_without_inventing_an_exception(self):
        _, value, _, _ = self.invoke(HEADER + frame())
        self.assertEqual(value['reports'], [])
        self.assertEqual(value['incomplete_tracebacks'], 1)
        self.assertTrue(value['input_complete'])

    def test_input_failure_is_closed_and_does_not_expose_exception_args(self):
        class FailedInput:
            def readline(self, _size):
                raise OSError('PRIVATE_CONFIG PRIVATE_JWT')
        output = BoundedOutput()
        self.assertEqual(M.main([], FailedInput(), output), 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value['status'], 'input_unavailable')
        self.assertEqual(value['input_error_class'], 'OSError')
        self.assertNotIn(b'PRIVATE', output.getvalue())

    def test_cli_rejects_all_file_or_other_arguments_without_reading_them(self):
        class ForbiddenInput:
            def readline(self, _size):
                raise AssertionError('must not read')
        output = BoundedOutput()
        self.assertEqual(M.main(['PRIVATE_FILENAME'], ForbiddenInput(), output), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'invalid_arguments')
        self.assertNotIn(b'PRIVATE', output.getvalue())

    def test_nonbinary_or_oversized_reader_contract_is_closed(self):
        class InvalidInput:
            def __init__(self, oversized):
                self.oversized = oversized
            def readline(self, size):
                return b'X' * (size + 1) if self.oversized else 'PRIVATE_TEXT_STREAM'
        for oversized in (False, True):
            output = BoundedOutput()
            with self.subTest(oversized=oversized):
                self.assertEqual(M.main([], InvalidInput(oversized), output), 1)
                value = json.loads(output.getvalue())
                self.assertEqual(value['input_error_class'], 'TypeError')
                self.assertEqual(value['reports'], [])
                self.assertNotIn(b'PRIVATE', output.getvalue())

    def test_nonascii_frame_and_nonexception_log_identifier_are_not_echoed(self):
        raw = (HEADER + '  File "/app/api/خصوصی.py", line 21, in PRIVATE\n'.encode()
               + b'Authorization: PRIVATE_TOKEN\n' + b'ValueError: PRIVATE_BODY\n')
        _, value, body, _ = self.invoke(raw)
        self.assertEqual(value['reports'], [{'exception_class': 'ValueError', 'frames': [], 'frames_omitted': 1}])
        self.assertNotIn(b'PRIVATE', body)
        self.assertNotIn('خصوصی'.encode(), body)

    def test_output_capacity_refusal_is_closed_without_a_partial_report(self):
        # Lower the production ceiling only in this owned writer fixture.
        with patch.object(M, 'MAX_OUTPUT_BYTES', 128):
            rc, value, body, _ = self.invoke(HEADER + frame() + b'ValueError: PRIVATE\n')
        self.assertEqual(rc, 1)
        self.assertEqual(value['status'], 'output_bound')
        self.assertEqual(value['reports'], [])
        self.assertNotIn(b'PRIVATE', body)

    def test_short_write_write_failure_and_flush_failure_never_claim_collection(self):
        class ShortOutput(BoundedOutput):
            def write(self, raw):
                return super().write(raw[:-1])
        class FailedOutput(BoundedOutput):
            def __init__(self, fail_flush):
                super().__init__()
                self.fail_flush = fail_flush
            def write(self, raw):
                if not self.fail_flush:
                    raise OSError('PRIVATE_WRITE_ARGUMENTS')
                return super().write(raw)
            def flush(self):
                raise OSError('PRIVATE_FLUSH_ARGUMENTS')
        for output in (ShortOutput(), FailedOutput(False), FailedOutput(True)):
            with self.subTest(writer=type(output).__name__):
                self.assertEqual(M.main([], BoundedInput(HEADER + frame() + b'ValueError: PRIVATE\n'), output), 1)
                self.assertNotIn(b'PRIVATE', output.getvalue())

    def test_actual_cli_entrypoint_reads_only_owned_stdin_and_emits_closed_json(self):
        source = BoundedInput(HEADER + frame('api/routers/invitations.py', 327) + b'RuntimeError: PRIVATE\n')
        output = BoundedOutput()
        with patch.object(sys, 'stdin', SimpleNamespace(buffer=source)), \
             patch.object(sys, 'stdout', SimpleNamespace(buffer=output)), \
             patch.object(sys, 'argv', [str(SOURCE)]), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(SOURCE), run_name='__main__')
        self.assertEqual(stopped.exception.code, 0)
        value = json.loads(output.getvalue())
        self.assertEqual(value['reports'][0]['exception_class'], 'RuntimeError')
        self.assertEqual(value['reports'][0]['frames'], [{'path': 'api/routers/invitations.py', 'line': 327}])
        self.assertNotIn(b'PRIVATE', output.getvalue())

    def test_normal_browser_workflows_filter_only_disposable_app_logs_before_always_upload(self):
        name = 'Collect closed browser CI backend exception metadata'
        for workflow in ('merge-gate.yml', 'pre-release-gate.yml'):
            with self.subTest(workflow=workflow):
                jobs = yaml.safe_load((SOURCE.parents[1] / '.github/workflows' / workflow).read_text())['jobs']
                owners = [key for key, job in jobs.items() if any(step.get('name') == name for step in job.get('steps', []))]
                self.assertEqual(owners, ['frontend-browser-matrix'])
                job = jobs['frontend-browser-matrix']
                self.assertEqual(job['timeout-minutes'], 75)
                self.assertEqual(job['strategy']['matrix']['browser'], ['chromium', 'firefox', 'webkit'])
                self.assertEqual(job['strategy']['matrix']['shard'], [1, 2, 3, 4])
                steps = job['steps']
                collect = next(step for step in steps if step.get('name') == name)
                self.assertEqual(collect['if'], 'always() && (failure() || cancelled())')
                self.assertEqual(collect['timeout-minutes'], 1)
                shell = collect['run'].replace('\\\n', '')
                self.assertIn('timeout --signal=TERM --kill-after=2s 15s', shell)
                self.assertIn('docker compose logs --no-color --no-log-prefix --tail 2000 app 2>&1 |', shell)
                self.assertIn('timeout --signal=TERM --kill-after=2s 20s', shell)
                self.assertIn('python3 -I -B scripts/extract_browser_ci_backend_exceptions.py', shell)
                self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', shell)
                self.assertIn('"${pipeline_status[0]}" "${pipeline_status[1]}"', shell)
                self.assertNotIn('tee ', shell)
                self.assertNotIn('app migration db redis', shell)
                self.assertFalse(any(step.get('name') == 'Print backend logs on failure' for step in steps))
                browser = next(step for step in steps if step.get('name') == 'Run Playwright browser gate')
                self.assertNotIn('continue-on-error', browser)
                upload = next(step for step in steps if step.get('name') == 'Upload Playwright artifacts')
                self.assertEqual(upload['if'], 'always()')
                for path in ('tmp/browser-ci-backend-exceptions.json', 'tmp/browser-ci-backend-exception-status.json'):
                    self.assertIn(path, upload['with']['path'].splitlines())
                self.assertLess(steps.index(browser), steps.index(collect))
                self.assertLess(steps.index(collect), steps.index(upload))


if __name__ == '__main__':
    unittest.main()
