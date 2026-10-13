"""Whole-report oracles, not behavior or savings authority."""
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import ast
import copy
import hashlib
import itertools
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import two_site_work_cost_report as m
from scripts import two_site_cycle_metrics as metrics
from tests import test_two_site_work_cost_witness_helpers as h
from tests import test_two_site_work_cost_retained_call_frame as retained

GAP="docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md#6-verification-evidence-administrative-diagnostics-open-consumer-roadmap"
SCENARIO="AE00-source-revision-cost-conservation"
OBS="OBSERVED_CONTROLLER_ATTEMPTS"
API="summarize_work_lineage_ledger"
C_KEYS=("input_tokens","cached_input_tokens","uncached_input_tokens","output_tokens")
P_KEYS=("input_tokens","output_tokens","total_tokens")

def semantic(fp="b",source="c",scenario=SCENARIO):
    return dict(stage_id="P1-00b",gap_reference=GAP,scenario_key=scenario,
                source_sha256=source*64,fingerprint=fp*64)

def row(rid="sample-a",work="a",attempt="b",source="c",state="CONTINUE",
        codex=(100,80,20,10),provider=(50,20,70),pair=(2,2),seconds=10,mechanical=True):
    sem=semantic(attempt,source);w=dict(schema_version=1,fingerprint=work*64)
    pm=dict(state=OBS,semantic_identity=copy.deepcopy(sem),work_lineage=copy.deepcopy(w))
    r=dict(run_id=rid,model="gpt-6.1-sol",effort="xhigh",state=state,
           semantic_identity=sem,work_lineage=w,provider_metrics=pm)
    if mechanical:r.update(head_before="1"*40,head_after="2"*40,
        gemini_worker_run_id="sample-worker-0",gemini_reviewer_run_id="sample-reviewer-0",proposal_sha256="f"*64)
    if codex is not None:r.update(usage_state="OBSERVED_COMPLETED_TURNS",**dict(zip(C_KEYS,codex)))
    if provider is not None:pm.update(usage=dict(zip(P_KEYS,provider)),observed_usage_subtotal=dict(zip(P_KEYS,provider)))
    if pair is not None:pm.update(attempts=pair[0],usage_observed_attempts=pair[1])
    if seconds is not None:r.update(finished_at=float(seconds),duration_seconds=seconds)
    return r

def costs(c=(0,0,(0,0,0,0),False),p=(0,0,(0,0,0),False,0,0,0,0),t=(0,0,0.0,False)):
    """Expected tuples, no Subject data."""
    co,cu,ct,cf=c;po,pu,pt,pf,cov,uncov,reported,observed=p;to,tu,total,tf=t
    return dict(codex=dict(observed_record_count=co,unknown_record_count=cu,
        observed_subtotal=dict(zip(C_KEYS,ct)),full_usage=dict(zip(C_KEYS,ct)) if cf else None),
        provider=dict(observed_record_count=po,unknown_record_count=pu,
        observed_subtotal=dict(zip(P_KEYS,pt)),full_usage=dict(zip(P_KEYS,pt)) if pf else None,
        attempt_coverage_record_count=cov,unknown_attempt_coverage_record_count=uncov,
        reported_attempt_count=reported,observed_attempt_count=observed,unknown_attempt_count=reported-observed),
        reported_cycle_seconds=dict(observed_record_count=to,unknown_record_count=tu,
        observed_subtotal=float(total),full_total=float(total) if tf else None))

def lineage(work,attempts,revisions,count,candidates,cost):
    return dict(work_fingerprint=work*64,semantic_attempt_count=attempts,source_revision_count=revisions,
                record_count=count,observed_candidate_count=candidates,costs=copy.deepcopy(cost))

