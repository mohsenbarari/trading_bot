"""Independent Unit C fixtures and literal full-report oracles."""
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import copy
import hashlib
import json
from dataclasses import dataclass
from tests import test_two_site_work_cost_report as fx
from tests import test_two_site_work_cost_witness_helpers as h

API="summarize_work_lineage_ledger"


MAX=1000000000000


MISSING=object()


SCALARS=("run_id","model","effort","state","gemini_worker_run_id","gemini_reviewer_run_id")


HEADS=("head_before","head_after","proposal_sha256")


TIMES=("duration_seconds","finished_at")

FINISHED_VALID=("int0","float0","int5","float5","intMAX","floatMAX","ascii1","ascii80","numeric_string")


SEMANTIC=("stage_id","scenario_key","gap_reference","source_sha256","fingerprint")


WORK=("schema_version","fingerprint")


IDENTITY_LAYERS=("top_semantic","top_work","nested_semantic","nested_work")


LAYERS={"top_semantic":("semantic_identity",),"top_work":("work_lineage",),
        "nested_semantic":("provider_metrics","semantic_identity"),"nested_work":("provider_metrics","work_lineage")}


TOKEN_FAULTS=("missing","null","bool","float","negative","string","subclass","overflow","arithmetic","zero","MAX")


SCALAR_FAULTS=("missing","null","empty","space","newline","integer","subclass","long","valid80")


HEX_FAULTS=("missing","null","empty","short","long","uppercase","nonhex","newline","integer","subclass","valid")


NUMBER_FAULTS=("missing","null","negative_int","negative_float","bool_true","bool_false","foreign_int","foreign_float",
               "nan","positive_inf","negative_inf","overflow","huge","int0","float0","int5","float5","intMAX","floatMAX")

NUMBER_TAG_RULES={"negative_int":(int,-1),"negative_float":(float,-1.0),"bool_true":(bool,True),"bool_false":(bool,False),
    "overflow":(int,1000000000001),"huge":(int,10**400),"int0":(int,0),"float0":(float,0.0),
    "int5":(int,5),"float5":(float,5.0),"intMAX":(int,1000000000000),"floatMAX":(float,1000000000000.0)}

IDENTITY_FAULTS=("missing","null","wrong_type","subclass","invalid")


MAP_FAULTS=("absent","null","list","extra","missing","integer_key","foreign_key","subclass")


ROOT_ALIASES=("schema_version","dimensions","codex_usage","reported_cycle_seconds","worker_run_id","reviewer_run_id",
              "provider_state","nested_identities","group_eligible","observed_candidate","provider_usage","reasoning_tokens","reasoning_output_tokens")


PROVIDER_ALIASES=("full_usage","observed_subtotal","attempt_pair")


def healthy(state="CONTINUE"):
    return fx.row(provider=(10,4,14),pair=(1,1),seconds=5,state=state)


def clone(value):
    """Copy builtin containers; no foreign hooks."""
    if type(value) is list:return [clone(v) for v in value]
    if type(value) is dict:
        result=value.copy()
        for key,item in value.items():
            if type(key) is str:result[key]=clone(item)
        return result
    return value


def expected_costs(recipe="known",n=1):
    """Literal costs, never from Subject."""
    c=(n,0,(100*n,80*n,20*n,10*n),True)
    p=(n,0,(10*n,4*n,14*n),True,n,0,n,n)
    t=(n,0,5.0*n,True)
    if recipe in ("codex_unknown","malformed"):c=(0,n,(0,0,0,0),False)
    if recipe in ("provider_unknown","malformed"):p=(0,n,(0,0,0),False,0,n,0,0)
    if recipe in ("timing_unknown","malformed"):t=(0,n,0.0,False)
    if recipe in ("provider_partial","provider_fallback"):p=(n,0,(10*n,4*n,14*n),False,n,0,n,n)
    if recipe=="provider_no_cost":p=(0,n,(0,0,0),False,n,0,n,n)
    if recipe=="counts_unknown":p=(n,0,(10*n,4*n,14*n),False,0,n,0,0)
    if recipe=="zero_incomplete":p=(n,0,(0,0,0),False,n,0,n,0)
    if recipe=="zero_full":p=(n,0,(0,0,0),True,n,0,0,0)
    return fx.costs(c,p,t)


