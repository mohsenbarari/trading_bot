"""SDK/Git/locks; synthetic outcomes/clock. No live release/pruning."""
from __future__ import annotations
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import time
from unittest.mock import patch
import pytest
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_provider_broker as broker
from scripts import two_site_quota_retention as subject
from scripts.two_site_assignment_scope import load_active_assignment_scope
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_assignment_scope_core import _write_assignment,_write_worktrees
from tests.test_two_site_quota_finalization_real_fixture import _w0600
from tests.test_two_site_quota_finalization_real_fixture import _frame,_recover

O='cycle-0001-20261020T120000Z-901'
C='cycle-0001-20261020T120000Z-902'
B=Path('.local/two-site-refactor')

from tests.test_two_site_quota_finalization_real_fixture import (
    _retirement_digest as _digest,_retirement_canonical as _canonical,
    _retirement_fp as _fp,_retirement_seed_authority as _seed_root_authority,
    _retirement_idle_broker as _real_idle_broker,_retirement_history as _history,
    _retirement_case,_quota_effect_frame,
    _quota_publication_fault,_retirement_public_inputs,_quota_external_marker,
    _quota_receipt_unlink_trace,_quota_request_alias,_quota_lock_fault,_status_abi,
    _quota_cold_base_intent,_quota_base_guard)

def test_root_bound_full_intent_crash_reconciliation_and_runtime_denials(broker_repo,monkeypatch):
    fx=_history(broker_repo);root=fx['root']
    _status_abi(root,fx);authority=_seed_root_authority(fx)
    expected=authority['intent'];stamp=expected['certified_at_utc']
    monkeypatch.setattr(subject,'_now_utc',lambda:authority['stamp']) # disclosed offline clock
    immutable={row['relative_path']:(root/row['relative_path']).read_bytes()
               for row in expected['retained']}
    with _real_idle_broker(root) as downstream:
        with subject._maintenance_holds(root):
            actual=subject._build_retirement_intent_locked(root,O,stamp)
        assert _canonical(actual)==_canonical(expected) and len(_canonical(actual))<=16384
        assert actual['retained_anchor_sha256']==_digest(_canonical([
            row for row in actual['retained'] if row['role'] not in
            {'RELEASE','RETIREMENT_ASSIGNMENT','RETIREMENT_PREACTION'}]))
        real_unlink=subject.os.unlink;calls=[]
        def interrupted(name,*args,**kwargs):
            if name==Path(expected['collectible'][1]['relative_path']).name:
                calls.append(name);raise OSError('declared second unlink crash')
            return real_unlink(name,*args,**kwargs)
        with patch.object(subject.os,'unlink',interrupted):
            with pytest.raises(OSError,match='second unlink'):
                subject.prune_quota_receipts(root,O,stamp)
        assert calls and not (root/expected['collectible'][0]['relative_path']).exists()
        assert (root/expected['collectible'][1]['relative_path']).exists()
        pending=subject.read_final_ack_history_locked(root,O)
        assert pending['state']=='CERTIFIED_RETIRED_PENDING' and pending['ack']==fx['ack']
        assert _recover(root) is None
        _write_assignment(root,assignment_id='WA-LATER-COLD-SCOPE',integration_branch=ap.BRANCH,
                          allowed_paths=['tests/test_quota_retained.py'],forbidden_paths=['scripts/**'])
        _write_worktrees(root,path=str(root),branch=ap.BRANCH,active_assignment_id='WA-LATER-COLD-SCOPE')
        assert load_active_assignment_scope(root).sha256!=actual['retirement_authority']['assignment_sha256']
        assert subject.read_final_ack_history_locked(root,O)['state']=='CERTIFIED_RETIRED_PENDING'
        completed=subject.prune_quota_receipts(root,O,stamp)
        assert completed['state']=='CERTIFIED_RETIRED_COMPLETE'
        complete_path=root/B/'autopilot/runs'/O/'quota-history-retirement-complete.json'
        complete=json.loads(complete_path.read_bytes())
        assert complete=={'schema_version':'1.0','record_kind':'QUOTA_HISTORY_RETIREMENT_COMPLETE',
          'intent_sha256':_digest(_canonical(expected)),'completed_at_utc':stamp,
          'deleted_receipts':[{key:row[key] for key in ('role','relative_path','sha256')}
                              for row in expected['collectible']]}
        assert subject.prune_quota_receipts(root,O,stamp)=={'state':'CERTIFIED_RETIRED_COMPLETE','deleted':[]}
        assert all((root/path).read_bytes()==raw for path,raw in immutable.items())
        assert _recover(root) is None and downstream==[]

        for path in (authority['assignment'],authority['preaction'],authority['release'],
                     root/B/'autopilot/runs'/O/'quota-resume-lineage-snapshot.json',
                     fx['task_path'],root/B/'assignments/WA-BROKER-TEST.md'):
            saved=path.read_bytes()
            path.write_bytes(b'{"malformed":true}')
            try:
                with pytest.raises((subject.QuotaRetentionError,ValueError,OSError)):
                    subject.read_final_ack_history_locked(root,O)
                assert all(not (root/row['relative_path']).exists() for row in expected['collectible'])
            finally:
                path.write_bytes(saved)
            assert subject.read_final_ack_history_locked(root,O)['state']=='CERTIFIED_RETIRED_COMPLETE'
        subject.assert_not_retired(root,fingerprint='f'*64)
        target=root/expected['collectible'][0]['relative_path']
        _w0600(target,b'{}')
        try:
            with pytest.raises(subject.QuotaRetentionError):
                subject.read_final_ack_history_locked(root,O)
        finally: target.unlink()

