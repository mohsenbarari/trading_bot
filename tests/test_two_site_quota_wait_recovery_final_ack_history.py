"""Real-file multi-history proof. Provider messages are synthetic, not live history.

One candidate receipt uses the actual native SDK. No accepted ACK is cloned or
re-signed. The fixture bootstraps once and each accepted child becomes the next
task's genuine source head.
"""
from __future__ import annotations
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch
import pytest

from scripts import two_site_codex_autopilot as ap
from scripts import two_site_quota_retention as retention
from scripts.two_site_quota_wait_recovery import WaitRecoveryError
from scripts.two_site_pre_review_receipt import run_candidate_tests
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_finalization_real_fixture import (
    _bootstrap_quota_history_lab,_append_quota_history,_history_custody,
    _w0600,_cj,_git,simulated_candidate_runner,_frame,_recover,_restore)
from tests.test_two_site_assignment_scope_core import _write_assignment,_write_worktrees
from scripts.two_site_assignment_scope import load_active_assignment_scope

PATHS=tuple(f'tests/test_quota_history_{letter}.py' for letter in 'abcd')
CIDS=tuple(f'cycle-0001-20261020T120000Z-{number}' for number in range(701,709))


def _unchanged_call(root,call):
    before=_frame(root)
    try:
        return call()
    finally:
        assert _frame(root)==before


def _native_runner(*args,**kwargs):
    from tests.test_two_site_agent_sandbox_overlay import install_native_namespace_adapter
    with pytest.MonkeyPatch.context() as native:
        install_native_namespace_adapter(native)
        receipt=run_candidate_tests(*args,**kwargs)
    assert receipt['state']=='PASSED' and receipt['returncode']==0
    assert receipt['isolation'] not in {'SIMULATED_EXTERNAL_TEST_OUTCOME_NOT_SANDBOX_PROOF',''}
    return receipt


def _replace_owned(path,raw):
    assert path.is_file() and not path.is_symlink() and path.stat().st_nlink==1
    old=(path.read_bytes(),path.stat().st_mode,path.stat().st_atime_ns,path.stat().st_mtime_ns)
    path.write_bytes(raw)
    os.chmod(path,stat.S_IMODE(old[1]))
    return old


