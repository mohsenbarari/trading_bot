"""Minimal frozen-recipe runtime-last hook. Source-only, future refs UNBOUND."""
from hashlib import sha256
from pathlib import Path
import argparse,json,re

PAIR_QUALIFICATION_REF={'bytes':23392,'sha256':'7cacf5aaf2c03759343c4a355fe9e8f0e414412fae22ccce0d17eb22c7a2073d'}
APP_NODE_QUALIFICATION_REF=None
APP_FROZEN_BUSINESS_REF=None
APP_BUSINESS_COMMIT='4df2fed29859c0b82367666521f1a345f269f58a'
RECIPE_PINS={'app':(5812,'54447fdee488e17246238796cb5e6778a382227a51399256da0b22eeb147178e'),
 'market':(6805,'870b558bf54c07219a5ed078cb8b1d12b38a265301ecd1589715c2a6cc121c33')}
MARKET_LOCK_PIN=(1062,'5399a09ae00cfaec4d9801c9ccf26c9ccde0914e6957f27fe02e396452061091')
MARKET_CATALOG_PIN=(179771,'33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831')
MARKET_DRIVER_PIN=(101762,'65ec5e1957cdb066971cccf130348b8a87dc5e106b825baa867781e1fa888049')
MARKET_ROLES=('capture2-f79','native/coin-estimator','native/estimator-snapshot-sender','native/market-fact-receiver','native/market-store-adapter',
 'web/estimator-snapshot-receiver','web/market-capture-account1','web/market-capture-external','web/market-fact-sync-worker','web/market-processor')
HOOK=b'''# Exact local glibc runtime pair; assembly/toolchain base stays unchanged.
RUN --network=none \\
    --mount=type=bind,from=glibc-runtime,target=/glibc-capsule,ro \\
    --mount=type=tmpfs,target=/tmp \\
    ["/usr/bin/setpriv", "--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "/usr/local/bin/python3", "-I", "-S", "-B", "/glibc-capsule/prepare_glibc_security_os_cache.py", "--install-image-cache", "--cache-dir", "/glibc-capsule"]
'''
ANCHORS={'app':b'RUN python -I -B scripts/verify_image_trust.py --require-certifi\n',
 'market':b'RUN python -I -B /usr/local/libexec/verify_psycopg2_runtime.py --package-root /opt/market-python\n'}

def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def bound(row):
 need(type(row)is dict and set(row)=={'bytes','sha256'}and type(row['bytes'])is int and 0<row['bytes']<=32*1024**2
  and type(row['sha256'])is str and re.fullmatch('[a-f0-9]{64}',row['sha256'])is not None,'future_binding');return row
def transform(kind,raw):
 need(kind in RECIPE_PINS and type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==RECIPE_PINS[kind],'frozen_recipe_changed')
 marker=ANCHORS[kind];need(raw.count(marker)==1 and b'glibc-runtime'not in raw,'recipe_anchor')
 before,after=raw.split(marker);need(b'FROM patched-base AS runtime\n'in before,'runtime_stage_required')
 if kind=='market':need(before.rfind(b'COPY --from=assembly /rootfs/ /')>before.rfind(b'FROM patched-base AS runtime\n'),'runtime_assembly_order')
 result=before+HOOK+marker+after;need(result.replace(HOOK,b'',1)==raw,'unexpected_recipe_delta');return result
def frozen_market_sources(recipe,requirements,catalog,driver,role):
 need(role in MARKET_ROLES,'closed_role')
 for raw,pin in[(recipe,RECIPE_PINS['market']),(requirements,MARKET_LOCK_PIN),(catalog,MARKET_CATALOG_PIN),(driver,MARKET_DRIVER_PIN)]:
  need(type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==pin,'frozen_market_source_changed')
 need(b'pyasn1==0.6.5'in requirements and b'whole_git_component_no_main_fallback'in driver,'business_source_policy')
 return {'role':role,'business_policy':'existing_whole_Git_component_no_Main_fallback','catalog_sha256':MARKET_CATALOG_PIN[1],
  'requirements_sha256':MARKET_LOCK_PIN[1],'pyasn1':'0.6.5','new_image_evidence_required':True,'security_accepted':False}
def admit_future_build(kind,*,pair_ref,node_ref=None,business_ref=None):
 need(kind in RECIPE_PINS,'closed_subject');need(PAIR_QUALIFICATION_REF is not None,'pair_qualification_UNBOUND')
 need(bound(pair_ref)==PAIR_QUALIFICATION_REF,'pair_qualification_changed')
 if kind=='app':
  need(APP_NODE_QUALIFICATION_REF is not None and APP_FROZEN_BUSINESS_REF is not None,'App_source_or_Node_UNBOUND')
  need(bound(node_ref)==APP_NODE_QUALIFICATION_REF and bound(business_ref)==APP_FROZEN_BUSINESS_REF,'App_source_or_Node_changed')
 return {'image_build_only':True,'security_accepted':False,'old_G12_reused_as_new_image_proof':False}
def named_context_record(files):
 expected={'manifest.json','glibc_image_io.py','prepare_glibc_security_os_cache.py',
  'libc6_2.41-12+deb13u4+gold1_amd64.deb','libc-bin_2.41-12+deb13u4+gold1_amd64.deb'}
 need(type(files)is dict and set(files)==expected,'named_context_inventory')
 for pin in files.values():bound(pin)
 value={'schema':'glibc84243_exact_runtime_named_context/1','files':files,'security_accepted':False}
 return value,sha256(encoded(value)).hexdigest()
def extend_argv(original,named_context,signature):
 need(type(original)is list and all(type(v)is str for v in original)and(original[:3]==['/usr/bin/docker','--host=unix:///var/run/docker.sock','build']
  or original[:4]==['/usr/bin/docker','--host=unix:///var/run/docker.sock','buildx','build']),'ordinary_builder_argv')
 need(type(named_context)is str and Path(named_context).is_absolute()and '\n'not in named_context and ','not in named_context
  and re.fullmatch('[a-f0-9]{64}',signature)is not None and not any('glibc-runtime='in v for v in original),'named_context_operand')
 # The ordinary producer retains its existing default/business contexts and
 # argument order. Its successor must hold/hash these five external files
 # through build/export, record this map and verify the added label on Config.
 return original[:3]+['--build-context','glibc-runtime='+named_context,'--label','io.gold-trade.glibc.named-context='+signature]+original[3:]if original[2]=='build'else original[:4]+['--build-context','glibc-runtime='+named_context,'--label','io.gold-trade.glibc.named-context='+signature]+original[4:]
def main(argv=None):
 argparse.ArgumentParser(description=__doc__).parse_args(argv)
 print(json.dumps({'status':'SOURCE_READY_FUTURE_PACKAGE_AND_NODE_INPUTS_UNBOUND','image_build':False,'security_accepted':False}));return 0
if __name__=='__main__':raise SystemExit(main())
