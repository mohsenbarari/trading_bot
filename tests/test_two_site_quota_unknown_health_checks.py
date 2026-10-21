"""UNKNOWN checks never permit Provider/replay."""
from __future__ import annotations
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
import pytest
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_autopilot_monitor_preflight as monitor
from scripts import two_site_quota_wait_storage as storage
from scripts import two_site_quota_supervisor_bridge as bridge
from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_finalization_real_fixture import (
    _bootstrap_quota_history_lab,_w0600,_cj,SOURCE_ROOT,_frame)
from tests.test_two_site_autopilot_monitor_wiring import SUPERVISOR_BLOCK,run_shell_block

O='cycle-0001-20261020T120000Z-801'
C='cycle-0001-20261020T120000Z-802'


def test_actual_monitor_start_gate_supervisor_five_checks_and_exhaustion(broker_repo,monkeypatch):
    from tests.test_two_site_quota_finalization_real_fixture import _quota_unknown_case,_quota_forbid_downstream,_quota_frame_abi
    lab,fx,root,state,run,expected,stamp,immutable=_quota_unknown_case(broker_repo,'unknown',O,C,monkeypatch)
    wait=fx['wait'];repo=ap.validate_repository(root)
    semantic=next(p for p in immutable if p.parent.name=='semantic-attempts')
    semantic_before=immutable[semantic]
    ap.write_supervisor_state(state,'WAITING_QUOTA',cycle=1,head=repo['head'],
         evidence=str(state/'runs'/O),request_id=wait['request_id'],schedule=wait['schedule'],blockers=[])
    before=_frame(root)
    assert monitor.run_preflight(root,quota_wait_enabled=True)==(True,'QUOTA_READY')
    assert _frame(root)==before
    assert not list((state/'runs'/O).glob('quota-unknown-health-*.json'))

    report=root/'.local/two-site-refactor/monitor';report.mkdir(mode=0o700)
    with patch.dict(os.environ,{'PYTHONPATH':str(SOURCE_ROOT)}):
        result=run_shell_block(SUPERVISOR_BLOCK,root,state,report,
            supervisor_count=0,restart_eligible='true',provider_probe='false',
            protected_refs_ok='false',quota_wait_enabled=True)
    assert result['protected_refs_ok']=='true' and result['provider_probe']=='false'
    assert (report/'preflight.latest.log').read_bytes()==b'QUOTA_READY\n'

    downstream=_quota_forbid_downstream(monkeypatch)
    _quota_frame_abi(monkeypatch,root,state,repo,stamp,downstream)
    created=datetime.fromisoformat(wait['schedule']['created_at_utc'].replace('Z','+00:00'))
    due=created
    real_gmtime=time.gmtime
    expected_previous=__import__('hashlib').sha256(immutable[run/'quota-wait.json']).hexdigest()
    for ordinal,gap in enumerate((15,30,60,120,240),1):
        due+=timedelta(minutes=gap,seconds=1)
        now=due.strftime('%Y-%m-%dT%H:%M:%SZ')
        with patch.object(time,'gmtime',lambda seconds=None,at=due:
                          real_gmtime(at.timestamp() if seconds is None else seconds)):
            before=_frame(root)
            assert monitor.run_preflight(root,quota_wait_enabled=True)==(True,'QUOTA_READY')
            assert _frame(root)==before
            assert ap.run_loop(root,Path(sys.executable),1,300,True,
                               preparation_route='one_shot',quota_wait_enabled=True)==2
        checks=storage.read_unknown_health_checks(root,O,expected)
        assert len(checks)==ordinal
        last=checks[-1]
        assert set(last)=={'schema_version','record_kind','controller_run_id','wait_sha256',
                          'ordinal','previous_sha256','checked_at_utc','observation'}
        assert type(last['ordinal']) is int and last['ordinal']==ordinal
        assert last['previous_sha256']==expected_previous
        assert last['observation']=='LOCAL_BINDINGS_UNCHANGED' and last['checked_at_utc']==now
        raw=(state/'runs'/O/f'quota-unknown-health-{ordinal}.json').read_bytes()
        assert len(raw)<=4096 and raw==_cj(last).encode()
        expected_previous=__import__('hashlib').sha256(raw).hexdigest()
        projection=evaluate_initial_wait_check(wait,expected,now,health_checks=checks)
        assert projection['health_checks_remaining']==5-ordinal
        if ordinal<5:
            assert projection['state']=='NOT_DUE'
            next_due=due+timedelta(minutes=(15,30,60,120,240)[ordinal])
            assert projection['next_check_at_utc']==next_due.strftime('%Y-%m-%dT%H:%M:%SZ')
        else:
            assert projection=={'schema_version':1,'state':'UNKNOWN_HEALTH_CHECKS_EXHAUSTED',
                'next_check_at_utc':None,'health_checks_completed':5,'health_checks_remaining':0}
        assert all(p.read_bytes()==raw for p,raw in immutable.items())
        assert semantic.read_bytes()==semantic_before
        assert not (state/'runs'/O/'quota-resume-claim.json').exists()
        assert downstream==[]
    far=(due+timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with pytest.raises(storage.WaitStorageError,match='exhausted|not due'):
        storage.publish_unknown_health_check(root,O,expected,far)
    assert len(storage.read_unknown_health_checks(root,O,expected))==5 and downstream==[]

    target=state/'runs'/O/'quota-unknown-health-3.json'
    healthy=target.read_bytes();value=json.loads(healthy)
    value['wait_sha256']='0'*64
    target.write_bytes(_cj(value).encode())
    def same_witness():
        before=_frame(root)
        try:
            with pytest.raises(storage.WaitStorageError,match='binding'):
                storage.read_unknown_health_checks(root,O,expected)
        finally:
            assert _frame(root)==before
    try:
        same_witness()
        used=storage._read_unknown_health_checks_unframed
        reached=[]
        def bypass(*args,**kwargs):
            reached.append(args[1]);return []
        with patch.object(storage,'_read_unknown_health_checks_unframed',bypass):
            with pytest.raises(pytest.fail.Exception,match='DID NOT RAISE'):
                same_witness()
        assert reached==[O]
        same_witness()
    finally:
        target.write_bytes(healthy)
    assert storage._read_unknown_health_checks_unframed is used
    assert len(storage.read_unknown_health_checks(root,O,expected))==5

    target=state/'runs'/O/'quota-unknown-health-2.json'
    healthy=target.read_bytes()
    original=json.loads(healthy)
    mutations=[dict(original,ordinal=True),dict(original,ordinal=4),
        dict(original,previous_sha256='0'*64),dict(original,checked_at_utc=wait['schedule']['created_at_utc']),
        dict(original,observation='PROVIDER_READY'),dict(original,extra='x')]
    for bad in mutations:
        target.write_bytes(_cj(bad).encode())
        before=_frame(root)
        try:
            with pytest.raises(storage.WaitStorageError):
                storage.read_unknown_health_checks(root,O,expected)
            assert _frame(root)==before
        finally:
            target.write_bytes(healthy)
        assert len(storage.read_unknown_health_checks(root,O,expected))==5
    for raw in (b'{} '*2000,b'{"schema_version":"1.0","schema_version":"2.0"}',b'\xff'):
        target.write_bytes(raw)
        before=_frame(root)
        try:
            with pytest.raises((storage.WaitStorageError,ValueError,UnicodeError)):
                storage.read_unknown_health_checks(root,O,expected)
            assert _frame(root)==before
        finally:
            target.write_bytes(healthy)
    stop=state/'STOP';stop.symlink_to(state/'absent-owned-stop')
    try:
        assert monitor.run_preflight(root,quota_wait_enabled=True)==(False,'STOP_PRESENT')
        assert downstream==[]
    finally:
        stop.unlink()
    assert all(p.read_bytes()==raw for p,raw in immutable.items())
    assert semantic.read_bytes()==semantic_before and downstream==[]


def test_fixed_normal_context_complete_sync_lifetime_and_legacy_parity(broker_repo,tmp_path,monkeypatch):
    import sys
    from types import ModuleType
    from scripts import two_site_quota_continuation_admission as admission
    from scripts import two_site_quota_broker_route as route
    from scripts import two_site_provider_broker as broker
    lab=_bootstrap_quota_history_lab(broker_repo,test_paths=('tests/test_profile.py',))
    root=lab['root'];legacy=Path('.local/two-site-refactor');normal=legacy/'pipeline'
    assert admission.quota_semantic_directory(root)==legacy
    assert admission.quota_semantic_directory(root,'custom-legacy')==Path('custom-legacy')
    with pytest.raises(KeyboardInterrupt):
        with admission.normal_pipeline_context(root):
            raise KeyboardInterrupt('declared primary interruption')
    assert admission.quota_semantic_directory(root)==legacy
    other=tmp_path/'other-canonical-root';other.mkdir()
    with admission.normal_pipeline_context(root):
        with pytest.raises(admission.ContinuationAdmissionError,match='cross-root'):
            with admission.normal_pipeline_context(other): pass
        with pytest.raises(admission.ContinuationAdmissionError,match='another root'):
            admission.quota_semantic_directory(other)
        with pytest.raises(admission.ContinuationAdmissionError,match='unknown'):
            admission.quota_semantic_directory(root,'custom-legacy')
        with monkeypatch.context() as scoped:
            scoped.setitem(sys.modules,'two_site_quota_continuation_admission',ModuleType('foreign'))
            with pytest.raises(admission.ContinuationAdmissionError,match='duplicate'):
                admission.quota_semantic_directory(root)
    assert admission.quota_semantic_directory(root)==legacy
    with monkeypatch.context() as scoped:
        scoped.setitem(sys.modules,'two_site_quota_continuation_admission',admission)
        with admission.normal_pipeline_context(root):
            assert admission.quota_semantic_directory(root,legacy)==normal


@pytest.mark.parametrize('stage',['write','file_fsync','close','link','link_after_effect',
                                  'directory_fsync','cleanup_directory_fsync','readback','readback_oserror','alias_only_crash'])
def test_unknown_actual_publication_faults_never_claim_or_spend(broker_repo,monkeypatch,stage):
    from tests.test_two_site_quota_finalization_real_fixture import (
        _quota_publication_fault,_quota_unknown_case,_quota_forbid_downstream)
    lab,fx,root,state,run,expected,stamp,immutable=_quota_unknown_case(broker_repo,'crash',O,C,monkeypatch)
    downstream=_quota_forbid_downstream(monkeypatch);name='quota-unknown-health-1.json'
    injected='directory_fsync' if stage=='alias_only_crash' else stage
    with _quota_publication_fault(monkeypatch,root,O,name,injected) as fault:
        if stage=='cleanup_directory_fsync':
            assert storage.publish_unknown_health_check(root,O,expected,stamp)['ordinal']==1
        else:
            with pytest.raises(storage.WaitStorageError):
                storage.publish_unknown_health_check(root,O,expected,stamp)
    assert fault['reached']==[injected]
    if stage=='alias_only_crash':
        (run/name).unlink()  # Synthetic pre-link, not process-kill proof.
    checks=storage.read_unknown_health_checks(root,O,expected)
    assert len(checks)==(1 if stage in ('readback','readback_oserror','cleanup_directory_fsync') else 0)
    if not checks:
        storage.publish_unknown_health_check(root,O,expected,stamp)
    assert storage.read_unknown_health_checks(root,O,expected)[0]['checked_at_utc']==stamp
    assert len(storage.read_unknown_health_checks(root,O,expected))==1
    assert not list(run.glob('.quota-unknown-health-*')) and downstream==[]
    assert all(p.read_bytes()==raw for p,raw in immutable.items())
    assert not (run/'quota-resume-claim.json').exists()
    _w0600(state/'STOP',b'STOP\n')
    try:
        before=_frame(root)
        with pytest.raises(storage.WaitStorageError,match='STOP'):
            storage.publish_unknown_health_check(root,O,expected,stamp)
        assert _frame(root)==before
    finally:(state/'STOP').unlink()


@pytest.mark.parametrize('finish',['normal','STOP','interrupt'])
def test_actual_standing_generator_normal_profile_full_broker_and_late_exit(broker_repo,monkeypatch,finish):
 from scripts import two_site_quota_continuation_admission as admission
 from tests.test_two_site_quota_finalization_real_fixture import (
  _prepare_quota_history_wait,_retirement_idle_broker)
 lab=_bootstrap_quota_history_lab(broker_repo,test_paths=('tests/test_standing_normal.py',))
 sc=_prepare_quota_history_wait(lab,label='standing',original_cid=O,continuation_cid=C,
        test_path=lab['test_paths'][0],unknown=False)
 root=lab['root'];state=sc['autopilot_dir'];normal=Path('.local/two-site-refactor/pipeline')
 with ap.exclusive_supervisor_lock(state/'autopilot.lock'):
  from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
  wait=admit_initial_quota_wait_locked(root,O,clock=lambda:sc['now_utc']-timedelta(seconds=120))
 clock=datetime.fromisoformat(wait['schedule']['next_check_at_utc'].replace('Z','+00:00')).timestamp()+10
 actual_time=time.time;actual_gmtime=time.gmtime
 from tests.test_two_site_quota_finalization_real_fixture import _quota_standing_external_outcomes
 def observe(root_arg):
  assert admission.quota_semantic_directory(root_arg)==normal
 produced,seen=_quota_standing_external_outcomes(monkeypatch,sc,lab['test_paths'][0],observe)
 with patch.object(time,'time',lambda:clock),patch.object(time,'gmtime',lambda seconds=None:
     actual_gmtime(clock if seconds is None else seconds)):
  with _retirement_idle_broker(root,quota_enabled=True):
   manager=bridge.quota_codex_review_locked(root,O,C,300)
   try:
    with ap.exclusive_supervisor_lock(state/'autopilot.lock'),manager as context:
     assert context['controller_run_id']==C and context['original_controller_run_id']==O
     assert admission.quota_semantic_directory(root)==normal
     for value in (Path('.local/two-site-refactor'),normal,root/'.local/two-site-refactor'):
      assert admission.quota_semantic_directory(root,value)==normal
     assert seen==['external-worker','external-reviewer']
     assert context['ready']['proposal_sha256']==produced['prop_sha']
     assert not (root/'.local/two-site-refactor/provider-broker/responses'/
        (context['request_id']+'.json')).exists()
     if finish=='STOP':_w0600(state/'STOP',b'STOP\n')
     if finish=='interrupt':raise KeyboardInterrupt('caller interruption')
   except bridge.QuotaSupervisorBridgeError as error:
    assert finish=='STOP'
    assert type(error) is bridge.QuotaSupervisorBridgeError and type(error.__cause__) is bridge.BrokerError
    assert str(error)==str(error.__cause__)=='stop requested before continuation dispatch'
   except KeyboardInterrupt:
    assert finish=='interrupt'
   else:assert finish=='normal'
   finally:
    assert admission.quota_semantic_directory(root)==Path('.local/two-site-refactor')
    if (state/'STOP').exists():(state/'STOP').unlink()
 assert time.time is actual_time
 assert (state/'runs'/O/'quota-resume-claim.json').exists()
 assert (state/'runs'/O/'quota-resume-submission.json').exists()
 assert seen==['external-worker','external-reviewer']
 assert all(not list((root/'.local/two-site-refactor/provider-broker'/q).iterdir()) for q in ('requests','claimed'))


def test_real_python_script_alias_join_rejects_duplicate_module_without_effects(broker_repo):
    lab=_bootstrap_quota_history_lab(broker_repo,test_paths=('tests/test_alias_join.py',))
    root=lab['root'];before=_frame(root)
    code=("import sys;from pathlib import Path;"
          "sys.path[:0]=[str(Path(sys.argv[2]).parent),sys.argv[2]];"
          "from scripts import two_site_quota_continuation_admission as canonical;"
          "import two_site_quota_continuation_admission as bare;"
          "root=Path(sys.argv[1]);"
          "\ntry:\n with canonical.normal_pipeline_context(root): pass"
          "\nexcept canonical.ContinuationAdmissionError as e:\n assert 'duplicate' in str(e);print('DUPLICATE_DENIED')"
          "\nelse: raise AssertionError('duplicate alias accepted')")
    result=subprocess.run([sys.executable,'-c',code,str(root),str(SOURCE_ROOT/'scripts')],
        cwd=root,env=dict(os.environ,PYTHONPATH=str(SOURCE_ROOT),PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True,timeout=30)
    assert result.returncode==0 and result.stdout==b'DUPLICATE_DENIED\n' and result.stderr==b''
    assert _frame(root)==before


def test_unknown_stop_after_real_publication_blocks_actuator_before_any_claim(broker_repo,monkeypatch):
    from tests.test_two_site_quota_finalization_real_fixture import _quota_unknown_case
    lab,fx,root,state,run,expected,stamp,immutable=_quota_unknown_case(broker_repo,'stop',O,C,monkeypatch)
    actual=storage._publish_cycle_record_bytes;reached=[];downstream=[]
    def published_then_stop(*args,**kwargs):
        result=actual(*args,**kwargs)
        if args[3]=='quota-unknown-health-1.json':
            reached.append(args[3]);_w0600(state/'STOP',b'STOP\n')
        return result
    def forbidden(*args,**kwargs):downstream.append(True);raise AssertionError('Provider reached')
    monkeypatch.setattr(ap,'run_cycle',forbidden)
    from scripts import two_site_quota_supervisor_resume_submit as submit
    monkeypatch.setattr(submit,'submit_known_reset_resume_once_locked',forbidden)
    ap.write_supervisor_state(state,'WAITING_QUOTA',cycle=1,head=fx['head'],blockers=[])
    try:
        with patch.object(storage,'_publish_cycle_record_bytes',published_then_stop):
            assert ap.run_loop(root,Path(sys.executable),1,300,True,
                              preparation_route='one_shot',quota_wait_enabled=True)==2
        assert reached==['quota-unknown-health-1.json'] and downstream==[]
        assert len(storage.read_unknown_health_checks(root,O,expected))==1
        assert not (run/'quota-unknown-health-2.json').exists()
        assert not (run/'quota-resume-claim.json').exists()
        assert all(p.read_bytes()==raw for p,raw in immutable.items())
        assert monitor.run_preflight(root,quota_wait_enabled=True)==(False,'STOP_PRESENT')

    finally:
        if os.path.lexists(state/'STOP'):(state/'STOP').unlink()
    assert len(storage.read_unknown_health_checks(root,O,expected))==1


def test_actual_loop_pending_first_fsync_is_readonly_ready_and_reconciles_same_ordinal(broker_repo,monkeypatch):
    from tests.test_two_site_quota_finalization_real_fixture import (
        _quota_unknown_case,_quota_publication_fault,_quota_forbid_downstream,_quota_unknown_launch)
    lab,fx,root,state,run,expected,stamp,immutable=_quota_unknown_case(broker_repo,'pending',O,C,monkeypatch)
    downstream=_quota_forbid_downstream(monkeypatch)
    ap.write_supervisor_state(state,'WAITING_QUOTA',cycle=1,head=fx['head'],evidence=str(run),
         request_id=fx['wait']['request_id'],schedule=fx['wait']['schedule'],blockers=[])
    name='quota-unknown-health-1.json'
    with _quota_publication_fault(monkeypatch,root,O,name,'directory_fsync') as fault:
        for attempt in range(2):
            assert _quota_unknown_launch(root,stamp)==2
            assert len(storage.read_unknown_health_checks(root,O,expected))==0
            aliases=list(run.glob('.quota-unknown-health-*.tmp.*'))
            assert len(aliases)==1
            if attempt==0:pending=aliases[0].read_bytes();inode=aliases[0].stat().st_ino
            assert aliases[0].read_bytes()==pending and aliases[0].stat().st_ino==inode
            assert (run/name).read_bytes()==pending and (run/name).stat().st_nlink==2
            before=_frame(root)
            with patch.object(storage.os,'fsync',side_effect=AssertionError('observer fsync')):
                assert monitor.run_preflight(root,quota_wait_enabled=True)==(True,'QUOTA_READY')
            assert _frame(root)==before
            assert all(p.read_bytes()==raw for p,raw in immutable.items()) and downstream==[]
            assert not (run/'quota-resume-claim.json').exists()
        assert fault['reached']==['directory_fsync']*2
    for bad_now in ('not-UTC',fx['wait']['schedule']['created_at_utc']):
        before=_frame(root)
        with pytest.raises(storage.WaitStorageError,match='clock'):
            storage.publish_unknown_health_check(root,O,expected,bad_now)
        assert _frame(root)==before and downstream==[]
    alias=aliases[0]
    for kind in ('duplicate','different_inode','malformed','STOP'):
        saved=alias.with_name('.owned-original-target')
        extra=alias.with_name(alias.name+('.bad' if kind=='malformed' else '1'))
        if kind=='duplicate':os.link(alias,extra)
        elif kind=='different_inode':(run/name).rename(saved);_w0600(run/name,pending)
        elif kind=='malformed':alias.rename(extra)
        else:_w0600(state/'STOP',b'STOP\n')
        try:
            before=_frame(root)
            with pytest.raises(storage.WaitStorageError):
                storage.publish_unknown_health_check(root,O,expected,stamp)
            assert _frame(root)==before and downstream==[]
        finally:
            if kind=='duplicate':extra.unlink()
            elif kind=='different_inode':(run/name).unlink();saved.rename(run/name)
            elif kind=='malformed':extra.rename(alias)
            else:(state/'STOP').unlink()
        assert storage.read_unknown_health_checks(root,O,expected)==[]
    assert _quota_unknown_launch(root,stamp)==2
    checks=storage.read_unknown_health_checks(root,O,expected)
    assert len(checks)==1 and checks[0]['ordinal']==1 and checks[0]['checked_at_utc']==stamp
    assert (run/name).read_bytes()==pending and not alias.exists()
    assert not (run/'quota-unknown-health-2.json').exists()
    assert all(p.read_bytes()==raw for p,raw in immutable.items()) and downstream==[]
    assert not (run/'quota-resume-claim.json').exists()


def test_actual_broker_main_help_composes_canonical_normal_identity_without_effects(broker_repo):
    root=broker_repo[0];before=_frame(root)
    code="""import contextlib,io,runpy,sys
from pathlib import Path
script=Path(sys.argv[1]);root=Path(sys.argv[2])
sys.path[:0]=[str(script.parent.parent),str(script.parent)];sys.argv=[str(script),'--root',str(root),'--help']
out=io.StringIO();entered=[]
def observe(frame,event,arg):
 if event=='call' and frame.f_code.co_name=='main' and frame.f_globals.get('__file__')==str(script):
  entered.append(sys.modules['__main__'])
prior=sys.getprofile();sys.setprofile(observe)
try:
 with contextlib.redirect_stdout(out):
  try:runpy.run_path(str(script),run_name='__main__')
  except SystemExit as exc:assert type(exc.code)is int and exc.code==0
  else:raise AssertionError('real argparse did not exit')
finally:sys.setprofile(prior)
assert 'usage:' in out.getvalue() and 'submit-maintenance' in out.getvalue()
from scripts import two_site_provider_broker as broker
assert len(entered)==1 and broker is entered[0] and broker.__name__=='__main__'
assert Path(broker.__file__)==script
assert sys.modules.get('two_site_provider_broker',broker)is broker
from scripts import two_site_quota_continuation_admission as admission
legacy=Path('.local/two-site-refactor');normal=legacy/'pipeline'
assert admission.quota_semantic_directory(root)==legacy
with admission.normal_pipeline_context(root):
 for supplied in (legacy,normal):
  assert admission.quota_semantic_directory(root,supplied)==normal
 assert admission.quota_semantic_directory(root)==normal
assert admission.quota_semantic_directory(root)==legacy
print('ACTUAL_CLI_NORMAL_IDENTITY_OK')
"""
    result=subprocess.run([sys.executable,'-c',code,str(SOURCE_ROOT/'scripts/two_site_provider_broker.py'),str(root)],
        cwd=root,env=dict(os.environ,PYTHONPATH=str(SOURCE_ROOT),PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True,timeout=30)
    assert result.returncode==0 and result.stdout==b'ACTUAL_CLI_NORMAL_IDENTITY_OK\n' and result.stderr==b''
    assert _frame(root)==before