def test_real_two_histories_cardinality_faults_and_same_witness(broker_repo,monkeypatch):
    lab=_bootstrap_quota_history_lab(broker_repo,test_paths=PATHS)
    root=lab['root']
    one=_append_quota_history(lab,label='a',original_cid=CIDS[0],continuation_cid=CIDS[1],
          test_path=PATHS[0],candidate_runner=_native_runner)
    assert one['receipt_metadata']['isolation']!='SIMULATED_EXTERNAL_TEST_OUTCOME_NOT_SANDBOX_PROOF'
    one_frame=(_history_custody(root),)
    two=_append_quota_history(lab,label='b',original_cid=CIDS[2],continuation_cid=CIDS[3],
          test_path=PATHS[1],candidate_runner=simulated_candidate_runner)
    assert one['child_head']==two['head_before']
    assert all(_history_custody(root).get(path)==value for path,value in one_frame[0].items())
    assert _unchanged_call(root,lambda:_recover(root)) is None
    for history in (one,two):
        actual=_unchanged_call(root,lambda h=history:retention.read_final_ack_history_locked(
            root,h['original_controller_run_id']))
        assert actual['state']=='ACKNOWLEDGED' and actual['ack']==history['ack']
    active=_append_quota_history(lab,label='c',original_cid=CIDS[4],continuation_cid=CIDS[5],
          test_path=PATHS[2],candidate_runner=simulated_candidate_runner,finalize=False)
    expected=(CIDS[4],active['wait'])
    def healthy():
        actual=_unchanged_call(root,lambda:_recover(root))
        assert type(actual) is tuple and actual==expected
        return actual
    healthy()

    # Current Scope differs.
    _write_assignment(root,assignment_id='WA-HISTORY-LATER',integration_branch=ap.BRANCH,
                      allowed_paths=list(PATHS),forbidden_paths=['scripts/**'])
    _write_worktrees(root,path=str(root),branch=ap.BRANCH,active_assignment_id='WA-HISTORY-LATER')
    assert load_active_assignment_scope(root).sha256!=one['actual_scope'].sha256
    for h in (one,two):
        actual=_unchanged_call(root,lambda h=h:retention.read_final_ack_history_locked(root,h['original_controller_run_id']))
        assert actual['ack']==h['ack'] and _git(root,'rev-parse','HEAD')!=h['head_before']
    # Restore registry only.
    _write_worktrees(root,path=str(root),branch=ap.BRANCH,active_assignment_id='WA-BROKER-TEST')
    healthy()

    original_dir=root/'.local/two-site-refactor/autopilot/runs'/CIDS[0]
    continuation_dir=root/'.local/two-site-refactor/autopilot/runs'/CIDS[1]
    paths=[original_dir/name for name in (
        'quota-wait.json','broker-reservation.json','preparation-startup.json',
        'quota-resume-claim.json','quota-resume-lineage-snapshot.json','quota-resume-submission.json')]
    paths += [continuation_dir/name for name in ('result.json','accepted-lineage.json','final-ack.json')]
    paths += [one['task_path'],root/'.local/two-site-refactor/assignments/WA-BROKER-TEST.md',
              root/'.local/two-site-refactor/proposals'/one['raw_result']['gemini_worker_run_id']/'status.json']
    # Same reader; every fault.
    for path in paths:
        original=path.read_bytes()
        for kind in ('missing','corrupt','symlink','hardlink','mode'):
            backup=path.with_name(path.name+'.owned-backup')
            old_mode=stat.S_IMODE(path.stat().st_mode)
            if kind=='mode':
                os.chmod(path,0o666 if path.suffix=='.md' else 0o644)
            elif kind=='corrupt':
                path.write_bytes(b'{"unsupported":true}')
                os.chmod(path,old_mode)
            else:
                path.rename(backup)
                if kind=='symlink': path.symlink_to(backup)
                if kind=='hardlink': os.link(backup,path)
            try:
                with pytest.raises((WaitRecoveryError,retention.QuotaRetentionError,ValueError,OSError)):
                    _unchanged_call(root,lambda:_recover(root))
            except BaseException as exc:
                exc.add_note(f'{path.relative_to(root)}:{kind}')
                raise
            finally:
                if kind=='mode':
                    os.chmod(path,old_mode)
                elif kind=='corrupt':
                    path.write_bytes(original);os.chmod(path,old_mode)
                else:
                    if os.path.lexists(path): path.unlink()
                    backup.rename(path)
            healthy()

    ack_path=continuation_dir/'final-ack.json'
    bad=json.loads(ack_path.read_text());bad['accepted_lineage_sha256']='0'*64
    old=_replace_owned(ack_path,_cj(bad).encode())
    def same_fault_witness():
        with pytest.raises(WaitRecoveryError):
            _unchanged_call(root,lambda:_recover(root))
    try:
        same_fault_witness()
        from scripts import two_site_quota_final_ack_storage as ack_sdk
        real=ack_sdk.read_final_ack_record_candidate
        reached=[]
        def bypass(*args,**kwargs):
            if args[1]!=CIDS[0]: return real(*args,**kwargs)
            reached.append(args[1])
            return copy.deepcopy(bad)
        with patch.object(ack_sdk,'read_final_ack_record_candidate',bypass):
            with pytest.raises(pytest.fail.Exception,match='DID NOT RAISE'):
                same_fault_witness()
        assert reached==[CIDS[0]]
        same_fault_witness()
    finally:
        _restore(ack_path,old)
    healthy()
    assert ack_sdk.read_final_ack_record_candidate is real

    # Targeted same-byte inode drift.
    from scripts import two_site_quota_final_acceptance_lineage as collector
    actual_collector=collector.collect_final_acceptance_upstream_lineage_from_snapshot_locked
    snapshot=original_dir/'quota-resume-lineage-snapshot.json'
    reached=[]
    def drift(root_arg,cid):
        if cid!=CIDS[0]: return actual_collector(root_arg,cid)
        reached.append(cid)
        raw=snapshot.read_bytes()
        replacement=snapshot.with_name('.owned-replacement')
        _w0600(replacement,raw)
        os.replace(replacement,snapshot)
        return actual_collector(root_arg,cid)
    with patch.object(collector,'collect_final_acceptance_upstream_lineage_from_snapshot_locked',drift):
        with pytest.raises(WaitRecoveryError):
            _recover(root)
    assert reached==[CIDS[0]]
    healthy()

    # Existing capacity and STOP gates remain real, including broken symlink STOP.
    stop=root/'.local/two-site-refactor/autopilot/STOP'
    stop.symlink_to(stop.with_name('absent-owned-stop-target'))
    try:
        with pytest.raises(WaitRecoveryError,match='STOP'):
            _unchanged_call(root,lambda:_recover(root))
    finally:
        stop.unlink()
    healthy()
    runs=root/'.local/two-site-refactor/autopilot/runs'
    created=[]
    try:
        for n in range(509):
            directory=runs/f'cycle-9999-20261020T120000Z-{9000+n}'
            directory.mkdir(mode=0o700);created.append(directory)
        with pytest.raises(WaitRecoveryError,match='512'):
            _unchanged_call(root,lambda:_recover(root))
    finally:
        for directory in created: directory.rmdir()
    healthy()

    # Two distinct real active waits fail before either is eligible for admission.
    _append_quota_history(lab,label='d',original_cid=CIDS[6],continuation_cid=CIDS[7],
         test_path=PATHS[3],candidate_runner=simulated_candidate_runner,finalize=False,reuse_pending=True)
    with pytest.raises(WaitRecoveryError,match='multiple wait'):
        _unchanged_call(root,lambda:_recover(root))


