"""Complete Unit C execution, helper contracts and custody proof."""
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
from unittest.mock import patch
import ast
import copy
import math
from pathlib import Path
import pytest
from scripts import two_site_work_cost_report as report_module
from scripts import two_site_work_cost_rows as row_module
from tests import test_two_site_work_cost_report as fx
from tests import test_two_site_work_cost_witness_helpers as h
from tests import test_two_site_work_cost_retained_call_frame as retained
from tests import test_two_site_work_cost_report_field_cases as case_data
from tests.test_two_site_work_cost_report_field_cases import (
    API,
    MAX,
    MISSING,
    SEMANTIC,
    WORK,
    NUMBER_TAG_RULES,
    healthy,
    clone,
    single_report,
    EXPECTED_PRODUCTS,
    EXPECTED,
    location,
    replace_field,
    reference_field,
    finish_case,
    scalar_case,
    identity_case,
    cost_report,
    token_case,
    map_value,
)

def other_case(product):
    good=[healthy()];fault=clone(good);reference=[healthy()];eg=single_report();recipe="known";group=True;duplicate=False
    kind,path=product.fault,product.paths[0]
    if product.family=="gate":
        changes={"COMPLETE":{"state":"COMPLETE"},"BLOCKED":{"state":"BLOCKED"},"STOP":{"state":"STOP"},
            "equal_heads":{"head_after":"1"*40},"worker_wrong":{"gemini_worker_run_id":"other-reviewer-1"},
            "reviewer_wrong":{"gemini_reviewer_run_id":"other-worker-1"},
            "swapped":{"gemini_worker_run_id":"sample-reviewer-0","gemini_reviewer_run_id":"sample-worker-0"},
            "identical":{"gemini_reviewer_run_id":"sample-worker-0"}}[kind]
        fault[0].update(changes);reference[0].update(changes)
        ef=single_report(candidate=kind=="COMPLETE")
        return finish_case(product,good,fault,reference,eg,ef)
    elif product.family=="coverage":
        pair,usage,subtotal,state,pcost={
            "both32":((32,32),(10,4,14),(10,4,14),fx.OBS,(1,0,(10,4,14),True,1,0,32,32)),
            "bothzero":((0,0),(0,0,0),(0,0,0),fx.OBS,(1,0,(0,0,0),True,1,0,0,0)),
            "observedzero":((1,0),(0,0,0),(0,0,0),fx.OBS,(1,0,(0,0,0),False,1,0,1,0)),
            "positive_zero":((1,0),(10,4,14),(10,4,14),fx.OBS,(1,0,(10,4,14),False,0,1,0,0)),
            "observed_exceeds":((1,2),(10,4,14),(10,4,14),fx.OBS,(1,0,(10,4,14),False,0,1,0,0)),
            "full_precedence":((1,1),(100,20,120),(10,4,14),fx.OBS,(1,0,(100,20,120),True,1,0,1,1)),
            "both_invalid":((1,1),(10,4,99),(10,4,99),fx.OBS,(0,1,(0,0,0),False,1,0,1,1)),
            "partial":((1,1),(100,20,120),(10,4,14),"PARTIAL",(1,0,(10,4,14),False,1,0,1,1)),
            "OK":((1,1),(10,4,14),(10,4,14),"OK",(1,0,(10,4,14),True,1,0,1,1)),
        }[kind]
        changes=dict(attempts=pair[0],usage_observed_attempts=pair[1],usage=dict(zip(fx.P_KEYS,usage)),
            observed_usage_subtotal=dict(zip(fx.P_KEYS,subtotal)),state=state)
        fault[0]["provider_metrics"].update(clone(changes));reference[0]["provider_metrics"].update(clone(changes))
        ef=cost_report(fx.costs((1,0,(100,80,20,10),True),pcost,(1,0,5.0,True)),group=kind not in ("partial","OK"))
        return finish_case(product,good,fault,reference,eg,ef)
    elif product.family=="count":
        value={"missing":MISSING,"null":None,"bool":True,"float":1.0,"negative":-1,"string":"1","subclass":h.ForeignFinished(1),"zero":0,"32":32,"33":33}[kind]
        recipe="counts_unknown"
        if kind=="32" and product.field=="attempts":
            recipe="provider_fallback"
        replace_field(fault[0],path,value);reference_field(reference[0],path,value)
    elif product.family=="state":
        value={"missing":MISSING,"null":None,"list":[],"dict":{},"subclass":h.ForeignStrKey(fx.OBS),"unknown":"UNKNOWN","OK":"OK","PARTIAL":"PARTIAL","matching":"OBSERVED_MATCHING_ATTEMPTS","valid":"OBSERVED_COMPLETED_TURNS"}[kind]
        recipe="codex_unknown" if product.layer=="root" and kind!="valid" else "known" if product.layer=="root" or kind in ("matching","OK") else "provider_partial" if kind=="PARTIAL" else "provider_unknown"
        group=kind!="OK";duplicate=product.layer=="root" and kind=="valid"
        replace_field(fault[0],path,value);reference_field(reference[0],path,value)
    elif product.family=="alias":
        leaf=h.ForeignOpaqueLeaf();value={"dict":{"PRIVATE-OPAQUE":leaf},"list":[leaf],"bomb":leaf}[kind]
        replace_field(fault[0],path,value);reference_field(reference[0],path,value);duplicate=True
    else:
        if product.layer=="subtotal":
            good[0]["provider_metrics"].pop("usage");fault=clone(good);reference=clone(good);eg=single_report("provider_fallback")
        old=location(good[0],path) if path else good[0]
        key="run_id" if product.layer=="root" else "state" if product.layer=="provider" else "input_tokens"
        value=map_value(old,kind,key)
        if not path:fault=[value];reference=[value]
        else:replace_field(fault[0],path,value);reference_field(reference[0],path,value)
        recipe="malformed" if product.layer=="root" and kind!="64" else "provider_unknown" if product.layer=="provider" and kind!="32" else "provider_fallback" if product.layer=="full" else "provider_no_cost" if product.layer=="subtotal" else "known"
        duplicate=kind in ("64","32")
    ef=single_report(recipe,group=group)
    if product.family=="count" and kind=="32" and product.field=="attempts":
        ef=cost_report(fx.costs((1,0,(100,80,20,10),True),(1,0,(10,4,14),False,1,0,32,1),(1,0,5.0,True)))
    return finish_case(product,good,fault,reference,eg,ef,duplicate)



