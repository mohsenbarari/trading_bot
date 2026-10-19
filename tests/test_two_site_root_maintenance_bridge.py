"""LAB-only bridge proof: real Git/Scope/Broker/checkpoint, never live authority."""
from __future__ import annotations
import os
os.environ['APP_ENV_FILE'] = 'config/unit-test.env.example'
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import copy
import fcntl

import pytest
from scripts import two_site_root_maintenance_bridge as subject
from scripts import two_site_maintenance_transition_startup as startup
from scripts import two_site_maintenance_transition_storage as storage
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_quarantine_recovery_storage as quarantine

from tests import two_site_root_maintenance_bridge_fixtures as helpers


@pytest.fixture
def lab(tmp_path, monkeypatch):
    return helpers.build_lab(tmp_path, monkeypatch)


def test_real_scope_git_broker_checkpoint_and_complete_detached_result(lab,monkeypatch):
    before=helpers.custody(lab); calls=[]; actual=subject._run_git_query
    def query(root,args,**kwargs):
        assert root==lab['root']
        descriptor=os.open(root/'.local/two-site-refactor/provider-broker/client.lock',os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(descriptor)
        calls.append((args,kwargs));return actual(root,args,**kwargs)
    monkeypatch.setattr(subject,'_run_git_query',query)
    expected={**copy.deepcopy(lab['record']),'new_repository':ap.validate_accepted_repository(lab['repository'])}
    result=helpers.run(lab)
    assert result==expected and len(calls)==2
    assert calls[0]==(['rev-list','--reverse','--parents',lab['record']['prior_head']+'..'+lab['marker']['head']],{'output_limit':16384})
    assert calls[1]==(['merge-base','--is-ancestor',lab['record']['prior_head'],lab['marker']['head']],{'output_limit':4096})
    result['commits'][0]['head']='0'*40;result['new_repository']['control_blobs'].clear()
    assert helpers.run(lab)==expected and helpers.custody(lab)==before


def test_unmarked_live_scope_only_returns_none_without_bridge_or_checkpoint_reads(lab,monkeypatch):
    lab['active_path'].write_bytes(helpers.document(helpers.DESTINATION));lab['repository']=lab['repository_oracle'](lab['root'])
    def forbidden(*args,**kwargs):raise AssertionError('unmarked Scope must not consult bridge/checkpoint/evidence')
    monkeypatch.setattr(subject,'_load_and_audit_accepted_checkpoint',forbidden)
    monkeypatch.setattr(subject,'bounded_file_bytes',forbidden)
    assert helpers.run(lab) is None


@pytest.mark.parametrize('edit',[
    lambda r:r.update(extra=True),lambda r:r.update(schema_version=1),lambda r:r.update(kind='REVIEWED_MAINTENANCE_TRANSITION'),
    lambda r:r.update(destination_assignment_id='FOREIGN'),lambda r:r.update(prior_head='0'*40),
    lambda r:r.update(prior_checkpoint_sha256='0'*64),lambda r:r['repository'].update(assignment_scope={}),
    lambda r:r['repository'].update(head='0'*40),lambda r:r['repository']['protected_refs'].update({'refs/heads/main':'0'*40}),
    lambda r:r['repository']['control_blobs'].update({next(iter(r['repository']['control_blobs'])):'0'*40}),
    lambda r:r.update(commits=[]),lambda r:r['commits'].append(copy.deepcopy(r['commits'][0])),
    lambda r:r['commits'][0].update(parent='0'*40),lambda r:r['commits'][0].update(evidence_path='../../foreign'),
    lambda r:r['commits'][0].update(evidence_sha256='0'*64),lambda r:r.update(history_audit_sha256='0'*64),
    lambda r:r.update(handoff_scope_sha256='0'*64),lambda r:r.update(handoff_assignment_id=helpers.DESTINATION),
])
def test_marked_structural_or_binding_failures_never_accept_or_mutate(lab,edit):
    helpers.replace_bundle(lab,record_edit=edit)
    helpers.reject_read(lab)


@pytest.mark.parametrize('edit',[
    lambda p:p.update(broker_posthold_completed=False),lambda p:p.update(broker_posthold_completed=1),
    lambda p:p.update(operation_error_type='OSError'),lambda p:p.update(record_sha256='0'*64),
    lambda p:p.update(head='0'*40),lambda p:p.update(destination_assignment_id='FOREIGN'),
    lambda p:p.update(handoff_scope_sha256='0'*64),lambda p:p.update(checkpoint_changed=True),
    lambda p:p.update(reservations_changed=True),lambda p:p.update(Supervisor_started=True),
    lambda p:p.update(extra=True),
])
def test_separately_pinned_normal_posthold_is_required_not_a_record_flag(lab,edit):
    helpers.replace_bundle(lab,post_edit=edit)
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault',['missing_record','missing_post','missing_audit','record_wire','post_wire',
    'record_mode','record_hardlink','record_symlink','directory_mode','checkpoint','scope','old_scope',
    'root_stop','state_stop','broker','queue','pending','evidence'])