def test_real_complete_without_commit_has_no_invented_actor_authority(broker_repo):
    lab=_bootstrap_quota_history_lab(broker_repo,test_paths=('tests/test_quota_nochange.py',))
    fx=_append_quota_history(lab,label='nochange',original_cid=CIDS[0],continuation_cid=CIDS[1],
         test_path=lab['test_paths'][0],candidate_runner=simulated_candidate_runner,commit_change=False)
    root=lab['root']
    assert fx['enriched_transition']['state']=='COMPLETE'
    assert fx['enriched_transition']['head_changed'] is False
    assert fx['enriched_transition']['head_before']==fx['enriched_transition']['head_after']
    assert all(fx['raw_result'][key] is None for key in
        ('commit','gemini_worker_run_id','gemini_reviewer_run_id','proposal_sha256'))
    actual=_unchanged_call(root,lambda:retention.read_final_ack_history_locked(root,CIDS[0]))
    assert actual['ack']==fx['ack'] and actual['state']=='ACKNOWLEDGED'
    path=root/'.local/two-site-refactor/autopilot/runs'/CIDS[1]/'result.json'
    value=json.loads(path.read_bytes());value['gemini_worker_run_id']='invented-worker'
    old=_replace_owned(path,_cj(value).encode())
    try:
        with pytest.raises((ValueError,ap.AutopilotError)):
            retention.read_final_ack_history_locked(root,CIDS[0])
    finally:
        _restore(path,old)
    assert retention.read_final_ack_history_locked(root,CIDS[0])['ack']==fx['ack']


