# Fixed public CI cache bytes, explicitly approved and independently read back.
# No rebuild, mutable tag fallback, cache healing or deployment authorization.
import os,runpy,tempfile
from contextlib import ExitStack
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
    'sqlite-wal':(397322674, 'sqlite-wal-package.yml', 'a32f0e187cea7aa11c0fac2977d31f31315093da'),
    'tar-acl':(397517752, 'tar-acl-descriptor-package.yml', '81ff65c9c9238a4797dc2824c5c51d274afdd6bd'),
    'coreutils-acl':(398051843, 'coreutils-acl-descriptor-package.yml', 'aed063887607a7ee8e219f7e99b14c4761fccdaf'),
}
REPO = 'mohsenbarari/trading_bot'
ROOT = Path(__file__).resolve().parents[1]

def main():
    helper=runpy.run_path(str(ROOT/'scripts/prepare_app_security_os_cache.py'))
    need=helper['need'];document=helper['document']
    need(type(RELEASE_ID) is int and RELEASE_ID>0 and set(ASSET_IDS)==set(LINEAGE)
         and all(type(v) is int and v>0 for v in ASSET_IDS.values())
         and len(set(ASSET_IDS.values()))==len(ASSET_IDS),'release_unarmed')
    with ExitStack() as stack:
        held=helper['Held'](stack);recipe=helper['load_recipe'](held)
        specs=dict(recipe.lock['artifacts']);extras={}
        for group,name in EXTRA_HELPERS.items():
            extra=runpy.run_path(str(ROOT/('scripts/prepare_'+name+'_os_cache.py')))
            gate=stack.enter_context(extra['Held']());lock=extra['load_lock'](gate)
            specs[group]=lock['artifact'];extras[group]=(extra,gate)
        cache=ROOT/'pip_packages/security-os-capsule'
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
        release=document(api('releases/'+str(RELEASE_ID),65536))
        need(type(release.get('id')) is int and release.get('id')==RELEASE_ID and release.get('tag_name')==RELEASE_TAG
             and release.get('target_commitish')==RELEASE_TARGET
             and release.get('draft') is False and release.get('prerelease') is True
             and release.get('html_url')=='https://github.com/'+REPO+'/releases/tag/'+RELEASE_TAG,'release_identity')
        assets=release.get('assets');need(type(assets) is list and len(assets)==len(ASSET_IDS),'release_assets')
        need(len({a.get('id') for a in assets})==len(assets) and len({a.get('name') for a in assets})==len(assets),'release_assets')
        for group,identifier in ASSET_IDS.items():
            spec=specs[group]
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
                raw=api('releases/assets/'+str(identifier),spec['bytes'],'application/octet-stream')
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
    print('Exact retained security release assets admitted; security acceptance remains false.')

if __name__=='__main__':main()