def test_real_file_scope_checkpoint_broker_and_pending_faults_block(lab,fault):
    record_path=lab['directory']/(lab['marker']['record_sha256']+'.json')
    post_path=lab['directory']/(lab['marker']['posthold_sha256']+'.json')
    if fault.startswith('missing_'):
        {'missing_record':record_path,'missing_post':post_path,'missing_audit':lab['directory']/(helpers.sha(lab['audit'])+'.json')}[fault].unlink()
    elif fault in ('record_wire','post_wire'):
        path=record_path if fault=='record_wire' else post_path;path.write_bytes(path.read_bytes()+b' ')
    elif fault=='record_mode':record_path.chmod(0o644)
    elif fault=='record_hardlink':os.link(record_path,record_path.with_name('alias'))
    elif fault=='record_symlink':record_path.rename(record_path.with_name('retained'));record_path.symlink_to('retained')
    elif fault=='directory_mode':lab['directory'].chmod(0o755)
    elif fault=='checkpoint':(lab['state']/'accepted_checkpoint.json').write_bytes(b'{}')
    elif fault=='scope':lab['active_path'].write_bytes(lab['active_path'].read_bytes()+b'changed')
    elif fault=='old_scope':lab['old_path'].write_bytes(lab['old_path'].read_bytes()+b'changed')
    elif fault in ('root_stop','state_stop'):(lab['root'] if fault=='root_stop' else lab['state']).joinpath('STOP').write_bytes(b'')
    elif fault=='broker':helpers.private(lab['root']/'.local/two-site-refactor/provider-broker/status.json',b'{}')
    elif fault=='queue':helpers.private(lab['root']/'.local/two-site-refactor/provider-broker/requests/foreign.json',b'{}')
    elif fault=='pending':helpers.private(lab['state']/'runs/cycle-0001-20261017T000000Z-1/quota-resume-claim.json',b'{}')
    else:lab['evidence_path'].write_bytes(b'changed')
    helpers.reject_read(lab,expect_post=fault not in ('broker','queue'))


@pytest.mark.parametrize('fault',['record','audit','checkpoint','scope','source','ref','stop','broker_exit'])
def test_late_broker_boundary_faults_do_not_return_success(lab,monkeypatch,fault):
    original=subject.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def disrupted(root):
        with original(root):
            yield
        if fault=='record':(lab['directory']/(lab['marker']['record_sha256']+'.json')).write_bytes(b'{}')
        elif fault=='audit':(lab['directory']/(helpers.sha(lab['audit'])+'.json')).write_bytes(b'{}')
        elif fault=='checkpoint':(lab['state']/'accepted_checkpoint.json').write_bytes(b'{}')
        elif fault=='scope':lab['active_path'].write_bytes(lab['active_path'].read_bytes()+b'changed')
        elif fault=='source':(root/ap.CONTROL_PATHS[0]).write_bytes(b'changed')
        elif fault=='ref':helpers.git(root,'update-ref',ap.PROTECTED_REFS[0],lab['marker']['head'])
        elif fault=='stop':(root/'STOP').write_bytes(b'')
        else:raise OSError('LAB exit failure')
    monkeypatch.setattr(subject,'hold_idle_broker_client_lock_for_cold_adoption',disrupted)
    with pytest.raises(subject.RootMaintenanceBridgeError):helpers.run(lab)


