#!/usr/bin/env python3
"""Separate Golden-only continuation; never retry the collector or rewrite history."""
from datetime import datetime
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import time
from types import ModuleType

REFRESH_SHA = '98d195f6cd39e8ec1efe07264cd893d0eb56de7017ec3a6890d02e08dc1e35ef'
PARENT = Path('/srv/trading-bot/backups/security-brotli-native-refresh-20261024-01/receipt.json')
PARENT_SHA = '5955ba42d551f85bfdd1d4637240b7e06c68d34e1a4956cd7cc42b6adce441c7'
ROOT = Path('/srv/trading-bot/backups/security-brotli-native-golden-resume-20261024-02')
CODES = frozenset({'resume_source_metadata', 'resume_source_changed', 'resume_parent_changed',
    'resume_parent_not_partial', 'resume_collector_not_ready', 'resume_golden_identity_changed',
    'resume_old_collector_alive', 'resume_job_pending', 'resume_scope_changed',
    'resume_new_window_required', 'resume_write_outside_journal', 'resume_collector_identity_changed',
    'resume_only_one_golden_restart_allowed', 'resume_already_attempted',
    'native_brotli_refresh_incomplete', 'refresh_window_too_short', 'refresh_window_expired',
    'fresh_window_required'})

def require(ok, code):
    if not ok:
        raise RuntimeError(code)

def load_refresh(path):
    require(path.resolve(strict=True) == path, 'resume_source_metadata')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 65536, 'resume_source_metadata')
        raw = os.read(fd, 65537)
        identity = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_mtime_ns,s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat())
                and sha256(raw).hexdigest() == REFRESH_SHA, 'resume_source_changed')
    finally:
        os.close(fd)
    module = ModuleType('reviewed_original_native_refresh')
    exec(compile(raw, '<reviewed-original-native-refresh>', 'exec'), module.__dict__)
    return module

def parent_record(N):
    raw = N.read(PARENT)
    require(sha256(raw).hexdigest() == PARENT_SHA, 'resume_parent_changed')
    value = json.loads(raw)
    require(value['schema'] == 'native_brotli_consumer_refresh/1'
            and value['phase'] == 'partial_refresh_incident_open' and not value['completed_units']
            and value['current_unit'] == 'coin-public-market-telegram.service'
            and value['native_brotli_runtime_verified'] is False
            and value['security_transition_completed'] is False, 'resume_parent_not_partial')
    return raw, N.identity(PARENT.lstat()), value

def preflight(M, N):
    raw, identity, prior = parent_record(N)
    _, _, _, units, _, processes = M.before_state(N)
    first, second = M.UNITS
    old = prior['before_processes'][prior['before_units'][first]['MainPID']]
    try:
        require(M.start_ticks(old['pid']) != old['start_ticks'], 'resume_old_collector_alive')
    except FileNotFoundError:
        pass
    pid = int(units[first]['MainPID'])
    require(M.module_is_patched(processes[pid])
            and M.journal_ready(N, first, pid, prior['requested_monotonic_us'])
            and M.heartbeat_ready(N, datetime.fromisoformat(prior['requested_at_utc'])), 'resume_collector_not_ready')
    require(units[second] == prior['before_units'][second]
            and not M.module_is_patched(processes[int(units[second]['MainPID'])]), 'resume_golden_identity_changed')
    for unit in M.UNITS:
        require(not N.command(['/usr/bin/systemctl','show',unit,'--property=Job','--value']).strip(), 'resume_job_pending')
    require(N.read(PARENT) == raw and N.identity(PARENT.lstat()) == identity, 'resume_parent_changed')
    require(not os.path.lexists(ROOT), 'resume_already_attempted')
    return raw, identity, prior, units[first]

def execute(M, N, action, window=None):
    require(M.UNITS == ('coin-public-market-telegram.service','golden_bot.service'), 'resume_scope_changed')
    raw, identity, prior, collector = preflight(M, N)
    if action == 'check':
        return {'status':'golden_resume_checked','collector_application_ready':True,
                'collector_patched_mapping':True,'only_golden_requires_refresh':True,
                'parent_partial_receipt_preserved':True,'runtime_changed':False,'security_transition_completed':False}
    require(window is not None and window.start >= M.utc(prior['window_end_utc'].replace('+00:00','Z')),
            'resume_new_window_required')
    original_command, original_write, original_root = N.command, N.write, M.ROOT
    requests = 0
    def bound_command(argv, **kwargs):
        nonlocal requests
        if argv[:3] == ['/usr/bin/systemctl','--no-block','restart']:
            require(argv == ['/usr/bin/systemctl','--no-block','restart','golden_bot.service'] and requests == 0,
                    'resume_only_one_golden_restart_allowed')
            requests += 1
        return original_command(argv, **kwargs)
    def bound_write(path, payload, *, exclusive):
        require(path == ROOT/'receipt.json', 'resume_write_outside_journal')
        require(N.read(PARENT) == raw and N.identity(PARENT.lstat()) == identity, 'resume_parent_changed')
        value = json.loads(payload)
        require(value['before_units'][M.UNITS[0]] == collector, 'resume_collector_identity_changed')
        value.update(schema='native_brotli_golden_resume/1', parent_partial_receipt_sha256=PARENT_SHA,
            parent_partial_receipt_preserved=True, scope='golden_only_after_verified_collector',
            collector_patched_ready_before_admission=True, collector_restart_requested=False)
        original_write(path, (json.dumps(value,sort_keys=True)+'\n').encode(), exclusive=exclusive)
    N.command, N.write, M.ROOT = bound_command, bound_write, ROOT
    try:
        result = M.action(N, 'refresh', window)
        require(requests == 1, 'resume_only_one_golden_restart_allowed')
        require(N.read(PARENT) == raw and N.identity(PARENT.lstat()) == identity, 'resume_parent_changed')
        return {**result, 'collector_restart_requested':False,'parent_partial_receipt_preserved':True}
    finally:
        N.command, N.write, M.ROOT = original_command, original_write, original_root

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('refresh_helper',type=Path);parser.add_argument('install_helper',type=Path)
    parser.add_argument('action',choices=('check','refresh'),nargs='?',default='check')
    parser.add_argument('--window-start',default='');parser.add_argument('--window-end',default='')
    args = parser.parse_args();M=load_refresh(args.refresh_helper);N=M.load_installer(args.install_helper)
    window=M.Window(args.window_start,args.window_end) if args.action=='refresh' else None
    N.DEADLINE=window.monotonic_end if window else time.monotonic()+180
    def interrupted(*_):
        raise RuntimeError('refresh_window_expired')
    for number in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT):signal.signal(number,interrupted)
    signal.alarm(max(1,int(window.remaining())) if window else 180)
    print(json.dumps(execute(M,N,args.action,window),sort_keys=True))

if __name__ == '__main__':
    try:main()
    except Exception as error:
        code=error.args[0] if type(error) is RuntimeError and error.args else None
        print(json.dumps({'status':'golden_resume_refused','reason_code':code if isinstance(code,str) and code in CODES else type(error).__name__,
                          'completion_unverified':True,'security_transition_completed':False}))
        raise SystemExit(1) from None