def single_report(recipe="known",candidate=True,group=True):
    if recipe in ("provider_unknown","provider_partial","malformed"):group=False
    result=cost_report(expected_costs(recipe),group,candidate)
    if recipe=="malformed":result["unattributed"]["malformed_record_count"]=1
    return result


def product_id(family,layer,field,fault,context):
    return "/".join(("C682",family,layer,field,fault,context))


@dataclass(frozen=True)
class Product:
    family:str
    layer:str
    field:str
    fault:str
    context:str
    paths:tuple

    @property
    def id(self):return product_id(self.family,self.layer,self.field,self.fault,self.context)


def required_products():
    """Authored domains; no Subject."""
    products=[]
    for family,fields,faults in (("scalar",SCALARS,SCALAR_FAULTS),("hex",HEADS,HEX_FAULTS),("number",TIMES,NUMBER_FAULTS)):
        for field in fields:
            if field=="finished_at":faults=NUMBER_FAULTS+("ascii1","ascii80","ascii81","empty_ascii","nonascii","control","numeric_string")
            for fault in faults:
                contexts=("single","same_run")
                if family=="scalar" and fault!="valid80":contexts+=("repeat_invalid" if field=="run_id" else "same_invalid",)
                if field=="finished_at" and fault not in FINISHED_VALID:
                    contexts+=("same_invalid",)
                if family=="scalar" and field!="run_id" and fault=="valid80" or field=="finished_at" and fault in FINISHED_VALID:
                    contexts+=("positive_invalid",)
                if field=="run_id" and fault=="valid80":contexts+=("repeat_valid",)
                for context in contexts:
                    products.append(Product(family,"root",field,fault,context,((field,),)))
    for layer in IDENTITY_LAYERS:
        fields=SEMANTIC if layer.endswith("semantic") else WORK
        for field in fields:
            for fault in IDENTITY_FAULTS:
                for context in ("single","same_run","same_invalid"):
                    products.append(Product("identity",layer,field,fault,context,(LAYERS[layer]+(field,),)))
        for fault in MAP_FAULTS:
            for context in (("single","same_run") if fault=="absent" else ("single","same_run","same_invalid")):
                products.append(Product("identity_map",layer,"map",fault,context,(LAYERS[layer],)))
        for field in fields:
            bounds=("valid128","long129","grammar") if field in ("stage_id","scenario_key") else (
                "utf8_500","utf8_501","chars501","traversal","absolute","drive","empty_anchor","two_anchors","control","c1","backslash","surrogate") if field=="gap_reference" else (
                "short","long","uppercase","nonhex","newline","valid") if field in ("source_sha256","fingerprint") else ("bool","float","valid")
            for fault in bounds:
                for context in (("single","same_run") if fault in ("valid","valid128","utf8_500") else ("single","same_run","same_invalid")):
                    products.append(Product("identity_bound",layer,field,fault,context,(LAYERS[layer]+(field,),)))
        if layer.startswith("nested"):
            for context in ("single","same_run"):
                products.append(Product("identity_map",layer,"map","valid_mismatch",context,(LAYERS[layer],)))
    for layer,fields in (("top_semantic",SEMANTIC),("top_work",WORK)):
        other="nested_semantic" if layer.endswith("semantic") else "nested_work"
        for field in fields:
            for fault in (("valid","utf8_500") if field=="gap_reference" else ("valid",)):
                for context in ("single","same_run"):
                    products.append(Product("coherent",layer,field,fault,context,(LAYERS[layer]+(field,),LAYERS[other]+(field,))))
    for family,fields,prefix in (("codex",fx.C_KEYS,()),("full",fx.P_KEYS,("provider_metrics","usage")),
                                 ("subtotal",fx.P_KEYS,("provider_metrics","observed_usage_subtotal"))):
        for field in fields:
            for fault in TOKEN_FAULTS:
                for context in ("single","same_run"):
                    paths=tuple(prefix+(k,) for k in fields)
                    products.append(Product("token",family,field,fault,context,paths))
    for family,layer,fields,faults,prefix in (
        ("gate","root",("candidate",),("COMPLETE","BLOCKED","STOP","equal_heads","worker_wrong","reviewer_wrong","swapped","identical"),()),
        ("coverage","provider",("pair",),("both32","bothzero","observedzero","positive_zero","observed_exceeds","full_precedence","both_invalid","partial","OK"),("provider_metrics",)),
        ("count","provider",("attempts","usage_observed_attempts"),("missing","null","bool","float","negative","string","subclass","zero","32","33"),("provider_metrics",)),
        ("state","root",("usage_state",),("missing","null","list","dict","subclass","unknown","valid"),()),
        ("state","provider",("state",),("missing","null","list","dict","subclass","unknown","OK","PARTIAL","matching"),("provider_metrics",)),
        ("alias","root",ROOT_ALIASES,("dict","list","bomb"),()),
        ("alias","provider",PROVIDER_ALIASES,("dict","list","bomb"),("provider_metrics",)),
        ("shape","root",("map",),("64","65","list","subclass","integer_key","foreign_key"),()),
        ("shape","provider",("map",),("32","33","absent","null","list","subclass","integer_key","foreign_key"),("provider_metrics",)),
        ("shape","full",("map",),MAP_FAULTS,("provider_metrics","usage")),
        ("shape","subtotal",("map",),MAP_FAULTS,("provider_metrics","observed_usage_subtotal"))):
        for field in fields:
            for fault in faults:
                for context in ("single","same_run"):
                    paths=(prefix if field=="map" else prefix+(field,),)
                    if family=="gate":
                        keys=("state",) if fault in ("COMPLETE","BLOCKED","STOP") else ("head_after",) if fault=="equal_heads" else SCALARS[-2:] if fault=="swapped" else (SCALARS[4 if fault=="worker_wrong" else 5],)
                        paths=tuple((k,) for k in keys)
                    if family=="coverage":paths=(("provider_metrics",),)
                    products.append(Product(family,layer,field,fault,context,paths))
    return tuple(products)