def test_both_shared_callers_use_real_marked_route_without_legacy_fallback(lab,monkeypatch):
    fallback_calls=[]
    def forbidden(*args,**kwargs):
        fallback_calls.append(args);raise AssertionError('marked failure or success cannot use old provenance')
    monkeypatch.setattr(startup,'read_maintenance_transition_locked',forbidden)
    monkeypatch.setattr(quarantine,'read_quarantine_recovery_locked',forbidden)
    before=helpers.custody(lab)
    with ap.exclusive_supervisor_lock(lab['state']/'autopilot.lock'):
        startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
        for token in ('1','2'):(lab['state']/('rescope-'+token*64)).mkdir(mode=0o700)
        ap.validate_rescope_epoch_startup(lab['state'],lab['repository'],root=lab['root'])
    assert helpers.custody(lab)==before
    from scripts import two_site_autopilot_monitor_preflight as monitor
    monkeypatch.setattr(monitor,'validate_repository',lab['repository_oracle'])
    helpers.private(lab['state']/'status.json',ap.serialize_json(dict(head=lab['marker']['head'],state='CONTINUE')))
    assert monitor.run_preflight(lab['root'])==(True,'READY')
    helpers.replace_bundle(lab,post_edit=lambda p:p.update(broker_posthold_completed=False))
    with ap.exclusive_supervisor_lock(lab['state']/'autopilot.lock'):
        with pytest.raises(startup.MaintenanceTransitionStartupError):
            startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
        with pytest.raises(ap.AutopilotError):
            ap.validate_rescope_epoch_startup(lab['state'],lab['repository'],root=lab['root'])
    assert monitor.run_preflight(lab['root'])==(False,'STALE_CHECKPOINT')
    assert fallback_calls==[]


def test_pure_marker_strict_bytes_utf8_duplicate_marker_and_unmarked_data(lab):
    class Hostile(bytes):
        def decode(self,*a,**kw):raise AssertionError('hostile decode')
        def __len__(self):raise AssertionError('hostile len')
    marker_line=b'ROOT_MAINTENANCE_BRIDGE_V1: '+helpers.canonical(lab['marker'])
    for raw in (Hostile(marker_line),bytearray(marker_line),b'\xff',marker_line+marker_line,
                b'ROOT_MAINTENANCE_BRIDGE_V1: {}\n',b'ROOT_MAINTENANCE_BRIDGE_V1: {"head":NaN}\n'):
        with pytest.raises(subject.RootMaintenanceBridgeError):subject.parse_scope_bridge_marker(raw)
    assert subject.parse_scope_bridge_marker(b'# no marker\n') is None
    assert subject.parse_scope_bridge_marker(marker_line)==lab['marker']
    duplicate=marker_line.replace(b'{',b'{"head":"'+b'0'*40+b'",',1)
    with pytest.raises(subject.RootMaintenanceBridgeError):subject.parse_scope_bridge_marker(duplicate)


def test_record_and_historical_raw_read_budgets_forward_then_restore(lab,monkeypatch):
    original=lab['evidence_path'].read_bytes();body=b'X'*200000
    lab['evidence_path'].write_bytes(body)
    helpers.replace_bundle(lab,record_edit=lambda r:r['commits'][0].update(evidence_sha256=helpers.sha(body)))
    actual=subject.bounded_file_bytes;reads=[]
    def observer(root,path,limit,label):
        if path==lab['evidence_path']:reads.append(limit)
        return actual(root,path,limit,label)
    monkeypatch.setattr(subject,'bounded_file_bytes',observer)
    assert helpers.run(lab)['repository']['head']==lab['marker']['head'] and reads and set(reads)=={200000}
    lab['evidence_path'].write_bytes(body+b'X');reads.clear()
    with pytest.raises(subject.RootMaintenanceBridgeError):helpers.run(lab)
    assert reads==[200000]
    lab['evidence_path'].write_bytes(original)
    helpers.replace_bundle(lab)
    assert helpers.run(lab)['repository']['head']==lab['marker']['head']


