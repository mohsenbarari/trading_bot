from hashlib import sha256
import json,math,os,re,selectors,subprocess,time
MAX_JSON=2*1024**2
MAX_NATIVE_REPORT=2*1024**2
PHASE="unarmed"
VERIFIERS={'verify_image_component_presence.py': ('3cd10d7bc591615723f7b85a87011d0d7029120324f09ef2f099d49137bcfd94', 'security_image_component_presence/1', 65536), 'verify_python_runtime.py': ('1c984804bedc8eb2b52fb77add484a53ba9df8e65420f0e3f8a2e754255627f3', 'future_app_python18_presence/1', 16384)}
NATIVE_PAYLOAD_PROBE_SHA256 = '1071f5988c2b0ed256a5973211bee1a2ec0ab860b7f60e59162b81ad353c6042'
NATIVE_PAYLOAD_EXPECTATION_SHA256='65f30c3e6789e081ff4b00f19d575f753d2eaa64fbb0991aeff9ccce815de53b'
EXPECTED_NATIVE_FILES=259
EXPECTED_HISTORICAL_NATIVE_FILES=260
PUBLIC_PROBE_LIMITS={'probe_native_payload.py':MAX_NATIVE_REPORT,'verify_image_component_presence.py':65536,'verify_acl_consumer_presence.py':MAX_JSON,'verify_python_runtime.py':16384}
PUBLIC_PROBE_START_CODES=frozenset(('public_probe_start_contract','public_probe_start_timeout','public_probe_stdout_bound','public_probe_stderr_bound','public_probe_start_nonzero','public_probe_start_finalization'))
def need(ok, code):
    if not ok: raise RuntimeError(code)

def document(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, 'evidence_shape')
    def unique(rows):
        out = {}
        for key, value in rows:
            need(key not in out, 'evidence_shape'); out[key] = value
        return out
    def finite(value):
        number = float(value); need(math.isfinite(number), 'evidence_shape'); return number
    return json.loads(raw, object_pairs_hook=unique, parse_float=finite,
        parse_constant=lambda _: need(False, 'evidence_shape'))

def same(a, b):
    if type(a) is not type(b): return False
    if type(a) is dict: return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if type(a) is list: return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b

def presence_args(old, ctx, name, image, proof, filename, pin, control):
    command = ['/usr/local/bin/python3','-I','-B','/proof/'+filename,'--image-id',image,
        '--source-commit',control['source_commit'],'--verifier-sha256',pin]
    return ['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never','--network=none','--read-only',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--user=0:0','--memory=512m','--memory-swap=512m',
        '--cpus=1','--pids-limit=64','--ulimit','core=0','--ulimit','fsize=2097153:2097153',
        '--mount','type=bind,source='+str(proof)+',target=/proof,readonly','--entrypoint','/usr/local/bin/python3',image,*command[1:]]

