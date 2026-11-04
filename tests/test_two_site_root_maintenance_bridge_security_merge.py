"""Hermetic actual Git V3 security-merge proofs; no live publication or authority."""
from __future__ import annotations
import os
os.environ['APP_ENV_FILE']='config/unit-test.env.example'
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
from contextlib import contextmanager
import copy,json
import pytest
from scripts import two_site_root_maintenance_bridge as subject
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_maintenance_transition_startup as startup
from scripts import two_site_quarantine_recovery_storage as quarantine
from scripts import two_site_maintenance_transition_git as gitproof
from tests import two_site_root_maintenance_bridge_fixtures as helpers

MAIN='refs/heads/main'
ORIGIN='refs/remotes/origin/main'
CANDIDATE='refs/heads/candidate/wa-ir-standby-v1'
ERROR='^root maintenance bridge verification failed$'

def _history(root,old,target):
    raw=(helpers.git(root,'rev-list','--reverse','--topo-order','--parents',old+'..'+target)+'\n').encode()
    return dict(commit_count=len(raw.splitlines()),utf8_bytes=len(raw),sha256=helpers.sha(raw))

def _publish(lab,record,*,post_edit=None,marker_edit=None,prefix=None):
    raw=helpers.canonical(record)
    post=copy.deepcopy(lab['post'])
    post.update(schema_version='3.0',kind=subject.POSTHOLD_V3,record_sha256=helpers.sha(raw),
        prior_checkpoint_sha256=record['prior_checkpoint_sha256'],head=record['repository']['head'])
    if post_edit:post_edit(post)
    post_raw=helpers.canonical(post)
    marker=dict(record_sha256=helpers.sha(raw),posthold_sha256=helpers.sha(post_raw),
        checkpoint_sha256=record['prior_checkpoint_sha256'],head=record['repository']['head'],
        security_commit=lab['target'])
    if marker_edit:marker_edit(marker)
    helpers.private(lab['directory']/(helpers.sha(raw)+'.json'),raw)
    helpers.private(lab['directory']/(helpers.sha(post_raw)+'.json'),post_raw)
    lab['active_path'].write_bytes(helpers.document(helpers.DESTINATION)+b'\n'+
        (prefix or subject.MARKER_V3.encode())+b' '+helpers.canonical(marker))
    lab['repository']=lab['repository_oracle'](lab['root'])
    lab['marker']=marker
    return raw

def _pure(lab,raw):
    marker=subject.parse_scope_bridge_marker(lab['active_path'].read_bytes())
    return subject.parse_root_maintenance_bridge_bytes(raw,marker=marker,
        prior_repository=lab['prior'],current_repository=ap.validate_accepted_repository(lab['repository']),
        destination_assignment_id=helpers.DESTINATION)