def report(n,distinct,cost=None,lines=(),attempts=0,candidates=0,duplicates=0,runs=0,conflicts=0,
           unattributed=(0,0,0),unknown_cost=None):
    u,conflicted,malformed=unattributed
    return dict(schema_version=1,state="OBSERVATIONAL_ONLY",input_record_count=n,distinct_record_count=distinct,
        duplicate_record_count=duplicates,run_conflict_count=runs,identity_conflict_work_count=conflicts,
        logical_work_count=len(lines),semantic_attempt_count=attempts,
        observed_acceptance_candidate_lineage_count=candidates,costs=copy.deepcopy(cost if cost is not None else costs()),
        lineages=copy.deepcopy(list(lines)),unattributed=dict(record_count=u,conflict_record_count=conflicted,
        malformed_record_count=malformed,allocation_rule="UNATTRIBUTED_NOT_ALLOCATED",
        costs=copy.deepcopy(unknown_cost if unknown_cost is not None else costs())),
        accepted_behavior_count=None,denominator_state="UNVERIFIED",token_per_accepted_behavior=None,
        billing_savings=None,value_state="NOT_ESTABLISHED",cohort_comparability="NOT_ESTABLISHED")

def known(n,seconds,provider_unknown=0):
    """Independent fixture totals."""
    p=n-provider_unknown
    return costs((n,0,(100*n,80*n,20*n,10*n),True),
                 (p,provider_unknown,(50*p,20*p,70*p),not provider_unknown,p,provider_unknown,2*p,2*p),
                 (n,0,seconds,True))

SINGLE=known(1,10.0)
GOLD_COST=costs((2,1,(300,200,100,30),False),(2,1,(150,60,210),False,2,1,4,4),(2,1,30.0,False))

def golden():
    a=row();a["reasoning_tokens"]=2
    b=row("sample-b",attempt="d",source="e",state="BLOCKED",codex=(200,120,80,20),provider=(100,40,140),seconds=20)
    c=row("sample-c",attempt="d",source="e",state="STOP",codex=None,provider=None,pair=None,seconds=None,mechanical=False)
    return [a,b,c],report(3,3,GOLD_COST,[lineage("a",2,2,3,1,GOLD_COST)],attempts=2,candidates=1)

def preflight(records):
    for r in records:
        if type(r) is dict and type(r.get("semantic_identity")) is dict:
            assert metrics.semantic_identity(r["semantic_identity"])==r["semantic_identity"]
            assert metrics.work_lineage(r["work_lineage"])==r["work_lineage"]

def witness(records,expected):
    frame=retained.RetainedCallFrame([records,expected],lambda:preflight(records))
    try:
        with h.armed_hooks():
            result=frame.call(m,API,(records,),expected)
            assert h.get_total_hook_calls()==0
        return result
    finally:frame.assert_all_custody()

def unchanged(snapshots):
    for value,snapshot in snapshots:h.assert_custody_unchanged(value,snapshot)

def test_independent_full_golden():
    records,expected=golden()
    witness(records,expected)

def singleton():
    return [row()],report(1,1,SINGLE,[lineage("a",1,1,1,1,SINGLE)],attempts=1,candidates=1)

def duplicate_report(n):
    return report(n,1,SINGLE,[lineage("a",1,1,1,1,SINGLE)],attempts=1,candidates=1,duplicates=n-1)

def quarantine_report(n,cost,runs,works):
    return report(n,n,cost,runs=runs,conflicts=works,unattributed=(n,n,0),unknown_cost=cost)