def check_payload(case):
    """Independent payload grammar, not field_value or its reference snapshot."""
    p=case.product
    if p.family not in ("scalar","hex","number","token","count"):
        check_other_payload(case);return
    path=p.paths[0]
    if p.family=="token":path=path[:-1]+(p.field,)
    parent=location(case.fault[-1],path[:-1]);kind=p.fault
    if kind=="missing":assert path[-1] not in parent;return
    value=parent[path[-1]]
    if kind=="null":assert value is None;return
    if kind in ("subclass","foreign_int","foreign_float"):
        expected=h.ForeignDuration if kind=="foreign_float" else h.ForeignStrKey if p.family in ("scalar","hex") else h.ForeignFinished
        assert type(value) is expected;return
    if p.family=="scalar":
        if kind=="integer":assert type(value) is int and value==3;return
        assert type(value) is str
        if kind=="valid80":assert len(value)==80 and all(c.isascii() and (c.isalnum() or c in "._-") for c in value)
        else:assert value=={"empty":"","space":"bad id","newline":"x\n","long":"a"*81}[kind]
    elif p.family=="hex":
        if kind=="integer":assert type(value) is int and value==2;return
        width=64 if p.field=="proposal_sha256" else 40
        assert type(value) is str and value=={"empty":"","short":"a"*(width-1),"long":"a"*(width+1),
            "uppercase":"A"*width,"nonhex":"x"*width,"newline":"a"*(width-1)+"\n","valid":"3"*width}[kind]
    elif p.family=="number":
        if kind.startswith("ascii"):
            assert type(value) is str and len(value)==int(kind[5:]) and all(32<=ord(c)<=126 for c in value);return
        if kind in ("empty_ascii","nonascii","control","numeric_string"):
            assert type(value) is str and value=={"empty_ascii":"","nonascii":"é","control":"a\x1f","numeric_string":"5"}[kind];return
        if kind in ("nan","positive_inf","negative_inf"):
            assert type(value) is float
            assert math.isnan(value) if kind=="nan" else value==(math.inf if kind=="positive_inf" else -math.inf);return
        typ,number=NUMBER_TAG_RULES[kind]
        assert type(value) is typ and value==number
    else:
        if kind in ("bool","float","negative","string","overflow","zero","32","33","MAX"):
            typ,number={"bool":(bool,True),"float":(float,1.0),"negative":(int,-1),"string":(str,"1"),
                "overflow":(int,1000000000001),"zero":(int,0),"32":(int,32),"33":(int,33),"MAX":(int,MAX)}[kind]
            assert type(value) is typ and value==number
        elif kind=="arithmetic":
            assert type(value) is int
            vector=location(case.fault[-1],path[:-1])
            if p.layer=="codex":
                if p.field=="output_tokens":assert value==11
                else:assert vector["input_tokens"]!=vector["cached_input_tokens"]+vector["uncached_input_tokens"]
            else:assert vector["input_tokens"]+vector["output_tokens"]!=vector["total_tokens"]