@pytest.fixture
def lab(tmp_path,monkeypatch):
    value=helpers.build_lab(tmp_path,monkeypatch);root=value['root']
    # Replace only owned LAB construction, not any historical/live repository.
    # All actual final19 controls are the original unchanged tree.
    initial=value['prior']['head'];tree=helpers.git(root,'rev-parse',initial+'^{tree}')
    base=helpers.git(root,'commit-tree',tree,'-m','LAB security common root')
    prior=helpers.git(root,'commit-tree',tree,'-p',base,'-m','LAB divergent integration checkpoint')
    old_origin=helpers.git(root,'commit-tree',tree,'-p',base,'-m','LAB historical origin main')
    old_main=helpers.git(root,'commit-tree',tree,'-p',old_origin,'-m','LAB historical local main')
    helpers.git(root,'reset','--hard',prior)
    helpers.git(root,'update-ref',MAIN,old_main)
    helpers.git(root,'update-ref',ORIGIN,old_origin)
    old_scope=value['prior']['assignment_scope']
    from scripts.two_site_assignment_scope import parse_assignment_scope_bytes
    scope=parse_assignment_scope_bytes(value['old_path'].read_bytes(),expected_assignment_id=helpers.HANDOFF,
                                      integration_branch=ap.BRANCH)
    prior_repo=value['repository_oracle'](root)
    prior_repo['assignment_scope']=scope
    value['prior']=ap.validate_accepted_repository(prior_repo)
    original=json.loads((value['state']/'accepted_checkpoint.json').read_bytes())['result']
    original.update(commit=prior,head_before=base,head_after=prior)
    checkpoint=ap.serialize_accepted_checkpoint(original,prior_repo)
    helpers.private(value['state']/'accepted_checkpoint.json',checkpoint)
    helpers.git(root,'commit','--allow-empty','-qm','LAB Root review before security merge')
    before=helpers.git(root,'rev-parse','HEAD')
    target=old_main
    for number in range(40):
        target=helpers.git(root,'commit-tree',tree,'-p',target,'-m','LAB security source '+str(number))
    helpers.git(root,'update-ref',MAIN,target)
    helpers.git(root,'update-ref',ORIGIN,target)
    helpers.git(root,'merge','--no-ff','--no-edit','-m','LAB ordinary reviewed two-parent security merge',target)
    merge=helpers.git(root,'rev-parse','HEAD')
    helpers.git(root,'commit','--allow-empty','-qm','LAB Root acceptance after security merge')
    head=helpers.git(root,'rev-parse','HEAD')
    assert helpers.git(root,'rev-list','--parents','-n','1',merge)==merge+' '+before+' '+target
    value.update(target=target,base=base)
    monkeypatch.setattr(subject,'SECURITY_MERGE_SOURCE_BASE',base)
    rows=[]
    for index,(commit,parent)in enumerate(((before,prior),(merge,before),(head,merge))):
        path='.local/two-site-refactor/reviews/LAB_SECURITY_ROOT_'+str(index)+'.json'
        evidence=helpers.canonical(dict(LAB=True,head=commit,parent=parent,
            not_live_Root_acceptance=True))
        helpers.private(root/path,evidence)
        rows.append(dict(head=commit,parent=parent,evidence_path=path,evidence_sha256=helpers.sha(evidence)))
    bare={k:v for k,v in ap.validate_accepted_repository(value['repository_oracle'](root)).items()if k!='assignment_scope'}
    assert bare['control_blobs']==value['prior']['control_blobs']
    record=copy.deepcopy(value['record'])
    record.update(schema_version='3.0',kind=subject.KIND_V3,prior_checkpoint_sha256=helpers.sha(checkpoint),
        prior_head=prior,repository=bare,commits=rows,security_merge_transition=dict(old_main=old_main,
            old_origin_main=old_origin,security_commit=target,source_merge_base=base,merge_commit=merge,
            main_history=_history(root,old_main,target),origin_history=_history(root,old_origin,target)))
    value['v3_record']=record
    _publish(value,record)
    return value

def test_closed_production_base_and_unchanged_caps():
    assert subject.SECURITY_MERGE_SOURCE_BASE=='703601c8e5d0ecb22b726b031f86794fb8a0e7d9'
    assert subject.SECURITY_HISTORY_LIMIT==gitproof.DEFAULT_GIT_OUTPUT_LIMIT==65536
    assert subject.MAX_RECORD_BYTES==32768 and subject.MAX_COMMITS==32768//246

def test_actual_divergent_40plus_history_two_parent_merge_and_detached_custody(lab):
    record=lab['v3_record'];transition=record['security_merge_transition']
    assert transition['main_history']['commit_count']==40
    assert transition['origin_history']['commit_count']==41
    assert transition['old_main']!=transition['old_origin_main']
    assert _pure(lab,helpers.canonical(record))==record
    before=helpers.custody(lab)
    expected={**copy.deepcopy(record),'new_repository':ap.validate_accepted_repository(lab['repository'])}
    got=helpers.run(lab)
    assert got==expected and helpers.custody(lab)==before
    got['security_merge_transition']['main_history'].clear();got['commits'].clear()
    assert helpers.run(lab)==expected and helpers.custody(lab)==before

def _actual_used_proof_witness(lab):
    good=copy.deepcopy(lab['v3_record']);_publish(lab,good)
    assert helpers.run(lab)['security_merge_transition']==good['security_merge_transition']
    bad=copy.deepcopy(good);bad['security_merge_transition']['main_history']['sha256']='0'*64
    try:
        raw=_publish(lab,bad);assert _pure(lab,raw)==bad
        with pytest.raises(subject.RootMaintenanceBridgeError,match=ERROR):helpers.run(lab)
    finally:_publish(lab,good)
    assert helpers.run(lab)['security_merge_transition']==good['security_merge_transition']

def test_same_unconditional_actual_witness_fails_if_used_proof_is_omitted_then_restores(lab,monkeypatch):
    _actual_used_proof_witness(lab)
    actual=subject._verify_security_merge_git
    try:
        monkeypatch.setattr(subject,'_verify_security_merge_git',lambda root,record:None)
        with pytest.raises(pytest.fail.Exception):_actual_used_proof_witness(lab)
    finally:monkeypatch.setattr(subject,'_verify_security_merge_git',actual)
    _actual_used_proof_witness(lab)