def test_cardinality_and_dedup():
    one,expected=singleton();witness(one,expected)
    cases=[([],report(0,0))]
    duplicate=copy.deepcopy(one[0]);private=copy.deepcopy(one[0]);private["ignored_private"]={"sentinel":"PRIVATE-DO-NOT-REPORT"}
    cases += [([one[0],duplicate],duplicate_report(2)),
              ([one[0],private],duplicate_report(2))]
    twice=known(2,20.0)
    separate=[row(),row("different",work="1",attempt="2",source="3")]
    cases.append((separate,report(2,2,twice,[lineage("1",1,1,1,1,SINGLE),lineage("a",1,1,1,1,SINGLE)],attempts=2,candidates=2)))
    failed=[row(state="BLOCKED")]
    cases.append((failed,report(1,1,SINGLE,[lineage("a",1,1,1,0,SINGLE)],attempts=1)))
    doc=[row()];doc[0]["changed_paths"]=["docs/only.md"];doc[0]["accepted"]=True
    cases.append((doc,copy.deepcopy(expected)))
    for records,oracle in cases:
        witness(records,oracle);witness(one,expected)
    for bad_id in (None,"bad id",81*"x",False):
        records=[row(),row()]
        for r in records:
            if bad_id is None:r.pop("run_id")
            else:r["run_id"]=bad_id
        oracle=report(2,2,twice,[lineage("a",1,1,2,2,twice)],attempts=1,candidates=1)
        witness(records,oracle);witness(one,expected)

def test_provider_coverage_and_costs():
    one,good=singleton()
    # Prescribed tuples, not raw-row calculations.
    cases=[
        ("PARTIAL",(100,20,120),(10,4,14),(1,1),(1,0,(10,4,14),False,1,0,1,1),False),
        (OBS,(100,20,120),(10,4,14),(1,1),(1,0,(100,20,120),True,1,0,1,1),True),
        (OBS,(100,20,999),(10,4,14),(1,1),(1,0,(10,4,14),False,1,0,1,1),True),
        ("UNKNOWN",(50,20,70),(50,20,70),(2,2),(0,1,(0,0,0),False,0,1,0,0),False),
        (OBS,(0,0,0),(0,0,0),(1,0),(1,0,(0,0,0),False,1,0,1,0),True),
        (OBS,(0,0,0),(0,0,0),(0,0),(1,0,(0,0,0),True,1,0,0,0),True),
    ]
    for pair in ((True,1),(1.0,1),(33,1),(1,2),(1,0)):
        cases.append((OBS,(50,20,70),(50,20,70),pair,(1,0,(50,20,70),False,0,1,0,0),True))
    for state,usage,subtotal,pair,pcost,group in cases:
        r=row();r["provider_metrics"].update(state=state,usage=dict(zip(P_KEYS,usage)),
            observed_usage_subtotal=dict(zip(P_KEYS,subtotal)),attempts=pair[0],usage_observed_attempts=pair[1])
        c=known(1,10.0);c["provider"]=costs(p=pcost)["provider"]
        expected=report(1,1,c,[lineage("a",1,1,1,1,c)] if group else (),attempts=int(group),candidates=int(group),
            unattributed=(0,0,0) if group else (1,0,0),unknown_cost=None if group else c)
        witness([r],expected);witness(one,good)
    for dimension,bad in (("input_tokens",True),("input_tokens",1.0),("input_tokens",101),
                          ("duration_seconds",True),("duration_seconds",h.ForeignDuration(10)),
                          ("duration_seconds",float("nan")),("duration_seconds",float("inf")),("duration_seconds",10**12+1)):
        r=row();r[dimension]=bad
        c=known(1,10.0)
        if dimension=="input_tokens":c["codex"]=costs(c=(0,1,(0,0,0,0),False))["codex"]
        else:c["reported_cycle_seconds"]=costs(t=(0,1,0.0,False))["reported_cycle_seconds"]
        witness([r],report(1,1,c,[lineage("a",1,1,1,1,c)],attempts=1,candidates=1));witness(one,good)