def public_probe_start(ctx,old,cid,filename):
    """Only fixed public probe STARTs; retain bounded diagnostics before closes."""
    need(type(filename)is str and filename in PUBLIC_PROBE_LIMITS and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock']
        and type(cid)is str and re.fullmatch('[a-f0-9]{64}',cid),'public_probe_start_contract')
    maximum=PUBLIC_PROBE_LIMITS[filename];end=min(ctx.end,time.monotonic()+90);begun=time.monotonic()
    buffers={'stdout':bytearray(),'stderr':bytearray()};counts={'stdout':0,'stderr':0}
    child=None;selector=None;error=None;result=None;close_errors=[]
    environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(ctx.root/'docker-config')}
    try:
        ctx.check()
        child=subprocess.Popen([*old.DOCKER,'start','--attach',cid],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,start_new_session=True,close_fds=True)
        selector=selectors.DefaultSelector()
        for key,pipe in (('stdout',child.stdout),('stderr',child.stderr)):
            os.set_blocking(pipe.fileno(),False);selector.register(pipe.fileno(),selectors.EVENT_READ,key)
        while selector.get_map():
            ctx.check();left=end-time.monotonic();need(left>0,'public_probe_start_timeout')
            for event,_ in selector.select(min(.1,left)):
                key=event.data;limit=maximum if key=='stdout'else 8192
                raw=os.read(event.fd,min(65536,limit-counts[key]+1))
                if not raw:selector.unregister(event.fd);continue
                counts[key]+=len(raw);buffers[key].extend(raw[:max(0,limit-len(buffers[key]))])
                need(counts[key]<=limit,'public_probe_stdout_bound'if key=='stdout'else'public_probe_stderr_bound')
        try:code=child.wait(timeout=max(.01,end-time.monotonic()))
        except subprocess.TimeoutExpired:raise RuntimeError('public_probe_start_timeout')from None
        ctx.check();need(type(code)is int and code==0,'public_probe_start_nonzero');result=bytes(buffers['stdout'])
    except BaseException as failure:error=failure
    # Preserve actual owned-child outcome, including bounded timeout/overflow
    # cleanup, without signalling any reaped or unowned PID.
    if child is not None:
        try:ctx.C.cleanup_owned_child(child)
        except BaseException as failure:
            if error is None:error=failure
    output=bytes(buffers['stdout']);detail=bytes(buffers['stderr']);tail_offset=max(min(len(output),4096),len(output)-4096)
    elapsed=time.monotonic()-begun
    receipt={'schema':'canonical_app_public_probe_start/1','probe':filename,
        'client_spawned':child is not None,'actual_client_returncode':child.returncode if child is not None and type(child.returncode)is int else None,
        'stdout_bytes_observed':counts['stdout'],'stdout_bytes_captured':len(output),'stdout_captured_sha256':sha256(output).hexdigest(),
        'stdout_prefix_text':output[:4096].decode('utf-8',errors='replace'),
        'stdout_tail_text':output[tail_offset:].decode('utf-8',errors='replace'),'stdout_tail_offset':tail_offset,
        'stdout_truncated':counts['stdout']>len(output)or tail_offset>min(len(output),4096),
        'stderr_bytes_observed':counts['stderr'],'stderr_text':detail.decode('utf-8',errors='replace'),
        'stderr_truncated':counts['stderr']>len(detail),
        'elapsed_seconds':round(elapsed,3)if math.isfinite(elapsed)and 0<=elapsed<=3600 else None,
        'closed_code':error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in PUBLIC_PROBE_START_CODES else('success'if error is None else'unclassified'),
        'capture_before_collector_physical_closes':True,'security_accepted':False}
    try:
        # C.write registers the exact known bytes/FD with the existing outer
        # ProofKeeper at creation; neither selector nor child pipe is closed yet.
        ctx.C.write(ctx.root/'reports'/('public-probe-start-'+filename[:-3]+'.json'),encode(receipt));ctx.check()
    except BaseException as failure:
        if error is None:error=failure
    # All resources are closed even when diagnostics or a source guard refuse.
    if selector is not None:
        try:selector.close()
        except BaseException as failure:close_errors.append(failure)
    if child is not None:
        for pipe in(child.stdout,child.stderr):
            try:pipe.close()
            except BaseException as failure:close_errors.append(failure)
    try:ctx.check()
    except BaseException as failure:
        if error is None:error=failure
    if close_errors and error is None:error=RuntimeError('public_probe_start_finalization')
    if error is not None:raise error
    return result