@pytest.mark.parametrize('fault',['old_main','old_origin','marker_target','unequal_refs','candidate','control',
    'base','missing_merge','extra_transition_key','history_bool','history_over_cap','extra_record_key'])
def test_closed_v3_structure_and_independent_scope_bindings_reject(lab,fault):
    record=copy.deepcopy(lab['v3_record']);t=record['security_merge_transition'];marker_edit=None
    if fault=='old_main':t['old_main']=t['old_origin_main']
    elif fault=='old_origin':t['old_origin_main']=t['old_main']
    elif fault=='marker_target':marker_edit=lambda m:m.update(security_commit=t['old_main'])
    elif fault=='unequal_refs':record['repository']['protected_refs'][ORIGIN]=t['old_origin_main']
    elif fault=='candidate':record['repository']['protected_refs'][CANDIDATE]=lab['target']
    elif fault=='control':record['repository']['control_blobs'][ap.CONTROL_PATHS[0].as_posix()]='0'*40
    elif fault=='base':t['source_merge_base']=record['prior_head']
    elif fault=='missing_merge':t['merge_commit']=lab['target']
    elif fault=='extra_transition_key':t['allow_any_merge']=True
    elif fault=='history_bool':t['main_history']['commit_count']=True
    elif fault=='history_over_cap':t['main_history']['utf8_bytes']=65537
    elif fault=='extra_record_key':record['override']=True
    raw=_publish(lab,record,marker_edit=marker_edit)
    with pytest.raises(subject.RootMaintenanceBridgeError,match=ERROR):_pure(lab,raw)

@pytest.mark.parametrize('fault',['history_digest','history_count','history_size','first_parent_rows',
    'nonmerge','swapped_parents','extra_parent','additional_merge','missing_object','noncommit','unrelated'])
def test_real_git_refutes_complete_history_and_ordinary_merge_claims(lab,fault):
    record=copy.deepcopy(lab['v3_record']);t=record['security_merge_transition'];root=lab['root']
    if fault.startswith('history_'):
        key={'history_digest':'sha256','history_count':'commit_count','history_size':'utf8_bytes'}[fault]
        t['main_history'][key]='0'*64 if key=='sha256'else t['main_history'][key]-1
    elif fault=='first_parent_rows':
        record['commits'][0]['head']='0'*40;record['commits'][1]['parent']='0'*40
    elif fault=='missing_object':t['security_commit']='0'*40
    elif fault=='noncommit':t['security_commit']=helpers.git(root,'rev-parse',lab['target']+'^{tree}')
    elif fault=='unrelated':t['old_origin_main']=helpers.git(root,'commit-tree',
        helpers.git(root,'rev-parse',lab['target']+'^{tree}'),'-m','LAB unrelated endpoint')
    else:
        merge=record['commits'][1];tree=helpers.git(root,'rev-parse',merge['head']+'^{tree}')
        parent=merge['parent'];args=['commit-tree',tree]
        parents=([parent]if fault=='nonmerge'else [lab['target'],parent]if fault=='swapped_parents'else
                 [parent,lab['target'],t['old_origin_main']])
        if fault=='additional_merge':
            # Keep the declared merge healthy; make the final claimed linear row a real extra merge.
            last=record['commits'][-1]
            changed=helpers.git(root,'commit-tree',tree,'-p',last['parent'],'-p',t['old_origin_main'],'-m','LAB extra merge')
            helpers.git(root,'reset','--hard',changed)
            last['head']=changed;record['repository']['head']=changed
        else:
            for item in parents:args+=['-p',item]
            changed=helpers.git(root,*args,'-m','LAB wrong selected merge parents')
            tail=helpers.git(root,'commit-tree',tree,'-p',changed,'-m','LAB tail')
            helpers.git(root,'reset','--hard',tail)
            merge['head']=changed;t['merge_commit']=changed
            record['commits'][-1].update(head=tail,parent=changed);record['repository']['head']=tail
    if fault in ('missing_object','noncommit','unrelated'):
        # Exercise actual object/FF gates directly, not a parser-only mismatch.
        with pytest.raises(subject.RootMaintenanceBridgeError,match=ERROR):subject._verify_security_merge_git(root,record)
    else:
        raw=_publish(lab,record);assert _pure(lab,raw)==record
        helpers.reject_read(lab)

