"""Owned LAB fixtures only; no collected tests, publisher or live authority."""
from __future__ import annotations
import os
os.environ['APP_ENV_FILE'] = 'config/unit-test.env.example'
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import copy
import hashlib
import json
import subprocess
import time

import pytest
from scripts import two_site_root_maintenance_bridge as subject
from scripts import two_site_maintenance_transition_startup as startup
from scripts import two_site_maintenance_transition_storage as storage
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_quarantine_recovery_storage as quarantine
from scripts.two_site_assignment_scope import load_active_assignment_scope, parse_assignment_scope_bytes

DESTINATION = 'WA-LAB-ROOT-BRIDGE-DESTINATION'
HANDOFF = 'WA-LAB-ROOT-BRIDGE-HANDOFF'


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(root), *args],
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1',
             'GIT_NO_LAZY_FETCH':'1','GIT_TERMINAL_PROMPT':'0'}, stderr=subprocess.DEVNULL, timeout=20).decode().strip()


def private(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(raw); path.chmod(0o600)


def document(identifier, *, marker=None, expires='2099-01-01T00:00:00Z'):
    fields = dict(assignment_id=identifier, stage_id='P1-00b', status='READY', role='WORKER',
        integration_branch=ap.BRANCH, external_access='NONE', expires_at=expires,
        allowed_paths=['docs/example.md'], forbidden_paths=['api/**'])
    raw = b'# LAB only, not an accepted Root operation\n\n```yaml\n' + canonical(fields) + b'```\n'
    if marker is not None:
        raw += b'\nROOT_MAINTENANCE_BRIDGE_V1: ' + canonical(marker)
    return raw


def build_lab(tmp_path, monkeypatch):
    root = tmp_path / 'owned-repo'; root.mkdir(mode=0o700)
    git(root, 'init', '-b', ap.BRANCH)
    git(root, 'config', 'user.name', 'LAB'); git(root, 'config', 'user.email', 'lab@example.invalid')
    (root / '.gitignore').write_text('.local/\n')
    for path in ap.CONTROL_PATHS:
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('LAB tracked control ' + path.as_posix() + '\n')
    order = root / 'docs/refactor/two-server/execution-order.yaml'
    order.parent.mkdir(parents=True, exist_ok=True); order.write_text('stages:\n  P1-00b:\n    state: IN_PROGRESS\n')
    git(root, 'add', '.'); git(root, 'commit', '-qm', 'LAB prior checkpoint')
    prior_head = git(root, 'rev-parse', 'HEAD')
    for ref in ap.PROTECTED_REFS: git(root, 'update-ref', ref, prior_head)
    state = root / subject.STATE
    state.mkdir(parents=True, mode=0o700)
    for directory in (root/'.local', root/'.local/two-site-refactor', state): directory.chmod(0o700)
    (state/'runs').mkdir(mode=0o700)
    broker = root/'.local/two-site-refactor/provider-broker'; broker.mkdir(mode=0o700)
    for name in ('requests','claimed'): (broker/name).mkdir(mode=0o700)
    private(broker/'client.lock', b'')
    private(broker/'status.json', ap.serialize_json(dict(schema_version='1.0',state='IDLE',pid=12345,
        started_at=time.time()-100,updated_at=time.time())))
    assignments = root/'.local/two-site-refactor/assignments'; assignments.mkdir(mode=0o700)
    old_raw = document(HANDOFF, expires='2001-01-01T00:00:00Z')
    old_scope = parse_assignment_scope_bytes(old_raw, expected_assignment_id=HANDOFF, integration_branch=ap.BRANCH)
    old_path = assignments/(HANDOFF+'.md'); old_path.write_bytes(old_raw); old_path.chmod(0o644)

    def facts(scope):
        return dict(head=git(root,'rev-parse','HEAD'),status=git(root,'status','--porcelain=v1','--untracked-files=all'),
            protected_refs={ref:git(root,'rev-parse',ref) for ref in ap.PROTECTED_REFS},
            control_blobs={p.as_posix():git(root,'rev-parse','HEAD:'+p.as_posix()) for p in ap.CONTROL_PATHS},
            assignment_scope=scope)

    prior = facts(old_scope)
    receipt = dict(schema_version='1.0',state='CONTINUE',disposition='NONE',category='NONE',
        summary='LAB checkpoint fixture, never a live accepted cycle',commit=prior_head,next_action='LAB only',
        blockers=[],gemini_worker_run_id='lab-initial-worker-0',gemini_reviewer_run_id='lab-initial-reviewer-0',
        proposal_sha256='0'*64,head_before='0'*40,head_after=prior_head,head_changed=True)
    checkpoint = ap.serialize_accepted_checkpoint(receipt, prior)
    private(state/'accepted_checkpoint.json', checkpoint)
    changed = root/ap.CONTROL_PATHS[0]; changed.write_text('LAB changed control\n')
    git(root,'add','--',ap.CONTROL_PATHS[0].as_posix()); git(root,'commit','-qm','LAB Root code provenance, not Worker acceptance')
    head = git(root,'rev-parse','HEAD')
    evidence_path = '.local/two-site-refactor/reviews/LAB_EXISTING_CODE_PROVENANCE.json'
    evidence = b'{"LAB":"code provenance only; no historical POST or Worker invented"}\n'
    private(root/evidence_path,evidence)
    audit = canonical(dict(LAB=True,head=head,parent=prior_head,not_startup_authority=True))
    directory = state/subject.DIRECTORY; directory.mkdir(mode=0o700)
    private(directory/(sha(audit)+'.json'),audit)
    record = dict(schema_version='1.0',kind='ROOT_MAINTENANCE_BRIDGE_V1',
        prior_checkpoint_sha256=sha(checkpoint),prior_head=prior_head,destination_assignment_id=DESTINATION,
        repository={k:v for k,v in ap.validate_accepted_repository(facts(old_scope)).items() if k!='assignment_scope'},
        commits=[dict(head=head,parent=prior_head,evidence_path=evidence_path,evidence_sha256=sha(evidence))],
        history_audit_sha256=sha(audit),handoff_assignment_id=HANDOFF,handoff_scope_sha256=old_scope.sha256)
    record_raw = canonical(record)
    post = dict(schema_version='1.0',kind='ROOT_CURRENT_SOURCE_MAINTENANCE_HANDOFF_NORMAL_POSTHOLD_V1',
        record_sha256=sha(record_raw),prior_checkpoint_sha256=sha(checkpoint),head=head,
        destination_assignment_id=DESTINATION,handoff_assignment_id=HANDOFF,handoff_scope_sha256=old_scope.sha256,
        broker_posthold_completed=True,operation_error_type=None,checkpoint_changed=False,
        reservations_changed=False,Supervisor_started=False)
    post_raw=canonical(post)
    marker=dict(record_sha256=sha(record_raw),posthold_sha256=sha(post_raw),head=head,checkpoint_sha256=sha(checkpoint))
    private(directory/(sha(record_raw)+'.json'),record_raw); private(directory/(sha(post_raw)+'.json'),post_raw)
    active_path=assignments/(DESTINATION+'.md');active_path.write_bytes(document(DESTINATION,marker=marker));active_path.chmod(0o644)
    registry=root/'.local/two-site-refactor/worktrees.yaml'
    registry.write_text('integration:\n  path: '+str(root)+'\n  branch: '+ap.BRANCH+'\n  active_assignment_id: '+DESTINATION+'\n')
    registry.chmod(0o644)
    def repository_oracle(root_arg, require_clean=True):
        assert root_arg==root and require_clean
        result=facts(load_active_assignment_scope(root))
        assert result['status']==''
        return result
    monkeypatch.setattr(subject,'validate_repository',repository_oracle)
    monkeypatch.setattr(storage,'validate_repository',repository_oracle)
    monkeypatch.setattr(startup,'validate_repository',repository_oracle)
    return dict(root=root,state=state,record=record,post=post,marker=marker,
        prior=ap.validate_accepted_repository(prior),repository=repository_oracle(root),directory=directory,
        active_path=active_path,old_path=old_path,registry=registry,
        evidence_path=root/evidence_path,audit=audit,repository_oracle=repository_oracle)


def run(lab):
    with ap.exclusive_supervisor_lock(lab['state']/'autopilot.lock'):
        return subject.read_root_maintenance_bridge_locked(lab['root'],lab['state'],lab['repository'])


def custody(lab):
    root=lab['root']
    return (git(root,'show-ref'),git(root,'status','--porcelain=v1','--untracked-files=all'),
        (lab['state']/'accepted_checkpoint.json').read_bytes(),lab['active_path'].read_bytes(),
        lab['old_path'].read_bytes(),lab['registry'].read_bytes(),
        tuple((p.name,p.read_bytes()) for p in sorted(lab['directory'].iterdir())),lab['evidence_path'].read_bytes())


def reject_read(lab, *, expect_post=True):
    before=custody(lab); events=[]
    actual=subject.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def observed(root):
        with actual(root):
            events.append('entered')
            yield
            events.append('body-returned')
        events.append('actual-posthold')
    try:
        subject.hold_idle_broker_client_lock_for_cold_adoption=observed
        with pytest.raises(subject.RootMaintenanceBridgeError,match='^root maintenance bridge verification failed$'):run(lab)
    finally:
        subject.hold_idle_broker_client_lock_for_cold_adoption=actual
    assert events==(['entered','body-returned','actual-posthold'] if expect_post else [])
    assert subject.hold_idle_broker_client_lock_for_cold_adoption is actual
    assert custody(lab)==before


def replace_bundle(lab, *, record_edit=None, post_edit=None):
    record=copy.deepcopy(lab['record']);post=copy.deepcopy(lab['post'])
    if record_edit: record_edit(record)
    raw=canonical(record);post['record_sha256']=sha(raw)
    if post_edit:post_edit(post)
    post_raw=canonical(post)
    marker={**lab['marker'],'record_sha256':sha(raw),'posthold_sha256':sha(post_raw)}
    private(lab['directory']/(sha(raw)+'.json'),raw);private(lab['directory']/(sha(post_raw)+'.json'),post_raw)
    lab['active_path'].write_bytes(document(DESTINATION,marker=marker))
    lab['repository']=lab['repository_oracle'](lab['root'])
    return raw,marker