@pytest.mark.parametrize('exc',[KeyboardInterrupt(),SystemExit(),GeneratorExit()])
@pytest.mark.parametrize('late',['none','close','source'])
def test_unexpected_base_exceptions_preserved_no_fallback(lab,monkeypatch,exc,late):
    events=[]; actual_scope=subject._scope_bytes; actual_close=subject._close
    actual_broker=subject.hold_idle_broker_client_lock_for_cold_adoption
    source=lab['root']/ap.CONTROL_PATHS[0]; original=source.read_bytes(); before=helpers.custody(lab)
    @contextmanager
    def observed(root):
        with actual_broker(root):
            yield
            events.append('body-returned')
        events.append('actual-posthold')
        if late=='source':source.write_bytes(b'LAB late Source drift')
    def close(descriptor):
        actual_close(descriptor);events.append('closed')
        if late=='close':raise OSError('LAB close error after actual close')
    def interrupted(*args,**kwargs):raise exc
    monkeypatch.setattr(subject,'_scope_bytes',interrupted)
    monkeypatch.setattr(subject,'_close',close)
    monkeypatch.setattr(subject,'hold_idle_broker_client_lock_for_cold_adoption',observed)
    try:
        with pytest.raises(type(exc)) as caught:helpers.run(lab)
        assert caught.value is exc
        assert events==['closed','closed','body-returned','actual-posthold']
    finally:
        monkeypatch.setattr(subject,'_scope_bytes',actual_scope)
        monkeypatch.setattr(subject,'_close',actual_close)
        monkeypatch.setattr(subject,'hold_idle_broker_client_lock_for_cold_adoption',actual_broker)
        source.write_bytes(original)
    assert helpers.custody(lab)==before
    assert helpers.run(lab)['repository']['head']==lab['marker']['head']


def test_unmarked_old_routes_and_main_hard_failure_remain_distinct(lab,monkeypatch):
    monkeypatch.setattr(startup,'read_root_maintenance_bridge_locked',lambda *a:None)
    norm=ap.validate_accepted_repository(lab['repository'])
    monkeypatch.setattr(startup,'read_maintenance_transition_locked',lambda *a:dict(new_repository=norm))
    startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
    calls=[]
    def recovery(*args):
        calls.append(args);return dict(record=dict(new_repository=norm))
    monkeypatch.setattr(quarantine,'read_quarantine_recovery_locked',recovery)
    monkeypatch.setattr(startup,'read_maintenance_transition_locked',lambda *a:None)
    startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
    assert len(calls)==1
    def main_failure(*args):raise storage.MainMaintenanceRouteError('LAB known main failure')
    monkeypatch.setattr(startup,'read_maintenance_transition_locked',main_failure)
    with pytest.raises(startup.MaintenanceTransitionStartupError,match='main maintenance verification failed'):
        startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
    assert len(calls)==1


def test_pure_decoder_and_history_count_boundaries_are_not_authority(lab):
    base=helpers.canonical(dict(padding=''))
    exact=helpers.canonical(dict(padding='x'*(32768-len(base))))
    assert len(exact)==32768 and subject._json(exact,32768)['padding']
    with pytest.raises(subject.RootMaintenanceBridgeError):subject._json(exact+b' ',32768)
    for count in (32,33):
        value={}
        for _ in range(count):value={'nested':value}
        if count==32:assert subject._json(helpers.canonical(value),32768)==value
        else:
            with pytest.raises(subject.RootMaintenanceBridgeError):subject._json(helpers.canonical(value),32768)
    for count in (8190,8191):
        raw=helpers.canonical(dict(items=[0]*count))
        if count==8190:assert len(subject._json(raw,32768)['items'])==count
        else:
            with pytest.raises(subject.RootMaintenanceBridgeError):subject._json(raw,32768)
    for count in (64,65,66,67):
        record=copy.deepcopy(lab['record']);rows=[];parent=record['prior_head']
        for n in range(count):
            head=record['repository']['head'] if n==count-1 else format(n+100,'040x')
            rows.append(dict(head=head,parent=parent,evidence_path='.local/two-site-refactor/reviews/LAB_'+str(n)+'.json',evidence_sha256='1'*64));parent=head
        record['commits']=rows;raw=helpers.canonical(record);marker={**lab['marker'],'record_sha256':helpers.sha(raw)}
        args=dict(marker=marker,prior_repository=lab['prior'],current_repository=ap.validate_accepted_repository(lab['repository']),destination_assignment_id=helpers.DESTINATION)
        if count<=66:assert subject.parse_root_maintenance_bridge_bytes(raw,**args)==record
        else:
            with pytest.raises(subject.RootMaintenanceBridgeError):subject.parse_root_maintenance_bridge_bytes(raw,**args)