def test_variants_and_full_graph_permutations():
    one,good=singleton()
    twice=known(2,20.0)
    for dimension,value in (("model","other-model"),("effort","high"),("finished_at",11.0),
                            ("state","BLOCKED"),("head_after","3"*40),("gemini_worker_run_id","different-worker-0")):
        r=row();r[dimension]=value
        expected=quarantine_report(2,twice,1,1)
        for ordered in itertools.permutations([row(),r]):witness(list(ordered),expected)
        witness(one,good)

    cost_variant=row();cost_variant["input_tokens"]=200;cost_variant["cached_input_tokens"]=180
    cost=costs((2,0,(300,260,40,20),True),(2,0,(100,40,140),True,2,0,4,4),(2,0,20.0,True))
    graphs=[]
    graphs.append(([row(),cost_variant],quarantine_report(2,cost,1,1)))
    nested=row();nested["provider_metrics"].pop("semantic_identity")
    graphs.append(([row(),nested],quarantine_report(2,twice,1,1)))
    metadata=row("meta",attempt="d",source="e")
    metadata["semantic_identity"]["scenario_key"]="different-scenario"
    metadata["provider_metrics"]["semantic_identity"]=copy.deepcopy(metadata["semantic_identity"])
    graphs.append(([row(),metadata],quarantine_report(2,twice,0,1)))
    graphs.append(([row(),row("cross",work="1")],quarantine_report(2,twice,0,2)))
    # Distinct-link chain a -> 1 -> 4, with a clean attached terminal row.
    for ineligible in (None,"OK","UNKNOWN"):
        a=row("seed",work="a",attempt="b")
        b=row("bridge1",work="a",attempt="d",source="e")
        b["semantic_identity"]["scenario_key"]="metadata-seed"
        b["provider_metrics"]["semantic_identity"]=copy.deepcopy(b["semantic_identity"])
        c=row("bridge1",work="1",attempt="2",source="3")
        d=row("bridge2",work="1",attempt="5",source="6")
        e=row("bridge2",work="4",attempt="7",source="8")
        f=row("clean",work="4",attempt="9",source="f")
        if ineligible:f["provider_metrics"]["state"]=ineligible
        cost=known(6,60.0,int(ineligible=="UNKNOWN"))
        graphs.append(([a,b,c,d,e,f],quarantine_report(6,cost,2,3)))
    for records,expected in graphs:
        preflight(records)
        for ordered in itertools.permutations(records):witness(list(ordered),expected)
        witness(one,good)

def test_bounds_and_foreign_privacy():
    sample=row();expected=duplicate_report(2000)
    records=[sample]*2000;original=records;references=tuple(records);values=copy.deepcopy(records)
    frame=retained.RetainedCallFrame([sample,expected],lambda:preflight([sample]))
    try:
        actual=m.summarize_work_lineage_ledger(records);h.assert_exact_typed(actual,expected)
    finally:
        assert records is original and len(records)==2000 and all(a is b for a,b in zip(records,references))
        assert records==values;frame.assert_all_custody()
    for invalid in (None,{},(),h.ForeignList(),records+[sample]):
        before=h.capture_custody(invalid) if type(invalid) is not list else None
        with patch.object(m,"normalize_work_cost_row",wraps=m.normalize_work_cost_row) as native:
            try:
                with h.armed_hooks(),pytest.raises(m.WorkCostReportError) as caught:m.summarize_work_lineage_ledger(invalid)
                assert str(caught.value)=="invalid work lineage ledger records" and h.get_total_hook_calls()==0
                native.assert_not_called()
            finally:
                if before is not None:h.assert_custody_unchanged(invalid,before)
                else:assert len(invalid)==2001 and all(r is sample for r in invalid)
                frame.assert_all_custody()
    unknown=costs((0,4,(0,0,0,0),False),(0,4,(0,0,0),False,0,4,0,0),(0,4,0.0,False))
    malformed=[h.ForeignDict(),h.ForeignOpaqueLeaf(),{h.ForeignStrKey("foreign"):h.ForeignOpaqueLeaf()},dict.fromkeys(range(65))]
    witness(malformed,report(4,4,unknown,unattributed=(4,0,4),unknown_cost=unknown))
    records,expected=golden();records[0]["private"]={"secret":"PRIVATE-REPORT-SENTINEL","opaque":h.ForeignOpaqueLeaf(),"type":h.ForeignClassAllHooks}
    value=witness(records,expected)
    text=json.dumps(value,sort_keys=True)
    assert "PRIVATE-REPORT-SENTINEL" not in text and "sample-worker" not in text and GAP not in text