EXPECTED_PRODUCTS=required_products()


EXPECTED={p.id:p for p in EXPECTED_PRODUCTS}

def assert_catalogue(products):
    """Literal catalogue seal, not a runtime oracle."""
    rows=sorted((p.family,p.layer,p.field,p.fault,p.context,p.paths) for p in products)
    assert len(rows)==1368
    assert hashlib.sha256(json.dumps(rows,separators=(",",":")).encode()).hexdigest()=="2f533d5b0ee8cb818c3c7872ea4784a6f787b53712a89a17030a192e489f7708"

@dataclass
class Case:
    product:Product
    good:object
    fault:object
    good_report:dict
    fault_report:dict
    fault_snapshot:object


def location(value,path):
    for key in path:value=value[key]
    return value


def replace_field(value,path,replacement):
    parent=location(value,path[:-1]);key=path[-1]
    if replacement is MISSING:parent.pop(key,None)
    else:parent[key]=replacement


def reference_field(value,path,payload):
    """Independent raw edit for custody."""
    parent=value
    for key in path[:-1]:parent=parent[key]
    if payload is MISSING:
        if path[-1] in parent:del parent[path[-1]]
    else:parent[path[-1]]=payload


def pair_report(single,conflict=False,duplicate=False,invalid_run=False,works=1,base=None):
    """Combine literal oracles, never Subject results."""
    if duplicate:
        result=copy.deepcopy(single);result.update(input_record_count=2,duplicate_record_count=1)
        return result
    if single["unattributed"]["malformed_record_count"]==1:
        cost=fx.costs((1,1,(100,80,20,10),False),(1,1,(10,4,14),False,1,1,1,1),(1,1,5.0,False))
        known=expected_costs()
        return fx.report(2,2,cost,[fx.lineage("a",1,1,1,1,known)],attempts=1,candidates=1,
            unattributed=(1,0,1),unknown_cost=expected_costs("malformed"))
    cost=copy.deepcopy(expected_costs() if base is None else base["costs"])
    for name in ("codex","provider","reported_cycle_seconds"):
        target=cost[name];other=single["costs"][name]
        for key,value in target.items():
            if type(value) is int:target[key]=value+other[key]
        subtotal=target["observed_subtotal"]
        if type(subtotal) is dict:
            for key in subtotal:subtotal[key]+=other["observed_subtotal"][key]
        else:target["observed_subtotal"]+=other["observed_subtotal"]
        full="full_total" if name=="reported_cycle_seconds" else "full_usage"
        target[full]=copy.deepcopy(target["observed_subtotal"]) if target[full] is not None and other[full] is not None else None
    if conflict:return fx.quarantine_report(2,cost,1,works)
    assert invalid_run and single["logical_work_count"]==1
    candidates=1+single["lineages"][0]["observed_candidate_count"]
    return fx.report(2,2,cost,[fx.lineage("a",1,1,2,candidates,cost)],attempts=1,candidates=1)


