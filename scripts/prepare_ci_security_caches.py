# Fixed public CI cache bytes, explicitly approved and independently read back.
# No rebuild, mutable tag fallback, cache healing or deployment authorization.
import argparse,errno,json,os,re,runpy,stat,sys,tempfile
from contextlib import ExitStack
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path

RELEASE_ID = 430404133
RELEASE_TAG = 'security-app-os-cache-20261101'
RELEASE_TARGET = '48c9706b10d7db802e0a7552e26aea6ab4921299'
ASSET_IDS = {'util':697537730,'ncurses':697537733,'acl-tar':697537734,'sqlite-wal':697537737,'tar-acl':697537738,'coreutils-acl':697537815}
EXTRA_HELPERS = {'sqlite-wal':'sqlite_wal','tar-acl':'tar_acl','coreutils-acl':'coreutils_acl'}
LINEAGE = {
    'util':(394981846,'trixie-util-linux-build.yml','bfc769e97b3da3d61ee2a84b12d8bf7ad636eebb'),
    'ncurses':(395251997,'trixie-ncurses-build.yml','bfc769e97b3da3d61ee2a84b12d8bf7ad636eebb'),
    'acl-tar':(395274440,'trixie-acl-tar-build.yml','ec6131f41d9719bbaf2f45b3100b5b4a74f4f693'),
    'sqlite-wal':(397322674, 'sqlite-wal-package.yml', 'f88ef1d9dddd3c091b647e21d6c24f500a7212ce'),
    'tar-acl':(397517752, 'tar-acl-descriptor-package.yml', '81ff65c9c9238a4797dc2824c5c51d274afdd6bd'),
    'coreutils-acl':(398051843, 'coreutils-acl-descriptor-package.yml', 'aed063887607a7ee8e219f7e99b14c4761fccdaf'),
}
# The retained release catalog is immutable. Its old SQLite ZIP is checked
# as catalog history only; the active cache uses the Root-admitted CI artifact.
RETAINED_SQLITE = {'bytes':933486,'sha256':'974c48ab927cc4b4aa2de963cf6745a65c0245121185804a69e8c4fc0f78a265'}
SQLITE_ROOT_RECEIPT = '004e80fee6548d8b1f447620f79918ecc819577546e2aa21892999e6b270b5ac'
SQLITE_ARTIFACT_EXPIRY = '2027-01-30T22:38:13Z'
REPO = 'mohsenbarari/trading_bot'
ROOT = Path(__file__).resolve().parents[1]