def test_exact_runner_source_shape_and_reached_same_history_witness(broker_repo):
    from tests.test_two_site_quota_finalization_real_fixture import _retirement_history
    fx=_retirement_history(broker_repo);root=fx['root'];original=fx['original_controller_run_id']
    actor=root/'.local/two-site-refactor/proposals'/fx['raw_result']['gemini_worker_run_id']/'task.json'
    raw=actor.read_bytes();snapshot=json.loads(raw);sources=snapshot['sources']
    path=next(name for name,row in sources.items() if row['content'] is None)
    assert sources[path]=={'sha256':None,'content':None}
    assert sources['source.py']=={'sha256':__import__('hashlib').sha256(b'VALUE = 1\n').hexdigest(),'content':'VALUE = 1\n'}
    assert retention.read_final_ack_history_locked(root,original)['ack']==fx['ack']
    bad_rows=[None,'legacy-string',{'sha256':None},{'sha256':None,'content':None,'extra':True},
              {'sha256':'0'*64,'content':None},{'sha256':None,'content':''}]
    for key,rows in [(path,bad_rows),('source.py',[
        {'sha256':None,'content':None},{'sha256':True,'content':'VALUE = 1\n'},
        {'sha256':'0'*64,'content':'VALUE = 1\n'},
        {'sha256':sources['source.py']['sha256'],'content':'VALUE = 2\n'}])]:
        for row in rows:
            forged=copy.deepcopy(snapshot);forged['sources'][key]=row
            actor.write_bytes(_cj(forged).encode())
            try:
                with pytest.raises(retention.QuotaRetentionError,match='actor|historical'):
                    _unchanged_call(root,lambda:retention.read_final_ack_history_locked(root,original))
            finally:actor.write_bytes(raw)
    real_sources=retention._validate_actor_sources;real_absence=retention._validate_historical_absence
    for both_lists in (False,True):
        def context_absence(root_arg,before,task,rows):
            changed=copy.deepcopy(task)
            if not both_lists:changed['allowed_files'].remove(path)
            changed.setdefault('context_files',[]).append(path)
            return real_sources(root_arg,before,changed,rows)
        def absence_witness():
            with pytest.raises(retention.QuotaRetentionError,match='context source is historically absent'):
                _unchanged_call(root,lambda:retention.read_final_ack_history_locked(root,original))
        reached=[]
        with patch.object(retention,'_validate_actor_sources',context_absence):
            absence_witness()
            def bypass_absence(*args):reached.append(args[1])
            with patch.object(retention,'_validate_historical_absence',bypass_absence):
                with pytest.raises(pytest.fail.Exception,match='DID NOT RAISE'):absence_witness()
            assert reached and set(reached)=={path}
            absence_witness()
        assert retention._validate_historical_absence is real_absence
        assert retention.read_final_ack_history_locked(root,original)['ack']==fx['ack']
    actual_pin=retention._pin_actor;actual_gate=retention._validate_actor_sources;reached=[]
    def bad_metadata(*args,**kwargs):
        value=actual_pin(*args,**kwargs)
        if args[2]==fx['raw_result']['gemini_worker_run_id']:
            value=copy.deepcopy(value);value['sources']['source.py']['sha256']='0'*64
        return value
    def witness():
        before=_frame(root)
        with pytest.raises(retention.QuotaRetentionError,match='actor Sources'):
            retention.read_final_ack_history_locked(root,original)
        assert _frame(root)==before
    with patch.object(retention,'_pin_actor',bad_metadata):
        witness()
        def bypass(*args,**kwargs):reached.append(True)
        with patch.object(retention,'_validate_actor_sources',bypass):
            with pytest.raises(pytest.fail.Exception,match='DID NOT RAISE'):witness()
        assert reached
        witness()
    assert retention._validate_actor_sources is actual_gate
    assert retention.read_final_ack_history_locked(root,original)['ack']==fx['ack']