def field_value(product):
    field,fault=product.field,product.fault
    if product.family=="scalar":
        values={"missing":MISSING,"null":None,"empty":"","space":"bad id","newline":"x\n","integer":3,
                "subclass":h.ForeignStrKey("sample"),"long":"a"*81,"valid80":"a"*80}
        if fault=="valid80" and field=="gemini_worker_run_id":return "worker_"+"a"*73
        if fault=="valid80" and field=="gemini_reviewer_run_id":return "reviewer_"+"b"*71
        return values[fault]
    if product.family=="hex":
        width=64 if field=="proposal_sha256" else 40
        return {"missing":MISSING,"null":None,"empty":"","short":"a"*(width-1),"long":"a"*(width+1),
            "uppercase":"A"*width,"nonhex":"x"*width,"newline":"a"*(width-1)+"\n","integer":2,
            "subclass":h.ForeignStrKey("a"*width),"valid":"3"*width}[fault]
    if product.family=="number":
        return {"missing":MISSING,"null":None,"negative_int":-1,"negative_float":-1.0,"bool_true":True,"bool_false":False,
            "foreign_int":h.ForeignFinished(5),"foreign_float":h.ForeignDuration(5),"nan":float("nan"),
            "positive_inf":float("inf"),"negative_inf":float("-inf"),"overflow":1000000000001,"huge":10**400,
            "int0":0,"float0":0.0,"int5":5,"float5":5.0,"intMAX":MAX,"floatMAX":1000000000000.0,
            "ascii1":" ","ascii80":"~"*80,"ascii81":"~"*81,"empty_ascii":"","nonascii":"é","control":"a\x1f","numeric_string":"5"}[fault]
    raise AssertionError("unknown family")


def finish_case(product,good,fault,reference,eg,ef,duplicate=False,invalid_run=False,works=1):
    if product.context=="same_run":
        ef=pair_report(ef,conflict=not invalid_run and not duplicate,duplicate=duplicate,invalid_run=invalid_run,works=works,base=eg)
        fault=[clone(good[0]),fault[0]];reference=[clone(good[0]),reference[0]]
    elif product.context in ("same_invalid","positive_invalid"):
        path=LAYERS[product.layer] if product.family.startswith("identity") else product.paths[0]
        invalid=clone(good[0]);reference_field(invalid,path,None)
        fault=[clone(invalid),fault[0]];reference=[invalid,reference[0]]
        ef=pair_report(ef,duplicate=product.context=="same_invalid",conflict=product.context=="positive_invalid")
    elif product.context in ("repeat_invalid","repeat_valid"):
        fault=[fault[0],clone(fault[0])];reference=[reference[0],clone(reference[0])]
        ef=pair_report(ef,invalid_run=True,duplicate=product.context=="repeat_valid")
    return Case(product,good,fault,eg,ef,h.capture_custody(reference))


def scalar_case(product):
    blocked=product.context=="positive_invalid" and product.field.startswith("gemini_")
    good=[healthy("BLOCKED" if blocked else "CONTINUE")];fault=clone(good);eg=single_report(candidate=not blocked)
    reference=clone(good)
    value=field_value(product);replace_field(fault[0],product.paths[0],value)
    reference_field(reference[0],EXPECTED[product.id].paths[0],value)
    valid=product.fault.startswith(("valid","int","float")) and product.fault not in ("integer",)
    recipe="known";candidate=True
    if product.family=="scalar":
        candidate=product.field not in ("state","gemini_worker_run_id","gemini_reviewer_run_id") or (
            product.fault=="valid80" and product.field!="state")
    elif product.family=="hex":candidate=valid
    elif product.family=="number" and product.field=="duration_seconds" and not valid:recipe="timing_unknown"
    ef=single_report(recipe,candidate and not blocked)
    if product.family=="number" and product.field=="duration_seconds" and valid:
        total={"int0":0.0,"float0":0.0,"int5":5.0,"float5":5.0,"intMAX":1000000000000.0,"floatMAX":1000000000000.0}[product.fault]
        cost=fx.costs((1,0,(100,80,20,10),True),(1,0,(10,4,14),True,1,0,1,1),(1,0,total,True))
        ef=cost_report(cost)
    invalid_run=product.family=="scalar" and product.field=="run_id"
    duplicate=product.family=="number" and product.fault in ("int5","float5")
    return finish_case(product,good,fault,reference,eg,ef,duplicate,invalid_run)