def context_root(value):
    # NPM's no-argument path stays anchored to this script, never cwd.
    if value is None:return ROOT
    def require(ok,code):
        if ok is not True:raise RuntimeError(code)
    require(os.getuid()==os.geteuid()==1001 and os.getgid()==os.getegid()
        and sys.flags.isolated==sys.flags.no_site==sys.flags.dont_write_bytecode==1
        and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
        and os.environ.get('GITHUB_REPOSITORY')==REPO
        and os.environ.get('GITHUB_REF')=='refs/heads/main','release_context_hosted')
    root=Path(value);temporary=Path(os.environ.get('RUNNER_TEMP',''))
    workspace=Path(os.environ.get('GITHUB_WORKSPACE',''));source=Path(__file__).absolute()
    runroot=source.parents[2]
    require(all(p.is_absolute()and p.resolve(strict=True)==p for p in(root,temporary,workspace,source,runroot))
        and source==runroot/'controller-source/scripts/prepare_ci_security_caches.py'
        and root==runroot/'sealed/release'and runroot.is_relative_to(temporary)
        and runroot!=temporary and not runroot.is_relative_to(workspace),'release_context_path')
    for path in(root,root.parent,runroot,runroot/'controller-source',source.parent,temporary,workspace):
        before=path.lstat()
        require(stat.S_ISDIR(before.st_mode)and (before.st_uid,before.st_gid)==(1001,os.getegid())
            and not before.st_mode&0o022,'release_context_directory')
    require(stat.S_IMODE(runroot.stat().st_mode)==0o700,'release_context_directory')
    def read(path,maximum,expected=None):
        require(path.resolve(strict=True)==path,'release_context_source')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        primary=None;before=None
        frame=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        try:
            before=frame(os.fstat(fd));require(stat.S_ISREG(before[2])and before[3:6]==(1001,os.getegid(),1)
                and not before[2]&0o022 and 0<before[6]<=maximum,'release_context_source')
            raw=os.pread(fd,before[6]+1,0)
            require(len(raw)==before[6]and frame(os.fstat(fd))==frame(path.lstat())==before,'release_context_source')
            require(expected is None or {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==expected,'release_context_pin')
        except BaseException as error:
            primary=error;raise
        finally:
            try:
                os.close(fd)
                try:os.fstat(fd)
                except OSError as error:require(error.errno==errno.EBADF,'release_context_close')
                else:require(False,'release_context_close')
                if before is not None:require(frame(path.lstat())==before,'release_context_source')
            except BaseException as error:
                if primary is not None:
                    primary.release_context_finality_failed=True;raise primary from error
                raise
        return raw
    def unique(rows):
        result={}
        for key,item in rows:require(key not in result,'release_context_receipt');result[key]=item
        return result
    sealed=json.loads(read(root.parent/'source-inputs.json',4*1024**2),object_pairs_hook=unique,
        parse_constant=lambda _:require(False,'release_context_receipt'))
    receipt=sealed['inputs'];signature=sealed['input_signature'];head=os.environ.get('GITHUB_SHA','')
    canonical=(json.dumps(receipt,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    require(type(head)is str and re.fullmatch('[a-f0-9]{40}',head)is not None
        and receipt['release_commit']==head and receipt['repository_identity']=={'host':'github.com','repository':REPO}
        and receipt['schema']in{'normal_market_git_component_inputs/1','normal_market_git_component_inputs/2'}
        and receipt['signature_format']=='normal-market-two-git-sources-v1'
        and sha256(canonical).hexdigest()==signature
        and sealed['sealed_source_only']is True and sealed['build_performed']is False
        and sealed['deployment_eligible']is False,'release_context_receipt')
    names=('prepare_app_security_os_cache.py','compose_security_os_candidate.py','app_security_os_packages.lock.json',
        'prepare_sqlite_wal_os_cache.py','sqlite_wal_package.lock.json','prepare_tar_acl_os_cache.py','tar_acl_package.lock.json',
        'prepare_coreutils_acl_os_cache.py','coreutils_acl_package.lock.json')
    entries=receipt['release_files'];require(type(entries)is list and len(entries)in{20,21}
        and all(type(row)is dict and type(row.get('path'))is str for row in entries)
        and len({row['path']for row in entries})==len(entries),'release_context_receipt')
    indexed={row['path']:row for row in entries}
    for name in names:
        row=indexed['scripts/'+name];pin={key:row[key]for key in('bytes','sha256')}
        read(root/'scripts'/name,1024**2,pin)
    return root

def main(context=None):
    root=context_root(context)
    helper=runpy.run_path(str(root/'scripts/prepare_app_security_os_cache.py'))
    need=helper['need'];document=helper['document']
    need(type(RELEASE_ID) is int and RELEASE_ID>0 and set(ASSET_IDS)==set(LINEAGE)
         and all(type(v) is int and v>0 for v in ASSET_IDS.values())
         and len(set(ASSET_IDS.values()))==len(ASSET_IDS),'release_unarmed')
    with ExitStack() as stack:
        held=helper['Held'](stack);recipe=helper['load_recipe'](held)
        specs=dict(recipe.lock['artifacts']);extras={};sqlite_source=None
        for group,name in EXTRA_HELPERS.items():
            extra=runpy.run_path(str(root/('scripts/prepare_'+name+'_os_cache.py')))
            gate=stack.enter_context(extra['Held']());lock=extra['load_lock'](gate)
            specs[group]=lock['artifact'];extras[group]=(extra,gate)
            if group=='sqlite-wal':sqlite_source=lock['source']
        cache=root/'pip_packages/security-os-capsule'
        present=[]
        for group in ('security-os',*extras):
            path=cache.parent/(group+'-capsule')
            try:path.lstat()
            except FileNotFoundError:exists=False
            else:exists=True
            present.append(exists)
            if exists:
                if group=='security-os':result=helper['prepare_cache'](path,check_only=True)
                else:result=extras[group][0]['prepare'](path,check_only=True)
                need(result.get('status')=='cache_verified' and result.get('security_accepted') is False,'release_existing_cache')
        if all(present):
            held.check()
            for _,gate in extras.values():gate.check()
            print('All four authentic CI caches verified without download; security acceptance remains false.')
            return
        need(bool(os.environ.get('GH_TOKEN')),'release_token')
        def api(endpoint,maximum,accept='application/vnd.github+json'):
            return recipe.module.command(['/usr/bin/gh','api','--method','GET','-H','Accept: '+accept,
                'repos/'+REPO+'/'+endpoint],seconds=120,maximum=maximum,
                environment={'GH_TOKEN':os.environ['GH_TOKEN']})
        for group,(workflow,name,head) in LINEAGE.items():
            spec=specs[group]
            run=document(api('actions/runs/'+str(spec['run_id'])+'/attempts/1',65536))
            need(all(type(run.get(k)) is int for k in ('id','run_attempt','workflow_id'))
                 and (run.get('id'),run.get('run_attempt'),run.get('workflow_id'),run.get('path'),run.get('head_sha'),
                  run.get('event'),run.get('status'),run.get('conclusion'),run.get('repository',{}).get('id'),
                  run.get('head_repository',{}).get('id'))
                 ==(spec['run_id'],1,workflow,'.github/workflows/'+name,head,'workflow_dispatch','completed','success',1065680631,1065680631),
                 'release_lineage')
        sqlite=specs['sqlite-wal'];source=sqlite_source
        need(source['commit']==LINEAGE['sqlite-wal'][2]
             and source['root_build_receipt_sha256']==SQLITE_ROOT_RECEIPT,'release_sqlite_source')
        artifact=document(api('actions/artifacts/'+str(sqlite['id']),65536))
        need(type(artifact) is dict,'release_sqlite_artifact')
        workflow=artifact.get('workflow_run',{})
        need(type(artifact.get('id')) is int and type(artifact.get('size_in_bytes')) is int
             and type(workflow) is dict and all(type(workflow.get(k)) is int
                 for k in ('id','repository_id','head_repository_id'))
             and artifact.get('id')==sqlite['id'] and artifact.get('size_in_bytes')==sqlite['bytes']
             and artifact.get('name')=='sqlite-wal-package-'+str(sqlite['run_id'])+'-1'
             and artifact.get('digest')=='sha256:'+sqlite['sha256'] and artifact.get('expired') is False
             and artifact.get('expires_at')==SQLITE_ARTIFACT_EXPIRY
             and datetime.now(timezone.utc)<datetime.strptime(SQLITE_ARTIFACT_EXPIRY,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
             and artifact.get('url')=='https://api.github.com/repos/'+REPO+'/actions/artifacts/'+str(sqlite['id'])
             and artifact.get('archive_download_url')=='https://api.github.com/repos/'+REPO+'/actions/artifacts/'+str(sqlite['id'])+'/zip'
             and (workflow.get('id'),workflow.get('repository_id'),workflow.get('head_repository_id'),
                  workflow.get('head_branch'),workflow.get('head_sha'))
                 ==(sqlite['run_id'],1065680631,1065680631,'main',source['commit']),'release_sqlite_artifact')
        release=document(api('releases/'+str(RELEASE_ID),65536))
        need(type(release.get('id')) is int and release.get('id')==RELEASE_ID and release.get('tag_name')==RELEASE_TAG
             and release.get('target_commitish')==RELEASE_TARGET
             and release.get('draft') is False and release.get('prerelease') is True
             and release.get('html_url')=='https://github.com/'+REPO+'/releases/tag/'+RELEASE_TAG,'release_identity')
        assets=release.get('assets');need(type(assets) is list and len(assets)==len(ASSET_IDS),'release_assets')
        need(len({a.get('id') for a in assets})==len(assets) and len({a.get('name') for a in assets})==len(assets),'release_assets')
        for group,identifier in ASSET_IDS.items():
            spec=RETAINED_SQLITE if group=='sqlite-wal' else specs[group]
            rows=[a for a in assets if a.get('id')==identifier]
            need(len(rows)==1,'release_asset_identity');asset=rows[0]
            need(type(asset.get('id')) is int and type(asset.get('size')) is int
                 and asset.get('name')=='app-os-'+group+'.zip' and asset.get('size')==spec['bytes']
                 and asset.get('digest')=='sha256:'+spec['sha256'] and asset.get('state')=='uploaded'
                 and asset.get('url')=='https://api.github.com/repos/'+REPO+'/releases/assets/'+str(identifier),
                 'release_asset_identity')
        # Only new current-UID 0600 files enter the unchanged local ZIP API. All
        # original ZIP, receipt, package, recipe and manifest pins remain in force.
        with tempfile.TemporaryDirectory(prefix='npm-app-os-',dir=os.environ['RUNNER_TEMP']) as temporary:
            paths={}
            for group,identifier in ASSET_IDS.items():
                spec=specs[group]
                endpoint=('actions/artifacts/'+str(spec['id'])+'/zip' if group=='sqlite-wal'
                          else 'releases/assets/'+str(identifier))
                accept='application/vnd.github+json' if group=='sqlite-wal' else 'application/octet-stream'
                raw=api(endpoint,spec['bytes'],accept)
                need(len(raw)==spec['bytes'] and sha256(raw).hexdigest()==spec['sha256'],'release_payload_pin')
                path=Path(temporary)/(group+'.zip');helper['write_new'](path,raw,0o600);paths[group]=path
                held.check()
            cache.parent.mkdir(exist_ok=True)
            result=helper['prepare_cache'](cache,{group:paths[group] for group in recipe.lock['artifacts']});held.check()
            need(result.get('status') in {'cache_prepared','cache_verified'} and result.get('packages')==15
                 and result.get('security_accepted') is False,'release_cache_admission')
            for group,(extra,gate) in extras.items():
                result=extra['prepare'](cache.parent/(group+'-capsule'),paths[group]);gate.check()
                need(result.get('status') in {'cache_prepared','cache_verified'}
                     and result.get('security_accepted') is False,'release_cache_admission')
    print('Exact retained release assets and Root-admitted SQLite CI artifact admitted; security acceptance remains false.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Admit only the exact public shared CI cache assets.')
    parser.add_argument('--context-root',type=Path)
    main(parser.parse_args().context_root)