def presence(ctx, old, image, control):
    global PHASE
    PHASE = 'fresh_same_image_presence'; proof = ctx.root/'presence-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    for filename,(pin,_,_) in VERIFIERS.items():
        raw, check = old.sealed(ctx.stack,ctx.root/'source'/filename,pin,131072); ctx.checks.append(check)
        ctx.C.write(proof/filename,raw,0o444)
    results = {}
    for index,(filename,(pin,schema,maximum)) in enumerate(VERIFIERS.items()):
        name = 'app-ci-presence-'+old.owner()+('', '-python18')[index]
        # Names are explicit and cleanup admits exact owner/name/image before RPC.
        ctx.created.append((name,image)); cid = ctx.docker(presence_args(old,ctx,name,image,proof,filename,pin,control),maximum=256).decode().strip()
        need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
        mounts = old.document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',cid],maximum=8192))
        need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(proof)
            and mounts[0].get('Destination') == '/proof' and mounts[0].get('RW') is False,'presence_mounts')
        raw = public_probe_start(ctx,old,cid,filename)
        need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit')
        value = document(raw)
        need(value.get('schema') == schema and value.get('image_id') == image and value.get('source_commit') == control['source_commit']
            and value.get('verifier_sha256') == pin and value.get('security_accepted') is False
            and value.get('production_mounts_or_data_used') is False and value.get('scanner_findings_modified') is False,'presence_identity')
        ctx.write(ctx.root/'reports'/('fresh-'+filename[:-3]+'.json'),value); results[filename] = value
    return results

def native_payload(ctx, old, image, control):
    """New same-image payload proof; does not alter the32 smoke assertions."""
    global PHASE
    PHASE = 'fresh_same_image_native_payload'
    script, script_check = old.sealed(ctx.stack,ctx.root/'source/probe_native_payload.py',NATIVE_PAYLOAD_PROBE_SHA256,131072)
    expectation, expectation_check = old.sealed(ctx.stack,ctx.root/'source/native-expectation.json',NATIVE_PAYLOAD_EXPECTATION_SHA256,MAX_NATIVE_REPORT)
    ctx.checks.extend((script_check,expectation_check))
    expected = document(expectation)
    need(expected.get('schema') == 'image_native_payload_expectation/2' and expected.get('armed') is True
        and expected.get('missing_package_sources') == [] and expected.get('security_accepted') is False,'payload_expectation_missing')
    proof = ctx.root/'native-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    ctx.C.write(proof/'probe_native_payload.py',script,0o444)
    ctx.C.write(proof/'native-expectation.json',expectation,0o444)
    name = 'app-ci-native-payload-'+old.owner()
    argv = presence_args(old,ctx,name,image,proof,'probe_native_payload.py',NATIVE_PAYLOAD_PROBE_SHA256,control)
    argv += ['--collect-expected-native-payload','--expectation-sha256',NATIVE_PAYLOAD_EXPECTATION_SHA256,
             '--expectation-gid',str((proof/'native-expectation.json').lstat().st_gid)]
    ctx.created.append((name,image)); cid = ctx.docker(argv,maximum=256).decode().strip()
    need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
    mounts = old.document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',cid],maximum=8192))
    need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(proof)
        and mounts[0].get('Destination') == '/proof' and mounts[0].get('RW') is False,'presence_mounts')
    raw = public_probe_start(ctx,old,cid,'probe_native_payload.py')
    need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit')
    value = document(raw)
    expected_scope = {key:control[key] for key in ('image_id','source_commit','source_tree','input_signature')}
    expected_scope.update(platform='linux/amd64',build_run_id=control['run_id'],build_run_attempt=control['run_attempt'])
    need(value.get('schema') == 'image_native_payload_observation/2' and same(value.get('scope'),expected_scope)
        and value.get('verifier_sha256') == NATIVE_PAYLOAD_PROBE_SHA256 and value.get('expectation_sha256') == NATIVE_PAYLOAD_EXPECTATION_SHA256
        and same(value.get('native_scope'),expected.get('native_scope'))
        and type(value.get('native_files_verified')) is int and value['native_files_verified'] == expected.get('native_file_count') == EXPECTED_NATIVE_FILES
        and type(value.get('historical_native_files_covered')) is int and value['historical_native_files_covered'] == expected.get('historical_native_file_count') == EXPECTED_HISTORICAL_NATIVE_FILES
        and type(value.get('distribution_filtered_files_verified')) is int and value['distribution_filtered_files_verified'] == 1
        and type(value.get('policy_files_verified')) is int and value['policy_files_verified'] == 1
        and type(value.get('distribution_filtered_absences')) is list and len(value['distribution_filtered_absences']) == 1
        and same(value['distribution_filtered_absences'][0].get('contract'),expected['distribution_filtered_absences'][0])
        and value['distribution_filtered_absences'][0].get('lookup_errno') == 'ENOENT'
        and value['distribution_filtered_absences'][0].get('all_existing_prefix_directories_held_until_physical_finalization') is True
        and value['distribution_filtered_absences'][0].get('policy_file_hash_verified') is True
        and value.get('inventory_complete') is True and value.get('production_mounts_or_data_used') is False
        and value.get('scanner_findings_modified') is False and value.get('security_accepted') is False,'presence_identity')
    native_complete(value,expected)
    ctx.write(ctx.root/'reports/fresh-native-payload.json',value)
    return value, expected

