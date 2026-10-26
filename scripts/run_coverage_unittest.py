#!/usr/bin/env python3
"""Disjoint hosted/root fixture lanes for the existing required coverage suite."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
import argparse
import base64
import io
import json
import os
import re
import shutil
import signal
import sys
import unittest
from unittest.mock import patch

ROOT_MODULES = tuple('tests.test_' + name for name in (
    'native_brotli_install', 'native_dashboard_tls', 'native_runtime_vulnerability_audit',
    'security_consumer_bootstrap', 'web_runtime_vulnerability_audit', 'web_market_hot3',
    'web_market_hot_preflight', 'web_market_managed_config', 'web_engine_current_controller',
    'coverage_root_fixture_lane',
    'datastore_candidate_stage', 'datastore_candidate_contract_completion',
    'datastore_candidate_stage_completion', 'datastore_candidate_stage_entry', 'datastore_candidate_images',
    'datastore_candidate_images_v2',
    'security_legacy_parking', 'security_legacy_parking_completion',
    'market_candidate_images', 'market_candidate_images_v2', 'market_candidate_images_v3',
    'canonical_app_candidate_images', 'image_store_health_entries', 'canonical_app_resource_contract',
    'canonical_app_activation', 'normal_app_restore_admission', 'normal_market_activation', 'normal_datastore_activation',
    'capture_conflict_diagnostic', 'capture_conflict_normal_owned',
    'normal_market_role_admission', 'normal_app_restore_rehearsal',
    'normal_app_restore_producer', 'normal_app_activation'))
AMBIENT = ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
    'subprocess.check_output', 'os.system', 'os.popen', 'os.fork', 'os.forkpty',
    'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg', 'socket.socket',
    'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen',
    'ctypes.CDLL', 'ctypes.PyDLL', 'resource.setrlimit', 'signal.signal', 'signal.alarm',
    'signal.setitimer')
MAX_ENVELOPE = 64 * 1024**2
ROOT_PRIVILEGES = {'CapEff': 1, 'CapPrm': 1, 'CapBnd': 1, 'CapInh': 0, 'CapAmb': 0, 'NoNewPrivs': 1}


def need(ok, code):
    if not ok: raise RuntimeError(code)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def read(path, maximum=MAX_ENVELOPE):
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    need(len(raw) <= maximum, 'coverage_fixture_input_bound')
    return raw


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from flatten(item)
        else: yield item


def partition(suite):
    hosted, root = [], []
    seen = set()
    for test in flatten(suite):
        name = test.id()
        if any(name.startswith(module + '.') for module in ROOT_MODULES):
            need(name not in seen, 'duplicate_coverage_test_id'); seen.add(name); root.append(test)
        else: hosted.append(test)
    root_ids = sorted(test.id() for test in root)
    need(all(any(name.startswith(module + '.') for name in root_ids) for module in ROOT_MODULES),
         'required_root_fixture_module_missing')
    return hosted, root


def expectation(commit, root):
    need(type(commit) is str and re.fullmatch('[a-f0-9]{40}', commit), 'coverage_source_commit')
    ids = sorted(test.id() for test in root)
    need(ids and len(ids) == len(set(ids)), 'coverage_root_inventory')
    return {'schema': 'coverage_root_fixture_expectation/1', 'source_commit': commit,
            'modules': list(ROOT_MODULES), 'test_ids': ids}


def expected_ok(value, commit):
    need(type(value) is dict and set(value) == {'schema', 'source_commit', 'modules', 'test_ids'}
         and value['schema'] == 'coverage_root_fixture_expectation/1' and value['source_commit'] == commit
         and value['modules'] == list(ROOT_MODULES) and type(value['test_ids']) is list
         and all(type(name) is str and any(name.startswith(module + '.') for module in ROOT_MODULES)
                 for name in value['test_ids'])
         and value['test_ids'] == sorted(set(value['test_ids']))
         and all(any(name.startswith(module + '.') for name in value['test_ids']) for module in ROOT_MODULES),
         'coverage_root_expectation')
    return value


def summary(result):
    return {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'skipped': len(result.skipped), 'unexpected_successes': len(result.unexpectedSuccesses),
            'expected_failures': len(result.expectedFailures),
            'successful': result.wasSuccessful()}


def complete(result, count):
    return result.wasSuccessful() and result.testsRun == count


class BoundedLog(io.StringIO):
    def __init__(self):
        super().__init__(); self.bytes = 0

    def write(self, value):
        self.bytes += len(value.encode('utf-8', 'backslashreplace'))
        need(self.bytes <= 8 * 1024**2, 'coverage_fixture_log_bound')
        return super().write(value)


def hosted(args):
    root = Path.cwd(); sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py', top_level_dir='.')
    ordinary, privileged = partition(suite)
    expected = expectation(args.commit, privileged)
    args.expectation.write_bytes(encode(expected))
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(ordinary))
    report = {'schema': 'coverage_hosted_unittest/1', 'source_commit': args.commit,
        'hosted_test_ids': sorted(test.id() for test in ordinary), 'root_test_ids': expected['test_ids'],
        'root_modules': list(ROOT_MODULES), 'result': summary(result), 'root_lane_required': True,
        'hosted_suite_complete': result.testsRun == len(ordinary)}
    args.report.write_bytes(encode(report))
    return 0 if complete(result, len(ordinary)) else 1


def privileges_ok(value):
    return type(value) is dict and set(value) == set(ROOT_PRIVILEGES) and all(
        type(value[name]) is int and value[name] == expected for name, expected in ROOT_PRIVILEGES.items())


def root_guard():
    # A fixture coordinator must be an ordinary owned child, not namespace init.
    # Production owner guards deliberately reject PID 1; Docker --init supplies
    # the separate reaper without weakening that contract in tests.
    need(os.getpid() > 1, 'root_fixture_separate_init_required')
    need(os.geteuid() == os.getegid() == 0 and Path('/.dockerenv').is_file()
         and not os.path.lexists('/var/run/docker.sock')
         and os.statvfs('/').f_flag & os.ST_RDONLY, 'isolated_root_fixture_container_required')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    # Real ownership drift fixtures need CAP_CHOWN; every other capability
    # remains absent, including inherited and ambient capabilities.
    privileges = {name: int(status[name].strip(), 10 if name == 'NoNewPrivs' else 16) for name in ROOT_PRIVILEGES}
    need(privileges_ok(privileges), 'root_fixture_privileges')
    need({p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}, 'root_fixture_network')
    return privileges


def root_lane(args):
    privileges = root_guard(); os.umask(0o077)
    import coverage
    collector = coverage.Coverage.current()
    need(collector is not None and collector.config.data_file == '/work/.coverage.root-fixtures',
         'root_fixture_process_coverage_required')
    def expired(*_): raise RuntimeError('root_fixture_wall_deadline')
    signal.signal(signal.SIGALRM, expired); signal.alarm(840)
    reviewed = Path('/reviewed'); work = Path('/work/source')
    need(not work.exists(), 'root_fixture_work_preexisting')
    shutil.copytree(reviewed, work, symlinks=True); work.chmod(0o700)
    need(all(p.lstat().st_uid == p.lstat().st_gid == 0 for p in work.rglob('*')), 'root_fixture_source_ownership')
    os.chdir(work); sys.path.insert(0, str(work))
    expected = expected_ok(json.loads(read(args.expectation, 4 * 1024**2)), args.commit)
    log = BoundedLog()
    try:
        with ExitStack() as fence, redirect_stdout(log), redirect_stderr(log):
            for target in AMBIENT: fence.enter_context(patch(target, side_effect=AssertionError('root_fixture_ambient_forbidden')))
            suite = unittest.defaultTestLoader.loadTestsFromNames(ROOT_MODULES)
            tests = list(flatten(suite))
            need(expectation(args.commit, tests) == expected
                 and all(any(test.id().startswith(module + '.') for module in ROOT_MODULES) for test in tests),
                 'root_fixture_identity_mismatch')
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite(tests))
    finally:
        collector.stop(); collector.save()
    data = read(Path('/work/.coverage.root-fixtures'), 32 * 1024**2)
    report = {'schema': 'coverage_root_fixture_result/2', 'source_commit': args.commit,
        'test_ids': expected['test_ids'], 'modules': list(ROOT_MODULES), 'uid': os.geteuid(),
        'fixture_privileges': privileges,
        'result': summary(result), 'log': log.getvalue(), 'coverage_sha256': sha256(data).hexdigest(),
        'coverage_base64': base64.b64encode(data).decode(), 'production_inputs_present': False}
    raw = encode(report); need(len(raw) <= MAX_ENVELOPE, 'root_fixture_envelope_bound')
    sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
    return 0 if complete(result, len(expected['test_ids'])) and not result.skipped and not result.expectedFailures else 1


def ingest(args):
    expected = expected_ok(json.loads(read(args.expectation, 4 * 1024**2)), args.commit)
    report = json.loads(read(args.envelope))
    need(type(report) is dict and set(report) == {'schema', 'source_commit', 'test_ids', 'modules', 'uid', 'fixture_privileges',
        'result', 'log', 'coverage_sha256', 'coverage_base64', 'production_inputs_present'}
        and report['schema'] == 'coverage_root_fixture_result/2' and report['source_commit'] == args.commit
        and report['modules'] == list(ROOT_MODULES) and report['test_ids'] == expected['test_ids']
        and type(report['uid']) is int and report['uid'] == 0 and report['production_inputs_present'] is False
        and privileges_ok(report['fixture_privileges']),
        'root_fixture_result_binding')
    outcome = report['result']
    need(type(outcome) is dict and set(outcome) == {'run', 'failures', 'errors', 'skipped', 'unexpected_successes', 'expected_failures', 'successful'}
        and all(type(outcome[k]) is int and outcome[k] >= 0 for k in outcome if k != 'successful')
        and type(outcome['successful']) is bool and outcome['run'] == len(expected['test_ids']), 'root_fixture_result_shape')
    need(type(report['log']) is str and len(report['log'].encode()) <= 8 * 1024**2, 'root_fixture_log_bound')
    need(type(report['coverage_base64']) is str and len(report['coverage_base64']) <= 45 * 1024**2, 'root_fixture_coverage_bound')
    data = base64.b64decode(report.pop('coverage_base64'), validate=True)
    need(data.startswith(b'SQLite format 3\x00') and len(data) <= 32 * 1024**2
         and sha256(data).hexdigest() == report['coverage_sha256'], 'root_fixture_coverage_identity')
    Path('.coverage.root-fixtures').write_bytes(data)
    Path('tmp/backend-root-fixtures.log').write_text(report.pop('log'))
    args.report.write_bytes(encode(report))
    return 0 if outcome['successful'] and not any(outcome[k] for k in ('failures', 'errors', 'skipped', 'unexpected_successes', 'expected_failures')) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', choices=('hosted', 'root', 'ingest'), required=True)
    parser.add_argument('--commit', required=True); parser.add_argument('--expectation', type=Path, required=True)
    parser.add_argument('--report', type=Path); parser.add_argument('--envelope', type=Path)
    args = parser.parse_args()
    need(re.fullmatch('[a-f0-9]{40}', args.commit), 'coverage_source_commit')
    return {'hosted': hosted, 'root': root_lane, 'ingest': ingest}[args.lane](args)


if __name__ == '__main__': raise SystemExit(main())