def identity_value(product):
    field,kind=product.field,product.fault
    original=location(healthy(),product.paths[0])
    if kind=="missing":return MISSING
    if kind=="null":return None
    if kind=="wrong_type":return []
    if kind=="subclass":return h.ForeignFinished(1) if field=="schema_version" else h.ForeignStrKey(original)
    if kind in ("invalid","grammar"):
        return 2 if field=="schema_version" else "-bad" if field in ("stage_id","scenario_key") else "../ref#x" if field=="gap_reference" else "x"*64
    if kind=="bool":return True
    if kind=="float":return 1.0
    if field in ("stage_id","scenario_key"):return "a"*(129 if kind=="long129" else 128)
    if field=="gap_reference":
        return {"utf8_500":"docs/"+"é"*246+"a#x","utf8_501":"docs/"+"é"*246+"aa#x",
            "chars501":"docs/"+"a"*494+"#x","traversal":"../ref#x","absolute":"/ref#x","drive":"C:/ref#x",
            "empty_anchor":"docs/ref#","two_anchors":"docs/ref#x#y","control":"docs/r\n#x","c1":"docs/\x85#x",
            "backslash":"docs\\ref#x","surrogate":"docs/\ud800#x","valid":"docs/valid#x"}[kind]
    if field=="schema_version":return 1
    return {"short":"a"*63,"long":"a"*65,"uppercase":"A"*64,"nonhex":"x"*64,"newline":"a"*63+"\n","valid":"f"*64}[kind]


def identity_case(product):
    good=[healthy()];fault=[healthy()];reference=[healthy()];eg=single_report();group=False
    if product.family=="identity_map":
        path=product.paths[0];old=location(good[0],path);kind=product.fault
        if kind=="absent":value=MISSING;group=product.layer.startswith("nested")
        elif kind=="null":value=None
        elif kind=="list":value=[]
        elif kind=="subclass":value=h.ForeignDict(old)
        else:
            value=clone(old);key="stage_id" if product.layer.endswith("semantic") else "schema_version"
            if kind=="extra":value["extra"]=None
            elif kind=="missing":value.pop(key)
            elif kind in ("integer_key","foreign_key"):
                item=value.pop(key);value[3 if kind=="integer_key" else h.ForeignStrKey(key)]=item
            else:value["fingerprint"]="f"*64
        replace_field(fault[0],path,value);reference_field(reference[0],EXPECTED[product.id].paths[0],value)
        duplicate=False
    else:
        value=identity_value(product)
        for path in product.paths:replace_field(fault[0],path,value)
        for path in EXPECTED[product.id].paths:reference_field(reference[0],path,value)
        # Coherent positives explicitly edit both top and matching nested projections.
        group=product.family=="coherent" or product.field=="schema_version" and product.fault=="valid"
        duplicate=product.field=="schema_version" and product.fault=="valid"
    ef=single_report(group=group)
    if group and product.field=="fingerprint" and product.layer.endswith("work"):
        ef["lineages"][0]["work_fingerprint"]="f"*64
    works=2 if product.layer=="top_work" and product.field=="fingerprint" and product.fault=="valid" else 1
    return finish_case(product,good,fault,reference,eg,ef,duplicate,works=works)


def cost_report(cost,group=True,candidate=True):
    return fx.report(1,1,cost,[fx.lineage("a",1,1,1,int(candidate),cost)] if group else (),
        attempts=int(group),candidates=int(group and candidate),unattributed=(0,0,0) if group else (1,0,0),
        unknown_cost=None if group else cost)