CODES=frozenset(('intake_unarmed', 'payload_expectation_missing', 'actual_archive_observation_missing', 'intake_shape', 'evidence_shape', 'evidence_identity', 'evidence_signature', 'evidence_reports', 'export_shape', 'export_identity', 'export_binding', 'archive_observation', 'canonical_labels', 'capsule_binding', 'presence_identity', 'presence_mounts', 'presence_exit', 'smoke_receipt', 'source_admission', 'hosted_ci_only', 'owned_cleanup_unproven', 'cleanup_report_unproven', 'caller_source_gaps', 'outer_keeper_required', 'inventory_receipt', 'inventory_input', 'inventory_bounds', 'unexpected_failure', 'inventory_cli_stage', 'inventory_cli_timeout', 'inventory_cli_stdout_bound', 'inventory_cli_stderr_bound', 'inventory_cli_nonzero', 'inventory_cli_pipe_close'))|PUBLIC_PROBE_START_CODES

def native_complete(value,expected,*,expected_packages=20,expected_aliases=28):
 need(type(value.get('aliases_verified'))is int and value['aliases_verified']==expected['alias_count']==expected_aliases,'presence_identity')
 rows=value.get('packages');need(type(rows)is list and len(rows)==len(expected['packages'])==expected_packages,'presence_identity');seen=set();total=0
 for p in rows:
  need(type(p)is dict and p.get('package')not in seen,'presence_identity');seen.add(p['package']);matches=[e for e in expected['packages']if e['package']==p['package']];need(len(matches)==1,'presence_identity');e=matches[0]
  need(same({k:v for k,v in p.items()if k!='actual_files'},e),'presence_identity');positive=[f for f in e['expected_files']if f['path']!=expected['distribution_filtered_absences'][0]['original_deb_file']['path']];need(same(p.get('actual_files'),positive),'presence_identity');total+=sum(f['bytes']for f in positive)
 need(type(value.get('bytes_hashed'))is int and value['bytes_hashed']==total,'presence_identity')

def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()

