"""Ordinary exact-six rehearsal inside the one owned disposable Ubuntu only."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256
import argparse,base64,errno,json,os,re,shutil,sys,time

FAMILIES={'ubuntu239':('libc-bin','libc-dev-bin','libc-devtools','libc6','libc6-dev','locales'),
          'ubuntu243':('libc-bin','libc-dev-bin','libc-gconv-modules-extra','libc6','libc6-dev','locales')}
VERSIONS={'ubuntu239':'2.39-0ubuntu8.9','ubuntu243':'2.43-2ubuntu2.4'}
M_PIN=(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82')
def need(value,code):
    if not value:raise RuntimeError(code)
def encoded(value):
    parts=[];size=1
    for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
        raw=part.encode();need(size+len(raw)<=4*1024**2,'report_bound');size+=len(raw);parts.append(raw)
    return b''.join(parts)+b'\n'
def read_pin(path,pin,maximum):
    path=Path(path);s=path.lstat();need(path.resolve(strict=True)==path and path.is_file()and s.st_nlink==1 and 0<s.st_size<=maximum,'input_scope')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        raw=os.pread(fd,s.st_size+1,0)
        def identity(v):return(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        need(len(raw)==s.st_size and sha256(raw).hexdigest()==pin and identity(s)==identity(os.fstat(fd))==identity(path.lstat()),'input_pin')
    finally:os.close(fd)
    return raw
def no_policy():
    try:Path('/usr/sbin/policy-rc.d').lstat()
    except FileNotFoundError as error:need(error.errno==errno.ENOENT,'policy_unknown')
    else:need(False,'policy_suppression_or_unknown')
def solver(raw,names,version):
    installs={};configured=set()
    def record(line,kind,code):
        # APT may append one transient broken-dependency status list after the
        # closing parenthesis. It is metadata, never another install action.
        head=(r'Inst ([A-Za-z0-9+.-]+)(?::amd64)?(?: \[[^\]\n]+\])? \('
              if kind=='Inst' else r'Conf ([A-Za-z0-9+.-]+)(?::amd64)? \(')
        match=re.fullmatch(head+r'([^ )]+)([^()\[\]\n]*)(?: \[([a-z0-9]+)\])?\)(?: \[([^\[\]\n]*)\])?',line)
        need(match is not None and match[1]in names and match[2]==version,code)
        arch=match[4]
        need(arch is None or arch==('all'if match[1]=='locales'else'amd64'),code)
        annotation=match[5]
        if annotation:
            need(re.fullmatch(r'[A-Za-z0-9+.-]+:(?:amd64|all)(?: [A-Za-z0-9+.-]+:(?:amd64|all))* ?',annotation)is not None,code)
            tokens=annotation.rstrip(' ').split(' ')
            need(len(tokens)==len(set(tokens)),code)
            for token in tokens:
                name,status_arch=token.split(':')
                need(name in names and status_arch==('all'if name=='locales'else'amd64'),code)
        return match[1],match[2]
    for line in raw.decode('utf8','strict').splitlines():
        need(not line.startswith(('Remv ','Purg ')),'solver_removal')
        if line.startswith('Inst '):
            name,target=record(line,'Inst','solver_nonfamily_or_version')
            need(name not in installs,'solver_nonfamily_or_version');installs[name]=target
        if line.startswith('Conf '):
            name,target=record(line,'Conf','solver_configuration')
            need(name not in configured,'solver_configuration');configured.add(name)
    need(set(installs)==set(names)==configured,'solver_incomplete_family');return installs
def simulation_evidence(raw,command_record,phase,diagnostic=False):
    """Retain exact bounded successful stdout before interpreting its plan."""
    need(type(raw)is bytes and 0<=len(raw)<=256*1024 and phase in {'legacy_default_forward','corrected_forward','corrected_rollback'}
         and type(diagnostic)is bool and diagnostic==(phase=='legacy_default_forward'),'simulation_evidence_scope')
    need(type(command_record)is dict and command_record.get('actual_returncode')==0
         and type(command_record.get('actual_returncode'))is int and type(command_record.get('bytes'))is int and command_record.get('bytes')==len(raw)
         and command_record.get('sha256')==sha256(raw).hexdigest(),'successful_simulation_identity')
    return {'phase':phase,'argv':command_record['argv'],'actual_returncode':0,'raw_output_bytes':len(raw),
            'raw_output_sha256':sha256(raw).hexdigest(),'raw_output_base64':base64.b64encode(raw).decode('ascii'),
            'diagnostic_only':diagnostic,'strict_solver_accepted':False,'accepted_for_install':False}
def checked_solver(raw,names,version,evidence,all_evidence):
    need(evidence.get('diagnostic_only')is False and evidence.get('phase')in {'corrected_forward','corrected_rollback'},'diagnostic_not_install_authority')
    try:result=solver(raw,names,version)
    except BaseException as error:
        error.family6_solver_phase=evidence['phase'];error.family6_simulation_evidence=all_evidence;raise
    evidence['strict_solver_accepted']=True;evidence['accepted_for_install']=True;return result
def package_state(raw):
    result={}
    for line in raw.decode('utf8','strict').splitlines():
        fields=line.split('\t');need(len(fields)==4 and fields[0]not in result and fields[3]=='ii ','package_database_not_clean')
        result[fields[0]]=fields[1:]
    need(1<=len(result)<=1024,'package_database_bound');return result
def unchanged_nonfamily(before,after,names):
    need(set(before)==set(after)and{k:v for k,v in before.items()if k not in names}=={k:v for k,v in after.items()if k not in names},'nonfamily_package_changed')
def configuration():
    result={}
    for name in ('/etc/locale.gen','/etc/default/locale','/etc/locale.conf','/etc/ld.so.conf'):
        p=Path(name)
        try:s=p.lstat()
        except FileNotFoundError as error:need(error.errno==errno.ENOENT,'configuration_unknown');result[name]={'absent':True};continue
        if p.is_symlink():result[name]={'symlink':os.readlink(p)};continue
        need(p.is_file()and s.st_nlink==1 and s.st_size<=16384,'configuration_bound')
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:raw=os.pread(fd,16385,0);need(len(raw)==s.st_size and len(raw)<=16384,'configuration_bound')
        finally:os.close(fd)
        result[name]={'sha256':sha256(raw).hexdigest(),'bytes':len(raw),'mode':s.st_mode&0o7777}
    return result
def apt_snapshot(profile):
    suite='noble'if profile=='ubuntu239'else'resolute'
    leaf='ubuntu.sources'if profile=='ubuntu239'else'locked-snapshot-bootstrap.sources'
    suites=[suite,suite+'-updates',*([suite+'-backports']if profile=='ubuntu239'else[]),suite+'-security']
    components='main restricted universe multiverse'if profile=='ubuntu239'else'main universe'
    sources=Path('/etc/apt/sources.list.d');names=sorted(p.name for p in sources.iterdir())
    need(names==[leaf],'unexpected_APT_source')
    plain=Path('/etc/apt/sources.list');need(not plain.is_symlink()and plain.is_file()and plain.stat().st_size==0,'unexpected_plain_APT_source')
    path=sources/leaf;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:raw=os.pread(fd,16385,0);need(len(raw)<=16384,'APT_source_bound')
    finally:os.close(fd)
    expected=('Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/20261101T000000Z/\nSuites: '+' '.join(suites)+'\nComponents: '+components+'\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n').encode()
    need(raw==expected,'APT_source_identity')
    records={};expected_names={'snapshot.ubuntu.com_ubuntu_20261101T000000Z_dists_'+s+'_InRelease'for s in suites}
    for p in Path('/var/lib/apt/lists').iterdir():
        if not p.name.endswith('_InRelease'):continue
        need(p.name in expected_names and not p.is_symlink(),'APT_InRelease_scope')
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:body=os.pread(fd,512*1024+1,0);need(0<len(body)<=512*1024,'APT_InRelease_bound')
        finally:os.close(fd)
        records[p.name]={'bytes':len(body),'sha256':sha256(body).hexdigest()}
    need(set(records)==expected_names,'APT_InRelease_count');return{'sources':raw.decode(),'authenticated_by_normal_APT_update':True,'InRelease':records}

def _execute(stage,expected_input,failure_context):
    simulation_records=failure_context['successful_simulation_evidence']
    need(os.getuid()==os.geteuid()==0 and re.fullmatch('[a-f0-9]{32}',os.environ.get('FAMILY6_NONCE',''))and Path('/.dockerenv').is_file(),'owned_disposable_only')
    cfg=json.loads(read_pin('/inputs/input.json',expected_input,65536));profile=cfg['profile'];need(profile in FAMILIES,'profile')
    names=FAMILIES[profile];old=VERSIONS[profile];new=old+'+gold1'
    need(cfg['old_version']==old and cfg['new_version']==new and set(cfg['old'])==set(cfg['new'])==set(names),'exact_family')
    raw=read_pin('/inputs/host_io_original.py',M_PIN[1],65536);need(len(raw)==M_PIN[0],'command_source_pin')
    M=ModuleType('_bounded_family6');M.__file__='/inputs/host_io_original.py';exec(compile(raw,M.__file__,'exec'),M.__dict__)
    end=time.monotonic()+1200
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive'}
    commands=[];remaining=3*1024**2
    def command(args,maximum=256*1024):
        nonlocal remaining
        need(remaining>0,'command_evidence_bound')
        try:raw=M.run(args,env=env,deadline=end,maximum=min(maximum,remaining))
        except BaseException as error:
            truth=getattr(error,'public_command_failure',{})
            captured=truth.get('captured_output',b'');need(type(captured)is bytes and len(captured)<=maximum,'failed_output_bound')
            error.family6_failed_command={'argv':args,'actual_returncode':truth.get('actual_returncode'),'actual_pipe_closed':truth.get('actual_pipe_closed'),'stdout_eof_observed':truth.get('stdout_eof_observed'),'captured_bytes':len(captured),'captured_output_utf8':captured.decode('utf8','replace'),'command_accepted':False}
            raise
        remaining-=len(raw)
        commands.append({'argv':args,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'actual_returncode':0,'output_utf8':raw.decode('utf8','replace')})
        return raw
    def status():return package_state(command(['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n']))
    def verify(version):
        current=status()
        for name in names:need(current.get(name)==[version,'all'if name=='locales'else'amd64','ii '],'installed_family')
        need(command(['/usr/bin/dpkg','--audit'])==b'','pending_dpkg_audit')
        command(['/usr/bin/apt-get','--no-download','check'])
        command(['/usr/sbin/ldconfig'])
        command(['/lib64/ld-linux-x86-64.so.2','--list','/bin/true'])
        need(command(['/usr/bin/getconf','GNU_LIBC_VERSION']).strip()==('glibc '+old.split('-')[0]).encode(),'actual_libc_version')
        locales=command(['/usr/bin/locale','-a']).decode().splitlines()
        need('en_US.utf8'in locales and'fa_IR.utf8'in locales,'selected_locales_missing')
        command(['/usr/bin/getent','passwd','root'])
        no_policy();return current
    def archives(which):
        paths=[]
        for name in names:
            record=cfg[which][name];leaf=name+'_'+(old if which=='old'else new)+'_'+('all'if name=='locales'else'amd64')+'.deb'
            source=Path('/inputs')/which/leaf
            raw=read_pin(source,record['sha256'],8*1024**2);need(len(raw)==record['bytes'],'package_size')
            control=command(['/usr/bin/dpkg-deb','-f',str(source)]).decode()
            fields={}
            for line in control.splitlines():
                if line and not line[0].isspace()and': 'in line:
                    key,value=line.split(': ',1);need(key not in fields,'duplicate_package_control');fields[key]=value
            for key,value in {'Package':name,'Version':old if which=='old'else new,'Architecture':'all'if name=='locales'else'amd64','Source':'glibc'}.items():need(fields.get(key)==value,'package_control')
            target=Path('/var/cache/apt/archives')/leaf
            # Populate ordinary APT's cache, never transplant payload binaries.
            shutil.copyfile(source,target);target.chmod(0o644);need(len(read_pin(target,record['sha256'],8*1024**2))==record['bytes'],'cache_copy')
            paths.append(str(target))
        return paths
    def apt(paths,rollback=False,simulate=False,legacy_default=False):
        need(not legacy_default or(simulate and not rollback),'legacy_diagnostic_simulation_only')
        args=['/usr/bin/apt-get','--no-download','--no-remove']
        if not legacy_default:args.append('--no-install-recommends')
        if rollback:args.append('--allow-downgrades')
        args+=['--simulate']if simulate else['-y']
        try:return command(args+['install',*paths])
        except BaseException as error:
            if simulate:error.family6_solver_phase='legacy_default_forward'if legacy_default else'corrected_rollback'if rollback else'corrected_forward'
            error.family6_simulation_evidence=simulation_records;raise
    failure_context['phase']='owned_policy_admission'
    no_policy();report={'schema':'disposable_exact_family6_worker/1','profile':profile,'stage':stage,'Host_install_allowed':False,'real_Host_PID1_reexec_proven':False,'security_accepted':False}
    if stage=='prepare':
        failure_context['phase']='signed_old_six_preparation'
        # Signed network preparation is done by the outer fixed bootstrap.
        # No build/multilib cohort is added to the ordinary six-family baseline.
        preparation=['/usr/bin/apt-get','--no-remove','--no-install-recommends']
        report['preparation_solver_utf8']=command(preparation+['--simulate','install',*[name+'='+old for name in names]]).decode('utf8','strict')
        command(preparation+['-y','install',*[name+'='+old for name in names]])
        Path('/etc/locale.gen').write_text('en_US.UTF-8 UTF-8\nfa_IR.UTF-8 UTF-8\n')
        command(['/usr/sbin/locale-gen']);command(['/usr/sbin/update-locale','LANG=en_US.UTF-8'])
        report['baseline']=verify(old);report['configuration']=configuration();report['signed_APT']=apt_snapshot(profile)
        report['baseline_snapshot_sha256']=sha256(encoded({'packages':report['baseline'],'configuration':report['configuration'],'apt':report['signed_APT']})).hexdigest()
    else:
        need(stage=='exercise','stage')
        failure_context['phase']='baseline_verification'
        baseline=verify(old);before=configuration();apt_before=apt_snapshot(profile)
        report['baseline_snapshot_sha256']=sha256(encoded({'packages':baseline,'configuration':before,'apt':apt_before})).hexdigest()
        failure_context['phase']='forward_archives'
        forward=archives('new');report['successful_simulation_evidence']=simulation_records
        failure_context['phase']='legacy_default_forward'
        legacy=apt(forward,simulate=True,legacy_default=True);simulation_records.append(simulation_evidence(legacy,commands[-1],'legacy_default_forward',True))
        failure_context['phase']='corrected_forward'
        forward_raw=apt(forward,simulate=True);forward_evidence=simulation_evidence(forward_raw,commands[-1],'corrected_forward');simulation_records.append(forward_evidence)
        failure_context['phase']='forward_solver_validation'
        report['forward_solver']=checked_solver(forward_raw,names,new,forward_evidence,simulation_records)
        failure_context['phase']='forward_apply'
        apt(forward)
        failure_context['phase']='forward_verification'
        after=verify(new)
        failure_context['phase']='forward_nonfamily_validation'
        unchanged_nonfamily(baseline,after,names)
        failure_context['phase']='forward_configuration'
        report['forward_configuration']=configuration()
        failure_context['phase']='rollback_archives'
        rollback=archives('old')
        failure_context['phase']='corrected_rollback'
        rollback_raw=apt(rollback,rollback=True,simulate=True);rollback_evidence=simulation_evidence(rollback_raw,commands[-1],'corrected_rollback');simulation_records.append(rollback_evidence)
        failure_context['phase']='rollback_solver_validation'
        report['rollback_solver']=checked_solver(rollback_raw,names,old,rollback_evidence,simulation_records)
        failure_context['phase']='rollback_apply'
        apt(rollback,rollback=True)
        failure_context['phase']='rollback_verification'
        restored=verify(old)
        failure_context['phase']='rollback_package_state'
        need(restored==baseline,'rollback_package_state')
        failure_context['phase']='rollback_configuration'
        need(configuration()==before,'rollback_configuration_drift')
        failure_context['phase']='rollback_APT_snapshot'
        need(apt_snapshot(profile)==apt_before,'offline_APT_sources_changed')
        report.update(forward_exact_six=True,rollback_exact_six=True,nonfamily_packages_unchanged=True,configuration_restored_exactly=True)
    report['commands']=commands;report['status']='PASS_DISPOSABLE_ONLY'
    failure_context['phase']='final_report_encoding'
    raw=encoded(report);need(len(raw)<=4*1024**2,'report_bound')
    failure_context['phase']='final_report_publication'
    sys.stdout.write(raw.decode());return report
def execute(stage,expected_input):
    # One bounded list owns every completed simulation across the whole worker.
    # Preserve the original exception and its command/finality attributes.
    context={'phase':'worker_admission','successful_simulation_evidence':[]}
    try:return _execute(stage,expected_input,context)
    except BaseException as error:
        error.family6_worker_phase=context['phase']
        error.family6_simulation_evidence=context['successful_simulation_evidence']
        raise
def main(argv=None):
    argv=sys.argv[1:]if argv is None else argv
    if len(argv)!=3 or argv[0]!='--owned-family6' or argv[1]not in{'prepare','exercise'}or re.fullmatch('[a-f0-9]{64}',argv[2])is None:
        print('{"status":"UNARMED","Host_action":false}');return 0
    try:execute(argv[1],argv[2]);return 0
    except BaseException as error:
        text=str(error);code=text if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',text)else'rehearsal_refused'
        value={'status':'FAILED','code':code,'failed_public_command':getattr(error,'family6_failed_command',None),'worker_phase':getattr(error,'family6_worker_phase',None),'solver_validation_phase':getattr(error,'family6_solver_phase',None),'successful_simulation_evidence':getattr(error,'family6_simulation_evidence',None),'automatic_retry':False,'Host_install_allowed':False,'security_accepted':False}
        sys.stdout.write(encoded(value).decode());return 1
if __name__=='__main__':raise SystemExit(main())