def test_directory_bound_stops_at_first_excess_and_same_full_history(broker_repo):
    from types import SimpleNamespace
    for limit in (6,32,64,128,512):
        seen=[];closed=[]
        class Entries:
            def __enter__(self):return self
            def __exit__(self,*args):closed.append(True)
            def __iter__(self):return self
            def __next__(self):
                seen.append(len(seen))
                assert len(seen)<=limit+1
                return SimpleNamespace(name='entry')
        with patch.object(retention.os,'scandir',lambda p:Entries()):
            with pytest.raises(retention.QuotaRetentionError,match='exceeds'):
                retention._bounded_names(Path('/bounded-probe'),limit,'probe')
        assert len(seen)==limit+1 and closed==[True]
    from tests.test_two_site_quota_finalization_real_fixture import _retirement_history
    fx=_retirement_history(broker_repo);root=fx['root'];cid=fx['original_controller_run_id']
    directory=root/'.local/two-site-refactor/autopilot/runs'/cid
    healthy_names=[p.name for p in directory.iterdir()]
    added=[]
    try:
        for index in range(129-len(healthy_names)):
            path=directory/('owned-excess-'+str(index));_w0600(path,b'');added.append(path)
        def witness():
            before=_frame(root)
            with pytest.raises(retention.QuotaRetentionError,match='128'):
                retention.read_final_ack_history_locked(root,cid)
            assert _frame(root)==before
        witness();actual=retention._bounded_names;reached=[]
        def bypass(path,limit,label):
            if path==directory and limit==128:reached.append(True);return healthy_names
            return actual(path,limit,label)
        with patch.object(retention,'_bounded_names',bypass):
            with pytest.raises(pytest.fail.Exception,match='DID NOT RAISE'):witness()
        assert reached
        witness()
    finally:
        for path in added:path.unlink()
    assert retention.read_final_ack_history_locked(root,cid)['ack']==fx['ack']


@pytest.mark.parametrize('retired',[False,True])
def test_current_frame_precedes_historical_sdk_and_survives_error_exit(broker_repo,monkeypatch,retired):
    from tests.test_two_site_quota_finalization_real_fixture import (
        _retirement_history,_retirement_seed_authority,_retirement_idle_broker)
    fx=_retirement_history(broker_repo);root=fx['root'];cid=fx['original_controller_run_id']
    if retired:
        authority=_retirement_seed_authority(fx)
        monkeypatch.setattr(retention,'_now_utc',lambda:authority['stamp'])
        with _retirement_idle_broker(root),patch.object(retention,'_unlink_certified_receipt',
                                                       side_effect=OSError('before first unlink')):
            with pytest.raises(OSError):retention.prune_quota_receipts(root,cid,authority['intent']['certified_at_utc'])
    actual_current=retention._pin_current_repository
    actual_old=retention._historical_repository;events=[]
    def current(*args,**kwargs):
        value=actual_current(*args,**kwargs);events.append('current');return value
    def old(*args,**kwargs):
        assert events and events[0]=='current';events.append('historical');return actual_old(*args,**kwargs)
    with patch.object(retention,'_pin_current_repository',current),patch.object(retention,'_historical_repository',old):
        assert retention.read_final_ack_history_locked(root,cid)['ack']==fx['ack']
    assert events[0]=='current' and 'historical' in events and events[-1]=='current'
    control=root/ap.CONTROL_PATHS[0];saved=control.read_bytes();reached=[]
    def fail_and_drift(*args,**kwargs):
        reached.append(True);control.write_bytes(saved+b'\n')
        raise RuntimeError('declared historical failure')
    try:
        with patch.object(retention,'_historical_repository',fail_and_drift):
            with pytest.raises(retention.QuotaRetentionError,match='changed|drift') as caught:
                retention.read_final_ack_history_locked(root,cid)
        chain=[];error=caught.value
        while error is not None:chain.append(error);error=error.__cause__
        assert reached and any(type(e)is RuntimeError for e in chain)
    finally:control.write_bytes(saved)
    assert retention.read_final_ack_history_locked(root,cid)['ack']==fx['ack']


def test_old_ack_does_not_hide_present_current_epoch_corruption(broker_repo):
    from tests.test_two_site_quota_finalization_real_fixture import _retirement_history
    fx=_retirement_history(broker_repo);root=fx['root']
    assert _recover(root) is None
    facts=ap.validate_repository(root);state=root/'.local/two-site-refactor/autopilot'
    epoch=state/ap.rescope_epoch_basename(ap.validate_accepted_repository(facts))
    epoch.mkdir(mode=0o700)
    assert _recover(root) is None
    for filename,payload in [('rescope_intent_1',b'bad'),('rescope_intent_2',b'rescope_intent_2\n'),
                             ('rescope_receipt.json',b'{}')]:
        target=epoch/filename;_w0600(target,payload)
        try:
            with pytest.raises(WaitRecoveryError,match='present pending'):
                _unchanged_call(root,lambda:_recover(root))
        finally:target.unlink()
        assert _recover(root) is None