def test_no_release_or_invalid_root_chain_never_deletes_and_same_witness(broker_repo,monkeypatch):
    fx=_history(broker_repo);root=fx['root']
    authority=_seed_root_authority(fx,transition_bad=True)
    stamp=authority['intent']['certified_at_utc']
    monkeypatch.setattr(subject,'_now_utc',lambda:authority['stamp'])
    paths=[root/row['relative_path'] for row in authority['intent']['collectible']]
    with _real_idle_broker(root):
        def same_witness():
            before=_frame(root)
            actual=subject.classify_quota_retention_locked(root,O,stamp)
            assert actual['state']=='KEEP' and 'STOP transition' in actual['reason']
            assert _frame(root)==before
            assert all(path.exists() for path in paths)
        same_witness()
        real=subject._validate_stop_transition;reached=[]
        def bypass(*args,**kwargs): reached.append(True)
        with patch.object(subject,'_validate_stop_transition',bypass):
            with pytest.raises(AssertionError):
                same_witness()
        assert reached
        same_witness()
        assert subject._validate_stop_transition is real


def test_postlink_failure_zero_unlinks_stop_and_preintent_authority_faults(broker_repo,monkeypatch):
    fx,authority,root,expected,stamp,receipts=_retirement_case(broker_repo,monkeypatch)
    receipts=[root/row['relative_path'] for row in expected['collectible']]
    original_bytes=[path.read_bytes() for path in receipts]
    assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='ELIGIBLE'
    for path in (authority['release'],authority['preaction'],authority['assignment'],
                 authority['observation']):
        backup=path.with_name(path.name+'.owned-backup')
        path.rename(backup)
        try:
            before=_frame(root)
            assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='KEEP'
            assert _frame(root)==before and [p.read_bytes() for p in receipts]==original_bytes
        finally: backup.rename(path)
        assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='ELIGIBLE'
    actual_read=subject._Pins.read;missing=[]
    def no_archive(self,base,relative,*args,**kwargs):
        if relative==authority['archive'].relative_to(root).as_posix():
            missing.append(relative);raise FileNotFoundError(relative)
        return actual_read(self,base,relative,*args,**kwargs)
    with patch.object(subject._Pins,'read',no_archive):
        assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='KEEP'
    assert missing and subject.classify_quota_retention_locked(root,O,stamp)['state']=='ELIGIBLE'
    queued=root/B/'provider-broker/requests'/(fx['request']['request_id']+'.json')
    _w0600(queued,_canonical(fx['request']))
    try:
        result=subject.classify_quota_retention_locked(root,O,stamp)
        assert result['state']=='KEEP' and 'outside references' in result['reason']
        assert [p.read_bytes() for p in receipts]==original_bytes
    finally: queued.unlink()
    unknown=root/B/'autopilot/unrecognized-retention-leaf.json'
    _w0600(unknown,b'{}')
    try: assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='KEEP'
    finally: unknown.unlink()
    assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='ELIGIBLE'
    for path,mutate in (
        (authority['release'],lambda v:dict(v,incident_disposition='CLEAR')),
        (authority['release'],lambda v:dict(v,final_ack_sha256='0'*64)),
        (authority['preaction'],lambda v:dict(v,assignment_id='WA-NOT-SELECTED')),
        (authority['preaction'],lambda v:dict(v,maintenance_head='0'*40)),
        (authority['preaction'],lambda v:dict(v,extra=True))):
        raw=path.read_bytes();path.write_bytes(_canonical(mutate(json.loads(raw))))
        try:
            assert subject.classify_quota_retention_locked(root,O,stamp)['state']=='KEEP'
            assert [p.read_bytes() for p in receipts]==original_bytes
        finally: path.write_bytes(raw)
    with _real_idle_broker(root):
        for when in ((authority['stamp']-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                     (authority['stamp']+timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')):
            with pytest.raises(subject.QuotaRetentionError):
                subject.prune_quota_receipts(root,O,when)
            assert [p.read_bytes() for p in receipts]==original_bytes
        stop=root/B/'autopilot/STOP';stop.symlink_to(stop.with_name('absent-owned-stop'))
        try:
            with pytest.raises(subject.QuotaRetentionError,match='STOP'):
                subject.prune_quota_receipts(root,O,stamp)
            assert [p.read_bytes() for p in receipts]==original_bytes
        finally: stop.unlink()
        from scripts import two_site_quota_wait_storage as storage
        actual_publish=storage._publish_cycle_record_bytes
        reached=[]
        def after_link(*args,**kwargs):
            raw=actual_publish(*args,**kwargs)
            if args[3]=='quota-history-retirement.json':
                reached.append(args[3])
                raise storage.WaitStorageError('ambiguous declared post-link failure')
            return raw
        with patch.object(storage,'_publish_cycle_record_bytes',after_link):
            with pytest.raises(storage.WaitStorageError,match='post-link'):
                subject.prune_quota_receipts(root,O,stamp)
        assert reached==['quota-history-retirement.json']
        assert [p.read_bytes() for p in receipts]==original_bytes
        assert subject.read_final_ack_history_locked(root,O)['state']=='CERTIFIED_RETIRED_PENDING'
        result=subject.prune_quota_receipts(root,O,stamp)
        assert result['state']=='CERTIFIED_RETIRED_COMPLETE'
        assert all(not path.exists() for path in receipts)

@pytest.mark.parametrize('stage',['write','file_fsync','close','link','directory_fsync','readback'])
@pytest.mark.parametrize('filename',['quota-history-retirement.json','quota-history-retirement-complete.json'])
def test_publication_faults_reconcile_without_false_completion(broker_repo,monkeypatch,stage,filename):
    from scripts import two_site_quota_wait_storage as storage
    fx,authority,root,expected,stamp,receipts=_retirement_case(broker_repo,monkeypatch)
    receipts=[root/row['relative_path'] for row in expected['collectible']]
    before=[p.read_bytes() for p in receipts]
    retained={row['relative_path']:(root/row['relative_path']).read_bytes() for row in expected['retained']}
    with _real_idle_broker(root),_quota_receipt_unlink_trace(root,receipts) as effects:
        with subject._maintenance_holds(root):pass
        allowed={row['relative_path'] for row in expected['collectible']}
        allowed|={(B/'autopilot/runs'/O/name).as_posix() for name in
                  ('quota-history-retirement.json','quota-history-retirement-complete.json')}
        whole=_quota_effect_frame(_frame(root),allowed)
        with _quota_publication_fault(monkeypatch,root,O,filename,stage) as fault:
            with pytest.raises(storage.WaitStorageError,match='injected'):
                subject.prune_quota_receipts(root,O,stamp)
        assert fault['reached']==[stage]
        assert _quota_effect_frame(_frame(root),allowed)==whole
        if filename=='quota-history-retirement.json':
            assert effects==[] and [p.read_bytes() for p in receipts]==before
        else:
            assert effects==[p.name for p in receipts] and all(not p.exists() for p in receipts)
        assert all((root/path).read_bytes()==raw for path,raw in retained.items())
        assert subject.prune_quota_receipts(root,O,stamp)['state']=='CERTIFIED_RETIRED_COMPLETE'
        assert all(not p.exists() for p in receipts)
        assert subject.read_final_ack_history_locked(root,O)['ack']==fx['ack']

@pytest.mark.parametrize('ordinal',[1,2])
@pytest.mark.parametrize('kind',['unlink','fsync','STOP'])
def test_each_unlink_fault_and_STOP_preserve_next_gate(broker_repo,monkeypatch,ordinal,kind):
    fx,authority,root,expected,stamp,receipts=_retirement_case(broker_repo,monkeypatch)
    paths=[root/row['relative_path'] for row in expected['collectible']]
    original_unlink=subject.os.unlink;original_fsync=subject.os.fsync
    calls=[];reached=[];stop=root/B/'autopilot/STOP'
    def unlink(name,*args,**kwargs):
        if name in {p.name for p in paths}:
            calls.append(name)
            if len(calls)==ordinal and kind=='unlink':
                reached.append(kind);raise OSError('injected exact unlink')
            result=original_unlink(name,*args,**kwargs)
            if len(calls)==ordinal and kind=='STOP':
                _w0600(stop,b'STOP\n');reached.append(kind)
            return result
        return original_unlink(name,*args,**kwargs)
    def fsync(fd):
        path=os.readlink('/proc/self/fd/'+str(fd))
        if (kind=='fsync' and len(calls)==ordinal and path==str(paths[ordinal-1].parent)
                and not paths[ordinal-1].exists()):
            reached.append(kind);raise OSError('injected exact unlink fsync')
        return original_fsync(fd)
    with _real_idle_broker(root):
        with patch.object(subject.os,'unlink',unlink),patch.object(subject.os,'fsync',fsync):
            with pytest.raises((OSError,subject.QuotaRetentionError)):
                subject.prune_quota_receipts(root,O,stamp)
        assert reached and calls==[p.name for p in paths[:ordinal]]
        assert all(not p.exists() for p in paths[:ordinal-(kind=='unlink')])
        assert all(p.exists() for p in paths[ordinal:])
        if stop.exists():stop.unlink() # Remove only this test-owned injected STOP.
        assert subject.read_final_ack_history_locked(root,O)['state']=='CERTIFIED_RETIRED_PENDING'
        assert subject.prune_quota_receipts(root,O,stamp)['state']=='CERTIFIED_RETIRED_COMPLETE'

def test_root_assignment_base_is_required_in_constructor_and_cold_certificate(broker_repo,monkeypatch):
    fx=_history(broker_repo);root=fx['root'];authority=_seed_root_authority(fx)
    stamp=authority['intent']['certified_at_utc']
    monkeypatch.setattr(subject,'_now_utc',lambda:authority['stamp'])
    path=authority['assignment'];healthy=path.read_bytes()
    line=('base_sha: '+fx['child_head']+'\n').encode()
    assert line in healthy and subject.classify_quota_retention_locked(root,O,stamp)['state']=='ELIGIBLE'
    for bad in (healthy.replace(line,b''),healthy.replace(line,b'base_sha: '+b'0'*40+b'\n')):
        path.write_bytes(bad)
        try:
            result=subject.classify_quota_retention_locked(root,O,stamp)
            assert result['state']=='KEEP' and 'base_sha' in result['reason']
        finally:path.write_bytes(healthy)
    with _real_idle_broker(root):
        with patch.object(subject,'_unlink_certified_receipt',side_effect=OSError('before first unlink')):
            with pytest.raises(OSError,match='before first'):
                subject.prune_quota_receipts(root,O,stamp)
    intent_path=root/B/'autopilot/runs'/O/'quota-history-retirement.json'
    intent_raw=intent_path.read_bytes()
    for bad in (healthy.replace(line,b''),healthy.replace(line,b'base_sha: '+b'0'*40+b'\n')):
        path.write_bytes(bad)
        forged=_quota_cold_base_intent(root,intent_raw,bad)
        intent_path.write_bytes(_canonical(forged))
        try:
            with _quota_base_guard(subject) as reached:
                with pytest.raises(subject.QuotaRetentionError,match='base_sha'):
                    subject.read_final_ack_history_locked(root,O)
                assert reached==[bad]
        finally:path.write_bytes(healthy);intent_path.write_bytes(intent_raw)
    assert subject.read_final_ack_history_locked(root,O)['state']=='CERTIFIED_RETIRED_PENDING'

def test_public_retired_guards_and_matched_healthy_controls(broker_repo,monkeypatch):
    from scripts import two_site_quota_continuation_admission as admission
    from scripts import two_site_quota_continuation_terminal_storage as terminal
    from scripts import two_site_quota_resume_role_dispatch as dispatch
    from scripts.two_site_semantic_attempts import reserve_semantic_attempt,SemanticAttemptError
    fx,root,resume,request,task,qrec,crec,clock=_retirement_public_inputs(broker_repo)
    Reached,positive,external=_quota_external_marker()
    with admission.normal_pipeline_context(root),patch.object(time,'time',lambda:clock):
        assert admission.validate_quota_continuation_request(root,resume)[0]==resume
        assert admission.admit_quota_continuation_request(root,resume)[0]==resume
        with patch.object(dispatch,'execute_worker_zero_once',external):
            with pytest.raises(Reached):dispatch.dispatch_quota_continuation_once(root,resume)
        with patch.object(broker,'_terminal_quota_directory',external):
            with pytest.raises(Reached):
                broker.publish_terminal_quota_record(root,qrec['request'],qrec['response'])
        with patch.object(terminal,'_open_terminal_directory',external):
            with pytest.raises(Reached):
                terminal.publish_terminal_continuation_record(root,crec['request'],crec['response'])
        with patch.object(broker.subprocess,'Popen',external):
            with pytest.raises(Reached):
                broker.execute_pipeline(root,request,fx['task_path'],fx['actual_scope'])
    assert len(positive)==4
    authority=_seed_root_authority(fx);stamp=authority['intent']['certified_at_utc']
    monkeypatch.setattr(subject,'_now_utc',lambda:authority['stamp'])
    with _real_idle_broker(root),patch.object(subject,'_unlink_certified_receipt',
                                                side_effect=OSError('before first unlink')):
        with pytest.raises(OSError,match='before first'):
            subject.prune_quota_receipts(root,O,stamp)
    _write_worktrees(root,path=str(root),branch=ap.BRANCH,active_assignment_id='WA-BROKER-TEST')
    assert ap.validate_repository(root)['head']==fx['head_before']
    state,status,requests,claimed,responses=broker.broker_paths(root)
    alias=_quota_request_alias(request)
    actual=subject.assert_not_retired;seen=[];provider=[]
    def guard(*args,**kwargs):
        seen.append(kwargs);return actual(*args,**kwargs)
    def forbidden(*args,**kwargs):provider.append(True);raise AssertionError('Provider reached')
    deny="quota retirement denies request";uncertain="quota identity is retired or uncertain"
    cases=[('submit-'+str(maintenance),
            lambda maintenance=maintenance:broker._submit_request(root,request,30,maintenance=maintenance),
            broker.BrokerError,deny) for maintenance in (False,True)]+[
      ('pipeline',lambda:broker.execute_pipeline(root,request,fx['task_path'],fx['actual_scope']),broker.BrokerError,deny),
      ('reserve',lambda:broker.publish_cycle_reservation(root,request),broker.BrokerError,deny),
      ('quota',lambda:broker.publish_terminal_quota_record(root,qrec['request'],qrec['response']),broker.BrokerError,deny),
      ('continuation',lambda:terminal.publish_terminal_continuation_record(root,crec['request'],crec['response']),terminal.TerminalContinuationStorageError,uncertain),
      ('admit',lambda:admission.validate_quota_continuation_request(root,resume),admission.ContinuationAdmissionError,uncertain),
      ('alias',lambda:admission.admit_quota_continuation_request(root,resume),admission.ContinuationAdmissionError,uncertain),
      ('role',lambda:dispatch.dispatch_quota_continuation_once(root,resume),admission.ContinuationAdmissionError,uncertain),
      ('facade',lambda:broker.execute_intent_bound_continuation(root,resume),subject.QuotaRetentionError,'retired'),
      ('semantic',lambda:reserve_semantic_attempt(root,root/B/'pipeline',task,task['task_id'],'alias'),SemanticAttemptError,'retired')]
    with patch.object(subject,'assert_not_retired',guard),patch.object(dispatch,'execute_worker_zero_once',forbidden):
        def same_witness():
            before=_frame(root);start=len(seen)
            with admission.normal_pipeline_context(root),patch.object(time,'time',lambda:clock):
                for label,call,error,message in cases:
                    try:
                        with pytest.raises(error,match=message) as caught:call()
                    except Exception as wrong:
                        raise AssertionError(label+' missed retirement guard') from wrong
                    if error is not subject.QuotaRetentionError:
                        assert type(caught.value.__cause__) is subject.QuotaRetentionError,label
            assert len(seen)-start==len(cases) and provider==[] and _frame(root)==before
        same_witness()
        with patch.object(subject,'assert_not_retired',lambda *a,**k:None):
            with pytest.raises((AssertionError,pytest.fail.Exception)):
                same_witness()
        same_witness()
        for family,call in [
            (requests,lambda:broker._claim_next(requests,claimed,responses,state/'terminal-quota')),
            (claimed,lambda:broker._recover_interrupted_claims(root,claimed,responses,requests,state/'terminal-quota'))]:
            path=family/(alias['request_id']+'.json');_w0600(path,_canonical(alias))
            try:
                before=_frame(root);start=len(seen)
                with pytest.raises(broker.BrokerError,match=deny) as caught:call()
                assert type(caught.value.__cause__) is subject.QuotaRetentionError
                assert len(seen)>start and _frame(root)==before and provider==[]
            finally:path.unlink()


@pytest.mark.parametrize('kind',['losing_open','replacement','mode','owner','hardlink'])
def test_held_lock_loser_open_and_inode_custody(broker_repo,monkeypatch,kind):
    fx,authority,root,expected,stamp,receipts=_retirement_case(broker_repo,monkeypatch)
    raw=[p.read_bytes() for p in receipts]
    with _real_idle_broker(root),subject._maintenance_holds(root) as check:
        with _quota_lock_fault(monkeypatch,root,kind) as reached:
            if kind=='losing_open':check()
            else:
                with pytest.raises(subject.QuotaRetentionError,match='held lock'):check()
        assert reached
        check()
    assert [p.read_bytes() for p in receipts]==raw