def check_other_payload(case):
    """Finite independent raw identity/container/state rules; no Subject helpers."""
    p=case.product;row=case.fault[-1];kind=p.fault
    if p.family=="gate":
        if kind in ("COMPLETE","BLOCKED","STOP"):assert row["state"]==kind
        elif kind=="equal_heads":assert row["head_before"]==row["head_after"]=="1"*40
        elif kind in ("worker_wrong","swapped"):assert row["gemini_worker_run_id"]==("other-reviewer-1" if kind=="worker_wrong" else "sample-reviewer-0")
        if kind in ("reviewer_wrong","swapped","identical"):assert row["gemini_reviewer_run_id"]==("other-worker-1" if kind=="reviewer_wrong" else "sample-worker-0")
        return
    if p.family=="coverage":
        pm=row["provider_metrics"]
        pair={"both32":(32,32),"bothzero":(0,0),"observedzero":(1,0),"positive_zero":(1,0),"observed_exceeds":(1,2)}.get(kind,(1,1))
        assert type(pm["attempts"]) is int and type(pm["usage_observed_attempts"]) is int
        assert (pm["attempts"],pm["usage_observed_attempts"])==pair
        usage=(0,0,0) if kind in ("bothzero","observedzero") else (100,20,120) if kind in ("partial","full_precedence") else (10,4,99) if kind=="both_invalid" else (10,4,14)
        subtotal=(10,4,14) if kind in ("partial","full_precedence") else usage
        h.assert_exact_typed(pm["usage"],dict(zip(fx.P_KEYS,usage)))
        h.assert_exact_typed(pm["observed_usage_subtotal"],dict(zip(fx.P_KEYS,subtotal)))
        assert pm["state"]==("PARTIAL" if kind=="partial" else "OK" if kind=="OK" else fx.OBS)
        return
    path=p.paths[0]
    parent=location(row,path[:-1]) if path else None
    if kind in ("missing","absent") and p.family not in ("identity_map","shape"):
        assert path[-1] not in parent;return
    if kind=="absent":assert path[-1] not in parent;return
    value=location(row,path) if path else row
    if kind=="null":assert value is None;return
    if p.family=="alias":
        if kind=="dict":assert type(value) is dict and tuple(value)==("PRIVATE-OPAQUE",) and type(value["PRIVATE-OPAQUE"]) is h.ForeignOpaqueLeaf
        elif kind=="list":assert type(value) is list and len(value)==1 and type(value[0]) is h.ForeignOpaqueLeaf
        else:assert type(value) is h.ForeignOpaqueLeaf
    elif p.family=="state":
        typ,payload={"list":(list,[]),"dict":(dict,{}),"subclass":(h.ForeignStrKey,None),"unknown":(str,"UNKNOWN"),
            "OK":(str,"OK"),"PARTIAL":(str,"PARTIAL"),"matching":(str,"OBSERVED_MATCHING_ATTEMPTS"),"valid":(str,"OBSERVED_COMPLETED_TURNS")}[kind]
        assert type(value) is typ
        if kind!="subclass":h.assert_exact_typed(value,payload)
    elif p.family in ("identity_map","shape"):
        if kind in ("list","subclass"):
            assert type(value) is (list if kind=="list" else h.ForeignDict);return
        assert type(value) is dict
        keys=SEMANTIC if p.layer.endswith("semantic") else WORK if p.layer.endswith("work") else fx.P_KEYS if p.layer in ("full","subtotal") else None
        removed="stage_id" if p.layer.endswith("semantic") else "schema_version" if p.layer.endswith("work") else "input_tokens" if p.layer in ("full","subtotal") else "run_id" if p.layer=="root" else "state"
        if kind in ("integer_key","foreign_key"):
            foreign=[key for key in value if type(key) is not str]
            assert len(foreign)==1 and type(foreign[0]) is (int if kind=="integer_key" else h.ForeignStrKey)
            assert removed not in tuple(key for key in value if type(key) is str)
        elif kind in ("64","65","32","33"):assert len(value)==int(kind) and all(type(k) is str for k in value)
        elif kind=="valid_mismatch":assert tuple(sorted(value))==tuple(sorted(keys)) and value["fingerprint"]=="f"*64
        else:
            wanted=set(keys)
            if kind=="extra":wanted.add("extra")
            elif kind=="missing":wanted.remove(removed)
            assert all(type(k) is str for k in value) and set(value)==wanted
    else:
        field=p.field
        if kind=="subclass":assert type(value) is (h.ForeignFinished if field=="schema_version" else h.ForeignStrKey);return
        if kind=="wrong_type":assert type(value) is list and value==[];return
        if field=="schema_version":
            typ,payload={"invalid":(int,2),"bool":(bool,True),"float":(float,1.0),"valid":(int,1)}[kind]
        else:
            typ=str
            if kind in ("invalid","grammar"):payload="-bad" if field in ("stage_id","scenario_key") else "../ref#x" if field=="gap_reference" else "x"*64
            elif field in ("stage_id","scenario_key"):payload="a"*(129 if kind=="long129" else 128)
            elif field=="gap_reference":payload={"utf8_500":"docs/"+"é"*246+"a#x","utf8_501":"docs/"+"é"*246+"aa#x",
                "chars501":"docs/"+"a"*494+"#x","traversal":"../ref#x","absolute":"/ref#x","drive":"C:/ref#x",
                "empty_anchor":"docs/ref#","two_anchors":"docs/ref#x#y","control":"docs/r\n#x","c1":"docs/\x85#x",
                "backslash":"docs\\ref#x","surrogate":"docs/\ud800#x","valid":"docs/valid#x"}[kind]
            else:payload={"short":"a"*63,"long":"a"*65,"uppercase":"A"*64,"nonhex":"x"*64,"newline":"a"*63+"\n","valid":"f"*64}[kind]
        assert type(value) is typ and value==payload
        for other in p.paths[1:]:h.assert_exact_typed(location(row,other),value)