def token_case(product):
    field,kind,layer=product.field,product.fault,product.layer
    fields=fx.C_KEYS if layer=="codex" else fx.P_KEYS
    baseline=(100,80,20,10) if layer=="codex" else (10,4,14)
    index=fields.index(field)
    if kind in ("bool","float","subclass"):
        baseline=((1,1,0,10),(21,1,20,10),(81,80,1,10),(100,80,20,1))[index] if layer=="codex" else ((1,4,5),(10,1,11),(1,0,1))[index]
    good=[healthy()]
    prefix=() if layer=="codex" else ("provider_metrics","usage" if layer=="full" else "observed_usage_subtotal")
    for key,value in zip(fields,baseline):replace_field(good[0],prefix+(key,),value)
    if layer=="subtotal":good[0]["provider_metrics"].pop("usage")
    c=(1,0,baseline,True) if layer=="codex" else (1,0,(100,80,20,10),True)
    p=(1,0,baseline,layer=="full",1,0,1,1) if layer!="codex" else (1,0,(10,4,14),True,1,0,1,1)
    eg=cost_report(fx.costs(c,p,(1,0,5.0,True)));fault=clone(good);reference=clone(good)
    payload={"missing":MISSING,"null":None,"bool":True,"float":1.0,"negative":-1,"string":"1",
             "subclass":h.ForeignFinished(1),"overflow":1000000000001,"arithmetic":baseline[index]+1}.get(kind)
    positive=kind in ("zero","MAX") or layer=="codex" and field=="output_tokens" and kind=="arithmetic"
    vector=None
    if kind in ("zero","MAX"):
        v=0 if kind=="zero" else 1000000000000
        vector=((v,v,0,10),(v+20,v,20,10),(v+80,80,v,10),(100,80,20,v))[index] if layer=="codex" else ((v,4,v+4),(10,v,10+v),(v,0,v))[index]
        if kind=="MAX" and index<len(fields)-1:
            vector=(MAX,MAX,0,10) if layer=="codex" and index<2 else (MAX,0,MAX,10) if layer=="codex" else (MAX,0,MAX) if index==0 else (0,MAX,MAX)
        for key,value in zip(fields,vector):
            replace_field(fault[0],prefix+(key,),value);reference_field(reference[0],prefix+(key,),value)
    else:
        replace_field(fault[0],prefix+(field,),payload);reference_field(reference[0],prefix+(field,),payload)
        if positive:vector=baseline[:index]+(baseline[index]+1,)+baseline[index+1:]
    if layer=="codex":c=(1,0,vector,True) if positive else (0,1,(0,0,0,0),False)
    elif positive:p=(1,0,vector,layer=="full",1,0,1,1)
    else:p=(1,0,(10,4,14),False,1,0,1,1) if layer=="full" else (0,1,(0,0,0),False,1,0,1,1)
    ef=cost_report(fx.costs(c,p,(1,0,5.0,True)))
    return finish_case(product,good,fault,reference,eg,ef)


def map_value(old,kind,key):
    if kind=="absent":return MISSING
    if kind=="null":return None
    if kind=="list":return []
    if kind=="subclass":return h.ForeignDict(old)
    value=clone(old)
    if kind=="extra":value["extra"]=None
    elif kind=="missing":value.pop(key)
    elif kind in ("integer_key","foreign_key"):
        payload=value.pop(key);value[3 if kind=="integer_key" else h.ForeignStrKey(key)]=payload
    else:
        size=int(kind)
        for i in range(size-len(value)):value["padding-"+str(i)]=h.ForeignOpaqueLeaf()
    return value


__all__ = ('API', 'MAX', 'MISSING', 'SCALARS', 'HEADS', 'TIMES', 'FINISHED_VALID', 'SEMANTIC', 'WORK', 'IDENTITY_LAYERS', 'LAYERS', 'TOKEN_FAULTS', 'SCALAR_FAULTS', 'HEX_FAULTS', 'NUMBER_FAULTS', 'NUMBER_TAG_RULES', 'IDENTITY_FAULTS', 'MAP_FAULTS', 'ROOT_ALIASES', 'PROVIDER_ALIASES', 'healthy', 'clone', 'expected_costs', 'single_report', 'product_id', 'Product', 'required_products', 'EXPECTED_PRODUCTS', 'EXPECTED', 'assert_catalogue', 'Case', 'location', 'replace_field', 'reference_field', 'pair_report', 'field_value', 'finish_case', 'scalar_case', 'identity_value', 'identity_case', 'cost_report', 'token_case', 'map_value')