def test_signed_zero_each_field_both_orders():
    zero=known(1,0.0)
    expected=report(2,1,zero,[lineage("a",1,1,1,1,zero)],attempts=1,candidates=1,duplicates=1)
    for field in ("finished_at","duration_seconds"):
        positive=row(seconds=0);negative=copy.deepcopy(positive);positive[field]=0.0;negative[field]=-0.0
        preflight([positive,negative])
        for ordered in itertools.permutations([negative,positive]):
            actual=witness(list(ordered),expected)
            assert actual["costs"]["reported_cycle_seconds"]["observed_subtotal"]==0.0
        records,gold=golden();witness(records,gold)

def paths(value,path=()):
    if type(value) is dict:
        yield path
        for key,child in value.items():yield from paths(child,path+(key,))
    elif type(value) is list:
        yield path
        for index,child in enumerate(value):yield from paths(child,path+(index,))

def at(value,path):
    for key in path:value=value[key]
    return value

def test_branch_detachment_two_returns():
    for records,expected in (golden(),singleton()):
        all_paths=list(paths(expected))
        for path in all_paths:
            frame=retained.RetainedCallFrame([records,expected],lambda:preflight(records))
            first=frame.call(m,API,(records,),expected)
            second=frame.call(m,API,(records,),expected)
            target=at(first,path)
            original=list(target.items()) if type(target) is dict else list(target)
            fixed=[(v,h.capture_custody(v)) for v in (records,expected,second)]
            siblings=[(at(first,p),h.capture_custody(at(first,p))) for p in all_paths
                      if p!=path and p[:len(path)]!=path and path[:len(p)]!=p]
            try:
                if type(target) is dict:target["mutation-only"]=999
                else:target.append({"mutation-only":999})
                unchanged(fixed+siblings)
            finally:
                target.clear()
                if type(target) is dict:target.update(original)
                else:target.extend(original)
                frame.assert_all_custody()
                unchanged(fixed+siblings)
            h.assert_exact_typed(first,expected)
            frame.call(m,API,(records,),expected)

def test_used_mutant_same_golden():
    records,expected=golden();frame=retained.RetainedCallFrame([records,expected],lambda:preflight(records))
    original=m.normalize_work_cost_row;used=[]
    def omit_group(value):
        result=original(value);used.append(result)
        if result is not None:result["group_eligible"]=False
        return result
    try:
        frame.call(m,API,(records,),expected)
        m.normalize_work_cost_row=omit_group
        assert m.normalize_work_cost_row is omit_group
        with pytest.raises(AssertionError):frame.call(m,API,(records,),expected)
        assert len(used)==3 and all(r["group_eligible"] is False for r in used)
        assert used[0]["codex_usage"]==dict(zip(C_KEYS,(100,80,20,10)))
    finally:
        m.normalize_work_cost_row=original
        assert m.normalize_work_cost_row is original
        frame.assert_all_custody()
        frame.call(m,API,(records,),expected)

def test_legacy_ast_and_fsum_rounding():
    tree=ast.parse(Path("scripts/two_site_cycle_metrics.py").read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="summarize_efficiency_ledger")
    assert hashlib.sha256(ast.dump(fn,include_attributes=False).encode()).hexdigest()=="6a1ec137f80e58182be2a136fb17bfc58235931d033a48d4bf4fc61636ccf977"
    records=[row("large",seconds=10**12),row("tiny-group",seconds=0.00004),row("tiny-ungrouped",seconds=0.00004)]
    records[2]["provider_metrics"]["state"]="OK"
    grouped=known(2,1000000000000.0)
    ungrouped=known(1,0.00004)
    total=known(3,1000000000000.0001)
    expected=report(3,3,total,[lineage("a",1,1,2,2,grouped)],attempts=1,candidates=1,unattributed=(1,0,0),unknown_cost=ungrouped)
    for ordered in itertools.permutations(records):witness(list(ordered),expected)