def check_plan(cases,expected=EXPECTED):
    identifiers=[c.product.id for c in cases]
    assert len(identifiers)==len(set(identifiers))
    assert set(identifiers)==set(expected)
    for case in cases:
        assert case.product==expected[case.product.id]
        h.assert_custody_unchanged(case.fault,case.fault_snapshot)
        check_payload(case)


def check_completed(completed,expected):
    ids=[entry[0] for entry in completed]
    assert len(ids)==len(set(ids)) and set(ids)==set(expected)
    for identifier,calls,custody in completed:
        assert type(identifier) is str and type(calls) is int and calls==3 and custody is True


def execute(case):
    """Three actual report calls; no faulty identity preflight or expected-from-UUT."""
    good,fault,eg,ef=case.good,case.fault,case.good_report,case.fault_report
    frame=retained.RetainedCallFrame([good,fault,eg,ef],lambda:fx.preflight(good))
    original=getattr(report_module,API);calls=[];clean=False
    def reached(records):
        calls.append(records)
        return original(records)
    try:
        with patch.object(report_module,API,reached),h.armed_hooks():
            frame.call(report_module,API,(good,),eg)
            frame.call(report_module,API,(fault,),ef)
            frame.call(report_module,API,(good,),eg)
            assert len(calls)==3 and calls[0] is good and calls[1] is fault and calls[2] is good
            assert h.get_total_hook_calls()==0
    finally:
        frame.assert_all_custody()
        assert getattr(report_module,API) is original
        clean=True
    return (case.product.id,len(calls),clean)