def test_root_maintenance_actual_head_cannot_reuse_old_preaction_base(broker_repo,monkeypatch):
    from tests.test_two_site_quota_finalization_real_fixture import _retirement_history,_retirement_seed_authority
    fx=_retirement_history(broker_repo);root=fx['root'];authority=_retirement_seed_authority(fx)
    monkeypatch.setattr(retention,'_now_utc',lambda:authority['stamp'])
    stamp=authority['intent']['certified_at_utc'];cid=fx['original_controller_run_id']
    assert retention.classify_quota_retention_locked(root,cid,stamp)['state']=='ELIGIBLE'
    (root/'later-maintenance.txt').write_text('owned later maintenance frame\n')
    _git(root,'add','later-maintenance.txt');_git(root,'commit','-qm','fixture: different actual maintenance head')
    result=retention.classify_quota_retention_locked(root,cid,stamp)
    assert result['state']=='KEEP' and 'actual clean head' in result['reason']
    assert all((root/row['relative_path']).exists() for row in authority['intent']['collectible'])


def test_matched_unretired_claim_and_recovery_controls_keep_real_ambiguous_receipt(broker_repo):
    from scripts import two_site_provider_broker as broker
    from tests.test_two_site_quota_finalization_real_fixture import _retirement_history,_quota_request_alias
    fx=_retirement_history(broker_repo,commit_change=False);root=fx['root']
    state,status,requests,claimed,responses=broker.broker_paths(root)
    request=_quota_request_alias(fx['request'])
    name=request['request_id']+'.json';path=requests/name
    _w0600(path,_cj(request));before=path.lstat()
    moved=broker._claim_next(requests,claimed,responses,state/'terminal-quota')
    assert moved==claimed/name and not path.exists()
    after=moved.lstat()
    assert (before.st_dev,before.st_ino,before.st_size)==(after.st_dev,after.st_ino,after.st_size)
    assert stat.S_IMODE(after.st_mode)==0o600 and moved.read_bytes()==_cj(request).encode()
    broker._recover_interrupted_claims(root,claimed,responses,requests,state/'terminal-quota')
    response_path=responses/name;raw=response_path.read_bytes()
    response=broker._safe_read_existing_response(root,response_path)
    assert response['state']=='BROKER_ERROR' and type(response['exit_code'])is int and response['exit_code']==2
    assert response['stderr']=='BrokerError: broker restarted after an ambiguous in-flight provider call; refusing duplicate execution'
    assert response['stdout']=='' and all(response[k]==request[k] for k in ('request_id','run_prefix','source_head'))
    assert not path.exists() and not moved.exists()
    full=_frame(root)
    broker._recover_interrupted_claims(root,claimed,responses,requests,state/'terminal-quota')
    assert _frame(root)==full and response_path.read_bytes()==raw


@pytest.mark.parametrize('kind',['history','held_lock'])
def test_new_leaf_opens_reject_reached_fifo_swap_without_blocking(tmp_path,monkeypatch,kind):
    import time
    from tests.test_two_site_quota_finalization_real_fixture import _quota_fifo_swap
    root=tmp_path/'owned';root.mkdir(mode=0o700);path=root/'record.json';_w0600(path,b'{}')
    def read():
        if kind=='history':
            with retention._Pins() as pins:return pins.read(root,path.name,4096)[0]
        with retention._held_sync_lock_pins((path,)) as check:check()
    assert read()==(b'{}' if kind=='history' else None)
    started=time.monotonic()
    with _quota_fifo_swap(monkeypatch,path) as (flags,fds):
        with pytest.raises(retention.QuotaRetentionError):read()
        assert len(flags)==1 and flags[0]&os.O_NONBLOCK and len(fds)==1
        for fd in fds:
            with pytest.raises(OSError):os.fstat(fd)
    assert time.monotonic()-started<2
    assert read()==(b'{}' if kind=='history' else None)