def test_real_64_65_66_chain_count_only_67_rejection_then_healthy_restore(lab,monkeypatch):
    root=lab['root'];rows=copy.deepcopy(lab['record']['commits'])
    protected=copy.deepcopy(lab['prior']['protected_refs'])
    checkpoint=(lab['state']/'accepted_checkpoint.json').read_bytes()
    actual_query=subject._run_git_query;queries=[]
    def query(*args,**kwargs):
        queries.append((args[1],kwargs));return actual_query(*args,**kwargs)
    monkeypatch.setattr(subject,'_run_git_query',query)
    def custody():
        snapshot=helpers.custody(lab)
        return (snapshot[:6],snapshot[7],helpers.git(root,'ls-files','--stage'),
            tuple((row['evidence_path'],(root/row['evidence_path']).read_bytes()) for row in rows))
    for count in range(2,67):
        parent=helpers.git(root,'rev-parse','HEAD')
        path=ap.CONTROL_PATHS[0]
        (root/path).write_text('LAB real sole-parent code provenance '+str(count)+'\n')
        helpers.git(root,'add','--',path.as_posix())
        helpers.git(root,'commit','-qm','LAB chain '+str(count)+', not live Source acceptance')
        head=helpers.git(root,'rev-parse','HEAD')
        evidence_path='.local/two-site-refactor/reviews/LAB_CHAIN_'+str(count)+'.json'
        evidence=helpers.canonical(dict(LAB=True,head=head,parent=parent,not_Worker_acceptance=True))
        helpers.private(root/evidence_path,evidence)
        rows.append(dict(head=head,parent=parent,evidence_path=evidence_path,evidence_sha256=helpers.sha(evidence)))
        if count not in (64,65,66):continue
        current=ap.validate_accepted_repository(lab['repository_oracle'](root))
        lab['record']['repository']={key:value for key,value in current.items() if key!='assignment_scope'}
        lab['record']['commits']=copy.deepcopy(rows)
        lab['audit']=helpers.canonical(dict(LAB=True,head=head,prior=rows[0]['parent'],
            actual_Git_chain_count=count,not_startup_authority=True))
        helpers.private(lab['directory']/(helpers.sha(lab['audit'])+'.json'),lab['audit'])
        lab['record']['history_audit_sha256']=helpers.sha(lab['audit'])
        lab['post']['head']=head;lab['marker']['head']=head
        helpers.replace_bundle(lab)
        before=custody();full_before=helpers.custody(lab);queries.clear()
        expected={**copy.deepcopy(lab['record']),
            'new_repository':ap.validate_accepted_repository(lab['repository'])}
        assert helpers.run(lab)==expected
        assert len(rows)==count and len(queries)==2
        assert queries[0]==(['rev-list','--reverse','--parents',
            lab['record']['prior_head']+'..'+head],{'output_limit':16384})
        assert queries[1]==(['merge-base','--is-ancestor',lab['record']['prior_head'],head],{'output_limit':4096})
        assert helpers.git(root,'rev-list','--reverse','--parents',
            lab['record']['prior_head']+'..'+head).splitlines()==[
                row['head']+' '+row['parent'] for row in rows]
        assert custody()==before and helpers.custody(lab)==full_before
    assert subject.MAX_COMMITS==66 and len(rows)==66
    healthy=copy.deepcopy(expected);healthy_custody=custody()
    def add_count_only_row(record):
        synthetic='f'*40
        assert synthetic not in {row['head'] for row in record['commits']}
        record['commits'][0]['parent']=synthetic
        record['commits'].insert(0,dict(head=synthetic,parent=record['prior_head'],
            evidence_path='.local/two-site-refactor/reviews/LAB_COUNT_ONLY_67.json',evidence_sha256='1'*64))
    raw,marker=helpers.replace_bundle(lab,record_edit=add_count_only_row)
    args=dict(marker=marker,prior_repository=lab['prior'],
        current_repository=ap.validate_accepted_repository(lab['repository']),
        destination_assignment_id=helpers.DESTINATION)
    record=subject._json(raw,32768)
    assert len(record['commits'])==67
    with pytest.raises(subject.RootMaintenanceBridgeError):subject.parse_root_maintenance_bridge_bytes(raw,**args)
    # Reached PURE mutation oracle: identical67 bytes are otherwise valid shape.
    # The actual reader below runs only after restoration to the real66 bound.
    with monkeypatch.context() as pure:
        pure.setattr(subject,'MAX_COMMITS',67)
        assert subject.parse_root_maintenance_bridge_bytes(raw,**args)==record
    assert subject.MAX_COMMITS==66
    actual_parse=subject.parse_root_maintenance_bridge_bytes
    actual_read=subject.bounded_file_bytes;parsed_counts=[];evidence_reads=[]
    evidence_paths={root/row['evidence_path'] for row in record['commits']}
    evidence_paths.add(lab['directory']/(lab['record']['history_audit_sha256']+'.json'))
    def parse(*a,**kw):
        parsed_counts.append(len(subject._json(a[0],32768)['commits']))
        return actual_parse(*a,**kw)
    def read(root_arg,path,*a,**kw):
        if path in evidence_paths:evidence_reads.append(path)
        return actual_read(root_arg,path,*a,**kw)
    queries.clear()
    with monkeypatch.context() as fault:
        fault.setattr(subject,'parse_root_maintenance_bridge_bytes',parse)
        fault.setattr(subject,'bounded_file_bytes',read)
        helpers.reject_read(lab)
    assert parsed_counts==[67] and evidence_reads==[] and queries==[]
    helpers.replace_bundle(lab)
    assert custody()==healthy_custody
    full_before=helpers.custody(lab)
    assert helpers.run(lab)==healthy and helpers.custody(lab)==full_before
    assert (lab['state']/'accepted_checkpoint.json').read_bytes()==checkpoint
    assert {ref:helpers.git(root,'rev-parse',ref) for ref in protected}==protected


def test_real_shared_four_million_limit_uses_remaining_then_healthy_restore(lab,monkeypatch):
    body=b'X'*200000;rows=[];parent=lab['record']['prior_head']
    for n in range(20):
        head=lab['marker']['head'] if n==19 else format(n+500,'040x')
        path='.local/two-site-refactor/reviews/LAB_BUDGET_'+str(n)+'.json'
        helpers.private(lab['root']/path,body)
        rows.append(dict(head=head,parent=parent,evidence_path=path,evidence_sha256=helpers.sha(body)));parent=head
    helpers.replace_bundle(lab,record_edit=lambda r:r.update(commits=rows))
    actual=subject.bounded_file_bytes;limits=[]
    def observer(root,path,limit,label):
        if path.name.startswith('LAB_BUDGET_'):limits.append(limit)
        return actual(root,path,limit,label)
    monkeypatch.setattr(subject,'bounded_file_bytes',observer)
    with pytest.raises(subject.RootMaintenanceBridgeError):helpers.run(lab)
    assert limits==[200000]*19+[4000000-len(lab['audit'])-19*200000]+[200000]*38
    helpers.replace_bundle(lab)
    assert helpers.run(lab)['repository']['head']==lab['marker']['head']