def construct(product):
    if product.family in ("scalar","hex","number"):return scalar_case(product)
    if product.family in ("identity","identity_map","identity_bound","coherent"):return identity_case(product)
    if product.family=="token":return token_case(product)
    return other_case(product)


def test_complete_catalogue_at_actual_report():
    case_data.assert_catalogue(EXPECTED_PRODUCTS)
    cases=[construct(p) for p in EXPECTED_PRODUCTS]
    check_plan(cases)
    completed=[]
    for case in cases:
        try:completed.append(execute(case))
        except AssertionError as error:raise AssertionError(case.product.id) from error
    check_completed(completed,EXPECTED)


def test_completion_rejects_drop_duplicate_actual_path_payload_and_no_call():
    wrong=list(EXPECTED_PRODUCTS);wrong[0]=case_data.Product("wrong","root","run_id","missing","single",(("run_id",),))
    for bad in (EXPECTED_PRODUCTS[:-1],EXPECTED_PRODUCTS+EXPECTED_PRODUCTS[:1],wrong):
        with pytest.raises(AssertionError):case_data.assert_catalogue(bad)
    p=next(p for p in EXPECTED_PRODUCTS if p.family=="scalar" and p.field=="model" and p.fault=="space" and p.context=="single")
    case=scalar_case(p);expected={p.id:p};check_plan([case],expected)
    for cases in ([],[case,case]):
        with pytest.raises(AssertionError):check_plan(cases,expected)
    for wrong in ("path","payload"):
        changed=scalar_case(p)
        if wrong=="path":changed.fault[0]["model"]="gpt-6.1-sol";changed.fault[0]["effort"]="bad id"
        else:changed.fault[0]["model"]=3
        with pytest.raises(AssertionError):check_plan([changed],expected)
    completed=[execute(case)];check_completed(completed,expected)
    for bad in ([],completed+completed,[(p.id,0,True)]):
        with pytest.raises(AssertionError):check_completed(bad,expected)
    bad=scalar_case(p);bad.fault[0]["model"]=3
    bad.fault_snapshot=h.capture_custody(bad.fault)
    with pytest.raises(AssertionError):check_plan([bad],expected)


