"""Finite pipeline branch integration; fake transport only, no model/auth claim."""
from pathlib import Path
import pytest
from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_maintenance_quota as quota


@pytest.mark.parametrize('due',[False,True])
def test_same_job_quota_branch_waits_or_consumes_only_existing_next_reviewer_slot(tmp_path,monkeypatch,due):
    prefix='maint-'+('a'*32);base=prefix+'-reviewer-1'
    retained=tmp_path/'.local/two-site-refactor/proposals'/base;retained.mkdir(parents=True)
    task={'task_id':'same-prepared-job'};sources={'scripts/example.py':{'sha256':'c'*64,'content':'original'}}
    auth={'kind':'QODER_AUTOMATION_MAINTENANCE_V1','job_id':prefix,'sha256':'d'*64}
    evidence={'kind':'QUOTA_RESET_KNOWN','provider':'antigravity','model':'configured-fixture',
        'conversation_id':'retained-reviewer','reset_at_utc':'2026-11-03T00:00:00Z'}
    attempt={'role':'verifier','run_id':base,'status_sha256':'b'*64}
    monkeypatch.setattr(pipeline,'load_task',lambda root,path,**kw:(task,sources))
    monkeypatch.setattr(pipeline,'_extract_bound_quota_evidence',lambda *args:(evidence,attempt))
    authorized=[];generated=[];checked=[]
    def bound(root,pointer,**kwargs):
        assert root==tmp_path and pointer==auth
        assert kwargs=={'task':task,'sources':sources,'expected_head':'f'*40,'base_run_id':base,'attempt_id':base,
            'attempts':(base,base+'-retry-1',base+'-retry-2'),'quota_evidence':evidence,'quota_attempt':attempt}
        authorized.append(kwargs);return due
    monkeypatch.setattr(quota,'maintenance_quota_resume_due',bound)
    original_worker=tmp_path/'original-author/result.json';native=tmp_path/'same-native.json'
    def generate(root,path,role,run_id,timeout,**kwargs):
        generated.append(run_id)
        assert role=='verifier' and run_id==base+'-retry-1'
        assert kwargs['maintenance_authorization']==auth and kwargs['proposal']==original_worker and kwargs['test_evidence']==native
        directory=retained.parent/run_id;directory.mkdir();return directory
    monkeypatch.setattr(pipeline,'runner_run',generate)
    def no_author(*args,**kwargs):raise AssertionError('quota must never replay author')
    args=(tmp_path,tmp_path/'task.json','verifier',base,30,lambda:checked.append(True))
    if due:
        result=pipeline.run_provider_session(*args,proposal=original_worker,test_evidence=native,
            expected_head='f'*40,maintenance_authorization=auth,author_fallback_hook=no_author)
        assert result==(base+'-retry-1',retained.parent/(base+'-retry-1')) and generated==[base+'-retry-1']
        assert len(checked)==2
    else:
        with pytest.raises(pipeline.QuotaBlockError) as error:
            pipeline.run_provider_session(*args,proposal=original_worker,test_evidence=native,
                expected_head='f'*40,maintenance_authorization=auth,author_fallback_hook=no_author)
        assert error.value.quota_evidence==evidence and error.value.quota_attempt==attempt and not generated
    assert len(authorized)==1 and not (retained.parent/(base+'-retry-2')).exists()


def test_normal_existing_reviewer_id_does_not_gain_maintenance_quota_authority(tmp_path,monkeypatch):
    directory=tmp_path/'.local/two-site-refactor/proposals/ordinary-reviewer-0';directory.mkdir(parents=True)
    def forbidden(*args,**kwargs):raise AssertionError('ordinary quota route must remain unchanged')
    monkeypatch.setattr(quota,'maintenance_quota_resume_due',forbidden)
    with pytest.raises(pipeline.ProposalError,match='already exists'):
        pipeline.run_provider_session(tmp_path,tmp_path/'task.json','verifier',directory.name,30,
            lambda:None,expected_head='f'*40)
