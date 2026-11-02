#!/usr/bin/env python3
"""Refresh only two Native Brotli consumers in a new explicit UTC window."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import time
from types import ModuleType

INSTALL_SHA = '1fbcadd259911575c1538e44e9336a34aebf822f5736af9863254f8f53ad4625'
ROOT = Path('/srv/trading-bot/backups/security-brotli-native-refresh-20261024-01')
PROC = Path('/proc')
UNITS = ('coin-public-market-telegram.service', 'golden_bot.service')
HEALTH = Path('/srv/trading-bot/production-data/coin-intelligence/estimator-live/public-market/public-telegram-health.json')
GOLDEN_DB = Path('/root/golden_bot/trades.db')
MODULE = Path('/usr/lib/python3/dist-packages/_brotli.cpython-312-x86_64-linux-gnu.so')
TARGETS = {'python_module': MODULE, **{name: Path('/usr/lib/x86_64-linux-gnu/libbrotli'+name+'.so.1')
                                   for name in ('dec', 'enc', 'common')}}
PROPERTIES = 'Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,KillMode,Restart,NRestarts,Result,ExecMainStatus,TimeoutStartUSec,TimeoutStopUSec,NeedDaemonReload'
STABLE_SECONDS = 10
MAX_SCAN_BYTES = 64 * 1024**2


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def load_installer(path):
    path = Path(path)
    require(path.resolve(strict=True) == path, 'installer_source_redirected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= 65536, 'installer_source_metadata')
        raw = os.read(fd, 65537)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == info.st_size and identity(info) == identity(os.fstat(fd)) == identity(path.lstat())
                and sha256(raw).hexdigest() == INSTALL_SHA, 'installer_source_changed')
    finally:
        os.close(fd)
    module = ModuleType('reviewed_native_brotli_installer')
    exec(compile(raw, '<reviewed-native-brotli-installer>', 'exec'), module.__dict__)
    return module


def utc(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value), 'window_utc_required')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


class Window:
    def __init__(self, start, end):
        self.start, self.end = utc(start), utc(end)
        now = datetime.now(timezone.utc)
        require(0 < (self.end-self.start).total_seconds() <= 600 and self.start <= now < self.end, 'fresh_window_required')
        self.monotonic_end = time.monotonic() + (self.end-now).total_seconds()

    def remaining(self):
        now = datetime.now(timezone.utc)
        remaining = min((self.end-now).total_seconds(), self.monotonic_end-time.monotonic())
        require(self.start <= now < self.end and remaining > 0, 'refresh_window_expired')
        return remaining


def duration(value):
    require(isinstance(value, str), 'unit_timeout_schema')
    parts = re.findall(r'([0-9]+)(us|ms|min|s|h)', value)
    require(parts and ''.join(number+unit for number, unit in parts) == value.replace(' ', ''), 'unit_timeout_schema')
    return sum(int(number)*{'us':.000001, 'ms':.001, 's':1, 'min':60, 'h':3600}[unit] for number, unit in parts)


def units(N):
    result = {}
    for name in UNITS:
        raw = N.command(['/usr/bin/systemctl', 'show', name, '--property='+PROPERTIES])
        value = dict(line.split('=', 1) for line in raw.splitlines())
        require(value['Id'] == name and value['LoadState'] == 'loaded' and value['UnitFileState'] == 'enabled'
                and value['KillMode'] == 'control-group' and value['Restart'] == 'always'
                and value['NeedDaemonReload'] == 'no', 'native_unit_policy_changed')
        result[name] = value
    return result


def policy(value):
    return {k:v for k,v in value.items() if k not in {'MainPID', 'NRestarts', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus'}}


def proc_read(pid, name, maximum):
    require(type(pid) is int and pid > 0 and name in {'maps', 'stat'}, 'proc_scope_invalid')
    fd = os.open(PROC/str(pid)/name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd); require(stat.S_ISREG(info.st_mode), 'proc_schema')
        raw = bytearray()
        while chunk := os.read(fd, min(65536, maximum+1-len(raw))):
            raw.extend(chunk); require(len(raw) <= maximum, 'proc_output_bound')
        return bytes(raw)
    finally:
        os.close(fd)


def start_ticks(pid):
    raw = proc_read(pid, 'stat', 8192).decode()
    require(raw.startswith(str(pid)+' (') and ')' in raw, 'proc_stat_schema')
    fields = raw.rsplit(')', 1)[1].split()
    require(len(fields) > 19 and fields[19].isdigit(), 'proc_stat_schema')
    return int(fields[19])


def current_targets(N):
    result = {}
    for tag, source in TARGETS.items():
        resolved = source.resolve(strict=True)
        require(resolved.parent == source.parent and resolved.name.startswith(source.name.split('.so')[0]), 'native_library_redirected')
        value = resolved.stat()
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022 and value.st_nlink == 1,
                'native_library_metadata')
        result[tag] = {'path': str(resolved), 'device': f'{os.major(value.st_dev):x}:{os.minor(value.st_dev):x}',
                       'inode': value.st_ino}
    return result


def map_records(raw, targets):
    records = {}
    for line in raw.decode(errors='replace').splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        path = fields[5].removesuffix(' (deleted)')
        for tag, target in targets.items():
            if path != target['path']:
                continue
            require(re.fullmatch(r'[0-9a-fA-F]+:[0-9a-fA-F]+', fields[3]) and fields[4].isdigit(), 'proc_map_schema')
            major, minor = (int(part, 16) for part in fields[3].split(':'))
            key = (tag, f'{major:x}:{minor:x}', int(fields[4]))
            records[key] = records.get(key, 0)+1
    return [{'target': tag, 'device': dev, 'inode': ino, 'segments': count,
             'old': dev != targets[tag]['device'] or ino != targets[tag]['inode']}
            for (tag, dev, ino), count in sorted(records.items())]


def scan_consumers(targets, allowed):
    """Only public module/library paths leave raw /proc buffers as identities."""
    pids = sorted(int(p.name) for p in PROC.iterdir() if p.name.isdigit())
    require(len(pids) <= 4096, 'proc_inventory_bound')
    deadline = time.monotonic()+20; total = 0; result = {}
    for pid in pids:
        require(time.monotonic() < deadline, 'proc_scan_deadline')
        try:
            before = start_ticks(pid); raw = proc_read(pid, 'maps', 2*1024**2)
            total += len(raw); require(total <= MAX_SCAN_BYTES, 'proc_scan_total_bound')
            records = map_records(raw, targets)
            native = []
            for record in records:
                # A container may use the same filename for its own image.
                # Compare only metadata of this known public file in that root,
                # never Env, sessions, command lines or unrelated files.
                path = PROC/str(pid)/'root'/Path(targets[record['target']]['path']).relative_to('/')
                info = path.stat()
                current = targets[record['target']]
                if (f'{os.major(info.st_dev):x}:{os.minor(info.st_dev):x}', info.st_ino) == (current['device'], current['inode']):
                    native.append(record)
            after = start_ticks(pid)
            require(before == after, 'proc_pid_reused_during_scan')
        except FileNotFoundError:
            require(pid not in allowed, 'native_consumer_disappeared')
            continue
        except PermissionError:
            raise RuntimeError('native_consumer_scan_incomplete') from None
        if any(record['old'] for record in native):
            require(pid in allowed, 'unknown_old_native_brotli_consumer')
        if pid in allowed:
            result[pid] = {'pid': pid, 'start_ticks': before, 'mappings': native}
    require(set(result) == set(allowed), 'native_consumer_scan_incomplete')
    return result


def module_is_patched(process):
    modules = [r for r in process['mappings'] if r['target'] == 'python_module']
    return bool(modules) and not any(r['old'] for r in process['mappings'])


def golden_db_ready():
    path = GOLDEN_DB
    require(path.resolve(strict=True) == path and path.is_file(), 'golden_database_missing')
    before = path.stat(); require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'golden_database_metadata')
    # URI mode=ro refuses a missing DB; no application module/table is imported
    # and no business row is selected. A normal SQLite lock-consistent check.
    deadline = time.monotonic()+5
    connection = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows = connection.execute('PRAGMA quick_check').fetchmany(2)
        require(rows == [('ok',)], 'golden_database_quick_check_failed')
    finally:
        connection.close()
    require((before.st_dev, before.st_ino) == (path.stat().st_dev, path.stat().st_ino), 'golden_database_replaced')
    return True


def heartbeat_ready(N, requested):
    payload = json.loads(N.read(HEALTH, 65536))
    source = payload.get('sources', {}).get('PUBLIC_MARKET_TELEGRAM', {})
    details = source.get('details', {})
    def stamp(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    try:
        times = [stamp(source[key]) for key in ('heartbeat_at_utc', 'last_success_at_utc')]
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
    now = datetime.now(timezone.utc)
    return (payload.get('schema_version') == 1 and source.get('status') == 'HEALTHY'
            and source.get('error_code') is None and details.get('connected') is True
            and type(details.get('channel_count')) is int and details['channel_count'] > 0
            and all(requested <= value <= now and (now-value).total_seconds() <= 45 for value in times))


def journal_ready(N, unit, pid, since_us):
    boot_id = N.boot_id()
    require(isinstance(boot_id, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id),
            'invalid_boot_identity')
    journal_boot = boot_id.replace('-', '')
    pattern = '"event": "telegram_live_ready"' if unit == UNITS[0] else 'telegram\\.ext\\.Application.*Application started'
    try:
        raw = N.command(['/usr/bin/journalctl', '--quiet', '--no-pager', '--output=json', '--lines=80',
                         '--boot='+journal_boot, '--unit='+unit, '_PID='+str(pid), '--grep='+pattern], seconds=15)
    except RuntimeError as error:
        if str(error) == 'native_command_failed_journalctl_1':
            return False
        raise
    for line in raw.splitlines():
        row = json.loads(line)
        if (row.get('_BOOT_ID') != journal_boot or row.get('_PID') != str(pid) or row.get('_SYSTEMD_UNIT') != unit
            or not str(row.get('__MONOTONIC_TIMESTAMP', '')).isdigit()
            or int(row['__MONOTONIC_TIMESTAMP']) < since_us):
            continue
        message = row.get('MESSAGE')
        if not isinstance(message, str):
            continue
        if unit == UNITS[0]:
            try: value = json.loads(message)
            except ValueError: continue
            if value.get('event') == 'telegram_live_ready' and isinstance(value.get('channels'), list) and value['channels']:
                return True
        elif re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} - telegram\.ext\.Application - INFO - Application started', message):
            return True
    return False


def observe_replacement(N, unit, before, targets, window, requested, since_us):
    stable = None
    deadline = time.monotonic()+duration(before['TimeoutStopUSec'])+duration(before['TimeoutStartUSec'])+STABLE_SECONDS
    while time.monotonic() < deadline:
        require(window.remaining() > 0, 'refresh_window_expired')
        value = units(N)[unit]
        pid = int(value['MainPID'])
        if value['ActiveState'] == 'active' and value['SubState'] == 'running' and pid > 1 and pid != int(before['MainPID']):
            process = {'pid': pid, 'start_ticks': start_ticks(pid), 'mappings': map_records(proc_read(pid, 'maps', 2*1024**2), targets)}
            require(policy(value) == policy(before), 'native_unit_policy_changed')
            if module_is_patched(process) and journal_ready(N, unit, pid, since_us):
                app_ready = heartbeat_ready(N, requested) if unit == UNITS[0] else golden_db_ready()
                if app_ready:
                    key = (pid, process['start_ticks'], value['NRestarts'])
                    if stable and stable[0] == key and time.monotonic()-stable[1] >= STABLE_SECONDS:
                        return value, process
                    if not stable or stable[0] != key:
                        stable = key, time.monotonic()
                else:
                    stable = None
            else:
                stable = None
        else:
            stable = None
        time.sleep(min(2, window.remaining()))
    raise RuntimeError('native_consumer_readiness_not_proven')


def before_state(N):
    N.native()
    require(N.versions() == {name:N.NEW for name in N.PACKAGES}, 'patched_packages_required')
    N.installed_api(N.NEW)
    require(not N.command(['/usr/bin/dpkg','--verify',*N.PACKAGES]).strip(), 'native_installed_package_drift')
    installed_path = N.ROOT/'receipt.json'; installed = N.read(installed_path)
    receipt = json.loads(installed)
    require(receipt['schema'] == 'native_brotli_install/1' and receipt['phase'] == 'on_disk_patched_consumers_pending'
            and receipt['security_transition_completed'] is False and receipt['boot_id'] == N.boot_id(), 'authentic_package_receipt_required')
    runtime = N.runtime(); require(runtime == receipt['runtime'], 'native_container_baseline_changed')
    state = units(N)
    require(all(row['ActiveState'] == 'active' and row['SubState'] == 'running' and int(row['MainPID']) > 1 for row in state.values()), 'native_consumer_not_running')
    targets = current_targets(N)
    processes = scan_consumers(targets, {int(row['MainPID']) for row in state.values()})
    for row in processes.values():
        require(any(m['target'] == 'python_module' for m in row['mappings']), 'native_module_mapping_missing')
    golden_db_ready()
    return installed, N.identity(installed_path.lstat()), runtime, state, targets, processes


def action(N, mode, window=None):
    installed, installed_identity, runtime, state, targets, processes = before_state(N)
    required = [unit for unit in UNITS if not module_is_patched(processes[int(state[unit]['MainPID'])])]
    if mode == 'check':
        return {'status':'native_brotli_refresh_checked', 'required_units':required, 'runtime_changed':False,
                'security_transition_completed':False, 'business_transactions_tested':False}
    require(window is not None and required, 'fresh_refresh_window_required')
    budget = sum(duration(state[u]['TimeoutStartUSec'])+duration(state[u]['TimeoutStopUSec'])+STABLE_SECONDS for u in required)+70
    require(window.remaining() >= budget, 'refresh_window_too_short')
    require(not os.path.lexists(ROOT), 'refresh_already_attempted')
    N.DEADLINE = window.monotonic_end
    with ExitStack() as stack:
        old_marker = N.read(N.MARKER, 8192); prior = N.MARKER.lstat()
        fd = os.open(N.MARKER, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
        require(N.identity(prior) == N.identity(os.fstat(fd)) == N.identity(N.MARKER.lstat()), 'refresh_marker_changed')
        N.acquire_marker(fd)
        require(N.read(N.MARKER, 8192) == old_marker and N.identity(prior) == N.identity(os.fstat(fd)) == N.identity(N.MARKER.lstat()), 'refresh_marker_changed')
        stack.enter_context(N.legacy_source_guard())
        N.native(source_held=True)
        require(N.versions() == {name:N.NEW for name in N.PACKAGES} and N.runtime() == runtime and units(N) == state
                and current_targets(N) == targets, 'refresh_baseline_changed_while_waiting')
        require(scan_consumers(targets, set(processes)) == processes, 'refresh_consumer_baseline_changed')
        require(window.remaining() >= budget, 'refresh_window_too_short')
        N.safe_parent(ROOT); ROOT.mkdir(mode=0o700)
        journal = {'schema':'native_brotli_consumer_refresh/1', 'phase':'admitted',
                   'window_start_utc':window.start.isoformat(), 'window_end_utc':window.end.isoformat(),
                   'boot_id':N.boot_id(), 'installed_receipt_unchanged':True, 'before_units':state,
                   'before_processes':processes, 'completed_units':{}, 'security_transition_completed':False,
                   'completion_unverified':True, 'business_transactions_tested':False,
                   'cleanup_review_due_utc':(datetime.now(timezone.utc)+timedelta(days=10)).isoformat()}
        path = ROOT/'receipt.json'
        N.write(path, (json.dumps(journal,sort_keys=True)+'\n').encode(), exclusive=True)
        try:
            for unit in required:
                require(window.remaining() >= duration(state[unit]['TimeoutStartUSec'])+duration(state[unit]['TimeoutStopUSec'])+STABLE_SECONDS, 'refresh_window_too_short')
                fresh = units(N)[unit]
                require(fresh == state[unit] and start_ticks(int(fresh['MainPID'])) == processes[int(fresh['MainPID'])]['start_ticks'],
                        'native_unit_changed_before_restart')
                require(N.runtime() == runtime and current_targets(N) == targets, 'native_runtime_changed')
                requested = datetime.now(timezone.utc); since_us = int(time.monotonic()*1000000)
                journal.update(phase='restart_requested', current_unit=unit, requested_at_utc=requested.isoformat(),
                               requested_monotonic_us=since_us)
                N.write(path, (json.dumps(journal,sort_keys=True)+'\n').encode(), exclusive=False)
                # Exactly one request, no polling command can enqueue a retry.
                N.command(['/usr/bin/systemctl', '--no-block', 'restart', unit], seconds=10)
                value, process = observe_replacement(N, unit, state[unit], targets, window, requested, since_us)
                old = processes[int(state[unit]['MainPID'])]
                try: require(start_ticks(old['pid']) != old['start_ticks'], 'old_native_consumer_still_alive')
                except FileNotFoundError: pass
                journal['completed_units'][unit] = {'unit':value, 'process':process, 'fresh_application_readiness':True,
                                                    'old_process_ended':True, 'stable_observation_seconds':STABLE_SECONDS}
                journal.update(phase='unit_verified')
                N.write(path, (json.dumps(journal,sort_keys=True)+'\n').encode(), exclusive=False)
            final = units(N)
            final_processes = scan_consumers(targets, {int(row['MainPID']) for row in final.values()})
            require(all(module_is_patched(row) for row in final_processes.values()), 'old_native_brotli_mapping_remains')
            boot_epoch = time.time()-time.monotonic()
            ticks_per_second = os.sysconf('SC_CLK_TCK')
            for unit in UNITS:
                expected = journal['completed_units'].get(unit, {}).get('unit', state[unit])
                require(final[unit] == expected, 'native_consumer_changed_after_readiness')
                process = final_processes[int(final[unit]['MainPID'])]
                if unit in journal['completed_units']:
                    observed = journal['completed_units'][unit]['process']
                    require((process['pid'],process['start_ticks']) == (observed['pid'],observed['start_ticks']),
                            'native_consumer_changed_after_readiness')
                since_us = int(process['start_ticks']/ticks_per_second*1000000)
                started = datetime.fromtimestamp(boot_epoch+process['start_ticks']/ticks_per_second, timezone.utc)
                require(journal_ready(N,unit,process['pid'],since_us)
                        and (heartbeat_ready(N,started) if unit == UNITS[0] else golden_db_ready()), 'final_application_readiness_missing')
            require(N.runtime() == runtime and all(policy(final[u]) == policy(state[u]) for u in UNITS), 'native_runtime_changed')
            require(N.versions() == {name:N.NEW for name in N.PACKAGES} and current_targets(N) == targets
                    and not N.command(['/usr/bin/dpkg','--verify',*N.PACKAGES]).strip(), 'native_installed_package_drift')
            require(N.read(N.ROOT/'receipt.json') == installed and N.identity((N.ROOT/'receipt.json').lstat()) == installed_identity, 'installed_receipt_changed')
            require(N.read(N.MARKER,8192) == old_marker and N.identity(prior) == N.identity(os.fstat(fd)) == N.identity(N.MARKER.lstat()), 'refresh_marker_changed')
            journal.update(phase='native_brotli_consumers_verified', native_brotli_runtime_verified=True,
                           completion_unverified=False, security_transition_completed=False,
                           final_processes=final_processes, fifteen_containers_unchanged=True,
                           complete_pre_post_proc_scans=True, unknown_old_native_consumers=False)
        except Exception as error:
            journal.update(phase='partial_refresh_incident_open', completion_unverified=True,
                           reason_code=type(error).__name__, native_brotli_runtime_verified=False)
            N.write(path, (json.dumps(journal,sort_keys=True)+'\n').encode(), exclusive=False)
            raise RuntimeError('native_brotli_refresh_incomplete') from None
        N.write(path, (json.dumps(journal,sort_keys=True)+'\n').encode(), exclusive=False)
        return {'status':journal['phase'], 'native_brotli_runtime_verified':True, 'security_transition_completed':False,
                'installed_receipt_unchanged':True, 'fifteen_containers_unchanged':True, 'business_transactions_tested':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('install_helper', type=Path)
    parser.add_argument('action', choices=('check','refresh'), nargs='?', default='check')
    parser.add_argument('--window-start', default=''); parser.add_argument('--window-end', default='')
    args = parser.parse_args()
    N = load_installer(args.install_helper)
    window = Window(args.window_start,args.window_end) if args.action == 'refresh' else None
    N.DEADLINE = window.monotonic_end if window is not None else time.monotonic()+180
    def interrupted(*_):
        raise RuntimeError('native_refresh_interrupted')
    for number in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    signal.alarm(max(1,int(window.remaining())) if window is not None else 180)
    print(json.dumps(action(N,args.action,window),sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status':'native_brotli_refresh_refused', 'reason_code':type(error).__name__,
                          'completion_unverified':True, 'security_transition_completed':False}))
        raise SystemExit(1)