def test_case_data_exports_and_purity_contract():
    tree=ast.parse(Path(case_data.__file__).read_text(encoding="utf-8"))
    declared={n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    declared.update(target.id for statement in tree.body if isinstance(statement,ast.Assign)
        for target in statement.targets if isinstance(target,ast.Name) and not target.id.startswith("_"))
    assert type(case_data.__all__) is tuple and len(case_data.__all__)==len(set(case_data.__all__))
    assert set(case_data.__all__)==declared
    assert not any(isinstance(n,(ast.Import,ast.ImportFrom)) for function in tree.body
        if isinstance(function,ast.FunctionDef) for n in ast.walk(function))
    forbidden={"open","eval","exec","compile","__import__","read_text","read_bytes","write_text","write_bytes",
        "summarize_work_lineage_ledger","normalize_work_cost_row"}
    for function in tree.body:
        if isinstance(function,ast.FunctionDef):
            for call in (n for n in ast.walk(function) if isinstance(n,ast.Call)):
                assert (call.func.id if isinstance(call.func,ast.Name) else call.func.attr if isinstance(call.func,ast.Attribute) else "") not in forbidden
    with patch.object(report_module,API,side_effect=AssertionError("Subject used by fixture")),patch.object(
        report_module,"normalize_work_cost_row",side_effect=AssertionError("Subject used by fixture")),patch.object(
        row_module,"normalize_work_cost_row",side_effect=AssertionError("Subject used by fixture")):
        cases=[construct(p) for p in EXPECTED_PRODUCTS]
        check_plan(cases)


def test_native_outer_bounds_good_fault_same_good_and_custody():
    sample=healthy();good=[sample]*2000
    oracle=single_report();oracle.update(input_record_count=2000,duplicate_record_count=1999)
    originals=tuple(good);values=copy.deepcopy(good)
    frame=retained.RetainedCallFrame([sample,oracle],lambda:fx.preflight([sample]))
    original=getattr(report_module,API);reached=[]
    def actual(records):reached.append(records);return original(records)
    wrong=(None,{},(),h.ForeignList(),good+[sample])
    snapshots=[None if type(v) is list else h.capture_custody(v) for v in wrong]
    class Whole:
        def call(self,owned_sample):
            assert owned_sample is sample
            return getattr(report_module,API)(good)
    whole=Whole()
    try:
        with patch.object(report_module,API,actual),h.armed_hooks():
            for bad,snapshot in zip(wrong,snapshots):
                frame.call(whole,"call",(sample,),oracle)
                with pytest.raises(report_module.WorkCostReportError) as caught:getattr(report_module,API)(bad)
                assert str(caught.value)=="invalid work lineage ledger records"
                frame.call(whole,"call",(sample,),oracle)
                if snapshot is not None:h.assert_custody_unchanged(bad,snapshot)
                else:assert len(bad)==2001 and all(r is sample for r in bad)
                frame.assert_all_custody()
            assert len(reached)==15 and all(reached[i*3] is good and reached[i*3+1] is bad and reached[i*3+2] is good for i,bad in enumerate(wrong))
            assert h.get_total_hook_calls()==0
    finally:
        assert getattr(report_module,API) is original
        assert len(good)==2000 and all(a is b for a,b in zip(good,originals)) and good==values
        frame.assert_all_custody()
        for bad,snapshot in zip(wrong,snapshots):
            if snapshot is not None:h.assert_custody_unchanged(bad,snapshot)
            else:assert len(bad)==2001 and all(r is sample for r in bad)


def test_every_mutable_return_branch_is_detached_and_restorable():
    p=next(p for p in EXPECTED_PRODUCTS if p.family=="state" and p.layer=="provider" and p.fault=="OK" and p.context=="single")
    case=other_case(p)
    for records,oracle in ((case.good,case.good_report),(case.fault,case.fault_report)):
        paths=list(fx.paths(oracle))
        for path in paths:
            frame=retained.RetainedCallFrame([records,oracle],lambda:fx.preflight(case.good))
            try:
                with h.armed_hooks():
                    first=frame.call(report_module,API,(records,),oracle)
                    second=frame.call(report_module,API,(records,),oracle)
                    target=fx.at(first,path);saved=list(target.items()) if type(target) is dict else list(target)
                    fixed=[(v,h.capture_custody(v)) for v in (records,oracle,second)]
                    siblings=[(fx.at(first,p),h.capture_custody(fx.at(first,p))) for p in paths
                        if p!=path and p[:len(path)]!=path and path[:len(p)]!=p]
                    try:
                        if type(target) is dict:target["mutation-only"]=999
                        else:target.append({"mutation-only":999})
                        fx.unchanged(fixed+siblings)
                    finally:
                        target.clear()
                        if type(target) is dict:target.update(saved)
                        else:target.extend(saved)
                        frame.assert_all_custody();fx.unchanged(fixed+siblings)
                    h.assert_exact_typed(first,oracle)
                    frame.call(report_module,API,(records,),oracle)
                    assert h.get_total_hook_calls()==0
            finally:frame.assert_all_custody()


def test_actual_numeric_and_grouping_guards_same_whole_witness():
    numeric=next(p for p in EXPECTED_PRODUCTS if p.family=="number" and p.field=="duration_seconds" and p.fault=="overflow" and p.context=="single")
    grouping=next(p for p in EXPECTED_PRODUCTS if p.family=="state" and p.layer=="provider" and p.fault=="OK" and p.context=="single")
    for case,name,value in ((scalar_case(numeric),"MAX_NUMERIC",1000000000001),(other_case(grouping),"GROUPING_PROVIDER_STATES",row_module.GROUPING_PROVIDER_STATES|{"OK"})):
        original=getattr(row_module,name);execute(case)
        try:
            setattr(row_module,name,value)
            with pytest.raises(AssertionError,match=h._ERR_TYPED):execute(case)
        finally:
            setattr(row_module,name,original);assert getattr(row_module,name) is original
            execute(case)
