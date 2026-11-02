"""Finite offline failure classification; no Provider or successful proof fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

import pytest

from scripts import two_site_proposal_runner as runner
from scripts import two_site_proposal_pipeline as pipeline


CID = '00000000-0000-4000-8000-000000000704'
OUTPUT_ERROR = ('Your previous response was cut off because it exceeded the output token limit\n'
                'Please continue from where you left off, keeping your response shorter\nRetries remaining: 3')
QUOTA_ERROR = 'RESOURCE_EXHAUSTED quota exhausted; reset at 2026-11-03T14:00:00Z'


class FixedTime:
    @staticmethod
    def now(tz):
        return datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def encode(events):
    return '\n'.join(json.dumps(e) for e in events) + '\n'


def error_case(message, *, model=None, first=False):
    model = model or runner.WORKER_MODEL
    manifest, wires = runner._framed_input_plan('x' * 180000)
    init = {'event': 'init', 'conversation_id': CID, 'init': {'model': model}}
    terminal = {'event': 'result', 'result': {
        'status': 'ERROR', 'conversation_id': CID, 'num_turns': 1 if first else 2,
        'error': message, 'response': '', 'usage': {'input_tokens': 0, 'output_tokens': 0}}}
    events = [init]
    if not first:
        events.append({'event': 'result', 'result': {
            'status': 'SUCCESS', 'conversation_id': CID, 'num_turns': 1,
            'structured_output': deepcopy(manifest['frames'][0]['ack'])}})
    events.append(terminal)
    return manifest, wires, events, model


@pytest.mark.parametrize('first', [False, True])
def test_real_output_limit_text_keeps_legacy_nonretryable_classification(first):
    manifest, _, events, model = error_case(OUTPUT_ERROR, first=first)
    events[-1]['result']['response'] = 'incomplete candidate must never be admitted'
    with pytest.raises(runner.ProposalError, match='^MODEL_OUTPUT_TRUNCATED:') as legacy:
        runner._parse_stream(encode([events[0], events[-1]]), {}, {}, 'worker', expected_model=model)
    with pytest.raises(runner.ProposalError) as framed:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    assert type(framed.value) is type(legacy.value) is runner.ProposalError
    assert str(framed.value) == str(legacy.value)
    assert not hasattr(framed.value, 'quota_evidence')


@pytest.mark.parametrize('first', [False, True])
def test_strict_quota_reaches_existing_observational_pipeline_extractor(tmp_path, monkeypatch, first):
    monkeypatch.setattr(runner, 'datetime', FixedTime)
    manifest, _, events, model = error_case(QUOTA_ERROR, model=runner.MODEL, first=first)
    with pytest.raises(runner._FramedQuotaError) as caught:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    evidence = caught.value.quota_evidence
    expected = runner.classify_gemini_quota_stream(encode([events[0], events[-1]]), model,
                                                  FixedTime.now(timezone.utc))
    assert evidence == expected and evidence['kind'] == 'QUOTA_RESET_KNOWN'
    # This is synthetic BLOCKED observational metadata, never an accepted unit.
    role_dir = tmp_path / 'quota-observation'; role_dir.mkdir()
    status = {'run_id': 'quota-observation', 'role': 'verifier', 'head': '1' * 40,
              'model': model, 'state': 'BLOCKED', 'conversation_id': CID,
              'quota_evidence': evidence}
    runner.write_json(role_dir / 'status.json', status)
    actual = pipeline._extract_bound_quota_evidence(tmp_path, role_dir, status['run_id'],
                                                    'verifier', status['head'])
    assert actual[0] == evidence
    assert actual[1] == {'role': 'verifier', 'run_id': status['run_id'],
                         'status_sha256': runner.file_sha256(role_dir / 'status.json')}


@pytest.mark.parametrize('fault', ['nonzero-usage', 'partial-response', 'extra-current-event'])
def test_quota_projection_never_erases_actual_usage_partial_response_or_events(monkeypatch, fault):
    monkeypatch.setattr(runner, 'datetime', FixedTime)
    manifest, _, events, model = error_case(QUOTA_ERROR)
    if fault == 'nonzero-usage':
        events[-1]['result']['usage']['input_tokens'] = 1
    elif fault == 'partial-response':
        events[-1]['result']['response'] = 'partial output'
    else:
        events.insert(-1, {'event': 'agent_response', 'conversation_id': CID, 'text': 'actual event'})
    with pytest.raises(runner.ProposalError) as caught:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    assert type(caught.value) is runner.ProposalError
    assert not hasattr(caught.value, 'quota_evidence')


@pytest.mark.parametrize('fault', ['foreign-cid', 'foreign-init', 'bad-ordinal', 'bool-ordinal',
                                  'malformed-result', 'denial', 'tool', 'bad-ack'])
def test_existing_identity_order_shape_ack_tool_denial_guards_win_before_classification(fault):
    manifest, _, events, model = error_case(OUTPUT_ERROR)
    if fault == 'foreign-cid':
        events[-1]['result']['conversation_id'] = 'foreign'
    elif fault == 'foreign-init':
        events[0]['init']['model'] = 'foreign-model'
    elif fault == 'bad-ordinal':
        events[-1]['result']['num_turns'] = 1
    elif fault == 'bool-ordinal':
        events[-1]['result']['num_turns'] = True
    elif fault == 'malformed-result':
        events[-1]['result'] = 'not an envelope'
    elif fault == 'denial':
        events[-1]['result']['denied_actions'] = ['denied']
    elif fault == 'tool':
        events.insert(-1, {'event': 'step_update', 'step_update': {'tool_name': 'finish'}})
    else:
        events[1]['result']['structured_output']['summary'] += 'wrong'
    with pytest.raises(runner.ProposalError) as caught:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    assert not str(caught.value).startswith('MODEL_OUTPUT_TRUNCATED:')
    assert not hasattr(caught.value, 'quota_evidence')


def test_generic_error_stays_rejected_and_legacy_retryable_error_grants_no_new_retry():
    manifest, _, events, model = error_case('backend refused this request')
    with pytest.raises(runner.ProposalError) as legacy:
        runner._parse_stream(encode([events[0], events[-1]]), {}, {}, 'worker', expected_model=model)
    with pytest.raises(runner.ProposalError) as framed:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    assert type(framed.value) is runner.ProposalError and str(framed.value) == str(legacy.value)
    events[-1]['result']['error'] = 'improperly formatted function call'
    with pytest.raises(runner.RetryableProviderOutputError):
        runner._parse_stream(encode([events[0], events[-1]]), {}, {}, 'worker', expected_model=model)
    with pytest.raises(runner.ProposalError) as framed:
        runner._framed_stream_projection(encode(events), manifest, model, CID, complete=False)
    assert type(framed.value) is runner.ProposalError
    assert not isinstance(framed.value, runner.RetryableProviderSessionError)


def test_actual_local_error_capture_retains_whole_transcript_and_refuses_premature_error(tmp_path):
    for premature in (False, True):
        manifest, wires, events, model = error_case(OUTPUT_ERROR, first=premature)
        folder = tmp_path / str(premature); folder.mkdir()
        body = 'import sys,json,time\n' + 'events=' + repr(events) + '\n'
        body += 'print(json.dumps(events[0]),flush=True)\n'
        if premature:
            body += 'print(json.dumps(events[-1]),flush=True)\ntime.sleep(5)\n'
        else:
            body += ('for event in events[1:]:\n'
                     ' assert sys.stdin.buffer.readline()\n'
                     ' print(json.dumps(event),flush=True)\n')
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', '-c', body],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, cwd=folder, env={'PATH': os.defpath, 'LANG': 'C'})
        try:
            expected = 'preceded its complete input' if premature else '^MODEL_OUTPUT_TRUNCATED:'
            with pytest.raises(runner.ProposalError, match=expected):
                runner._capture_framed_process(process, wires, manifest, folder / 'out',
                                               folder / 'err', 3, model, CID)
            assert process.poll() is not None
            assert all(s.closed for s in (process.stdin, process.stdout, process.stderr))
            raw = (folder / 'out').read_text()
            assert json.loads(raw.splitlines()[-1]) == events[-1]
            if not premature:
                assert raw == encode(events)
                assert len([json.loads(x) for x in raw.splitlines() if json.loads(x)['event'] == 'result']) == 2
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9)
            process.wait(timeout=3)