@pytest.mark.parametrize('fault',['ref','scope','checkpoint','stop','pending','broker'])
def test_v3_late_broker_boundary_drift_rejects(lab,monkeypatch,fault):
    actual=subject.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def disrupted(root):
        with actual(root):yield
        if fault=='ref':helpers.git(root,'update-ref',ORIGIN,lab['v3_record']['security_merge_transition']['old_origin_main'])
        elif fault=='scope':lab['active_path'].write_bytes(lab['active_path'].read_bytes()+b'\nLAB late scope\n')
        elif fault=='checkpoint':
            p=lab['state']/'accepted_checkpoint.json';p.write_bytes(p.read_bytes()+b' ')
        elif fault=='stop':(lab['state']/'STOP').write_bytes(b'')
        elif fault=='pending':helpers.private(lab['state']/'runs/cycle-0001-20261102T000000Z-1/quota-resume-claim.json',b'{}\n')
        else:raise OSError('LAB actual Broker posthold failure')
    monkeypatch.setattr(subject,'hold_idle_broker_client_lock_for_cold_adoption',disrupted)
    with pytest.raises(subject.RootMaintenanceBridgeError,match=ERROR):helpers.run(lab)

@pytest.mark.parametrize('fault',['posthold','wrong_marker','missing_marker_key','late_ref','late_pending','late_broker'])
def test_v3_shared_startup_and_monitor_never_fallback(lab,monkeypatch,fault):
    calls=[]
    def forbidden(*a,**k):calls.append(a);raise AssertionError('V3 cannot fall back')
    monkeypatch.setattr(startup,'read_maintenance_transition_locked',forbidden)
    monkeypatch.setattr(quarantine,'read_quarantine_recovery_locked',forbidden)
    for token in ('1','2'):(lab['state']/('rescope-'+token*64)).mkdir(mode=0o700)
    with ap.exclusive_supervisor_lock(lab['state']/'autopilot.lock'):
        startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
        ap.validate_rescope_epoch_startup(lab['state'],lab['repository'],root=lab['root'])
    from scripts import two_site_autopilot_monitor_preflight as monitor
    monkeypatch.setattr(monitor,'validate_repository',lab['repository_oracle'])
    helpers.private(lab['state']/'status.json',ap.serialize_json(
        dict(head=lab['marker']['head'],state='RETRYABLE_FAILURE')))
    assert monitor.run_preflight(lab['root'])==(True,'READY')
    if fault=='posthold':_publish(lab,lab['v3_record'],post_edit=lambda p:p.update(broker_posthold_completed=False))
    elif fault=='wrong_marker':_publish(lab,lab['v3_record'],marker_edit=lambda m:m.update(security_commit='0'*40))
    elif fault=='missing_marker_key':_publish(lab,lab['v3_record'],marker_edit=lambda m:m.pop('security_commit'))
    else:
        actual=subject.hold_idle_broker_client_lock_for_cold_adoption
        @contextmanager
        def disrupted(root):
            with actual(root):yield
            if fault=='late_broker':raise OSError('LAB failed posthold')
            if fault=='late_pending':helpers.private(lab['state']/'runs/cycle-0001-20261102T000000Z-1/quota-resume-claim.json',b'{}\n')
            else:helpers.git(root,'update-ref',ORIGIN,lab['v3_record']['security_merge_transition']['old_origin_main'])
        monkeypatch.setattr(subject,'hold_idle_broker_client_lock_for_cold_adoption',disrupted)
    with ap.exclusive_supervisor_lock(lab['state']/'autopilot.lock'):
        with pytest.raises(startup.MaintenanceTransitionStartupError):
            startup.require_reviewed_maintenance_transition_locked(lab['root'],lab['state'],lab['repository'])
        with pytest.raises(ap.AutopilotError):
            ap.validate_rescope_epoch_startup(lab['state'],lab['repository'],root=lab['root'])
    assert monitor.run_preflight(lab['root'])==(False,'STALE_CHECKPOINT')
    assert calls==[]

def test_v3_fixed_executor_budget_and_complete_history_output(lab,monkeypatch):
    actual=subject._run_git_query;seen=[]
    def observed(root,args,**kwargs):
        seen.append((list(args),kwargs['output_limit']))
        assert 0<kwargs['output_limit']<=65536
        return actual(root,args,**kwargs)
    monkeypatch.setattr(subject,'_run_git_query',observed)
    helpers.run(lab)
    histories=[(a,n)for a,n in seen if a[:3]==['rev-list','--reverse','--topo-order']]
    assert len(histories)==6 and all(n==65536 for _,n in histories)
    assert sum(a[:3]==['rev-list','--first-parent','--reverse']for a,_ in seen)==4