EXTRA_NATIVE_PAYLOAD_PROBE_SHA256 = '541c5ad694ec2235bb553d6e2fb2b9b9ab5d42732f7cd40e96994ec9666b491c'
EXTRA_NATIVE_PAYLOAD_EXPECTATION_SHA256 = '18af9a553ad648912e2e347e69c7bc7462bdaee5f2d1b007b2ee1b8e49ded3f9'
EXTRA_NATIVE_FILES=25
EXTRA_HISTORICAL_NATIVE_FILES=26
PUBLIC_PROBE_LIMITS['probe_native_extra.py']=MAX_NATIVE_REPORT
def native_extra(ctx, old, image, control):
    """New same-image payload proof; does not alter the32 smoke assertions."""
    global PHASE
    PHASE = 'fresh_same_image_native_payload'
    script, script_check = old.sealed(ctx.stack,ctx.root/'source/probe_native_extra.py',EXTRA_NATIVE_PAYLOAD_PROBE_SHA256,131072)
    expectation, expectation_check = old.sealed(ctx.stack,ctx.root/'source/native-extra-expectation.json',EXTRA_NATIVE_PAYLOAD_EXPECTATION_SHA256,MAX_NATIVE_REPORT)
    ctx.checks.extend((script_check,expectation_check))
    expected = document(expectation)
    need(expected.get('schema') == 'image_native_payload_expectation/2' and expected.get('armed') is True
        and expected.get('missing_package_sources') == [] and expected.get('security_accepted') is False,'payload_expectation_missing')
    proof = ctx.root/'native-extra-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    ctx.C.write(proof/'probe_native_extra.py',script,0o444)
    ctx.C.write(proof/'native-extra-expectation.json',expectation,0o444)
    name = 'app-ci-native-extra-'+old.owner()
    argv = presence_args(old,ctx,name,image,proof,'probe_native_extra.py',EXTRA_NATIVE_PAYLOAD_PROBE_SHA256,control)
    argv += ['--collect-expected-native-payload','--expectation-sha256',EXTRA_NATIVE_PAYLOAD_EXPECTATION_SHA256,
             '--expectation-gid',str((proof/'native-extra-expectation.json').lstat().st_gid)]
    ctx.created.append((name,image)); cid = ctx.docker(argv,maximum=256).decode().strip()
    need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
    mounts = old.document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',cid],maximum=8192))
    need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(proof)
        and mounts[0].get('Destination') == '/proof' and mounts[0].get('RW') is False,'presence_mounts')
    raw = public_probe_start(ctx,old,cid,'probe_native_extra.py')
    need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit')
    value = document(raw)
    expected_scope = {key:control[key] for key in ('image_id','source_commit','source_tree','input_signature')}
    expected_scope.update(platform='linux/amd64',build_run_id=control['run_id'],build_run_attempt=control['run_attempt'])
    need(value.get('schema') == 'image_native_payload_observation/2' and same(value.get('scope'),expected_scope)
        and value.get('verifier_sha256') == EXTRA_NATIVE_PAYLOAD_PROBE_SHA256 and value.get('expectation_sha256') == EXTRA_NATIVE_PAYLOAD_EXPECTATION_SHA256
        and same(value.get('native_scope'),expected.get('native_scope'))
        and type(value.get('native_files_verified')) is int and value['native_files_verified'] == expected.get('native_file_count') == EXTRA_NATIVE_FILES
        and type(value.get('historical_native_files_covered')) is int and value['historical_native_files_covered'] == expected.get('historical_native_file_count') == EXTRA_HISTORICAL_NATIVE_FILES
        and type(value.get('distribution_filtered_files_verified')) is int and value['distribution_filtered_files_verified'] == 1
        and type(value.get('policy_files_verified')) is int and value['policy_files_verified'] == 2
        and type(value.get('distribution_filtered_absences')) is list and len(value['distribution_filtered_absences']) == 1
        and same(value['distribution_filtered_absences'][0].get('contract'),expected['distribution_filtered_absences'][0])
        and value['distribution_filtered_absences'][0].get('lookup_errno') == 'ENOENT'
        and value['distribution_filtered_absences'][0].get('all_existing_prefix_directories_held_until_physical_finalization') is True
        and value['distribution_filtered_absences'][0].get('policy_file_hash_verified') is True
        and value.get('inventory_complete') is True and value.get('production_mounts_or_data_used') is False
        and value.get('scanner_findings_modified') is False and value.get('security_accepted') is False,'presence_identity')
    native_complete(value,expected,expected_packages=3,expected_aliases=2)
    need(value.get('additional_policy_files')==[EXTRA_POLICY_FILE]and value.get('policy_bytes_hashed')==4182,'presence_identity')
    ctx.write(ctx.root/'reports/fresh-native-extra.json',value)
    return value, expected

EXTRA_POLICY_FILE={'path': '/etc/xattr.conf', 'bytes': 681, 'sha256': '0fc794a9826011c88b118c5ff4e30dfcbebd73518e64b0cda7aaec3ad7e578bd', 'mode': 33188, 'uid': 0, 'gid': 0}
