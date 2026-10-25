"""Fixed App/Web image-store RAM accounting; never a daemon heap quota."""
from hashlib import sha256
from pathlib import Path
import json,os,re,time

BASE_RAM=2216689664
CLIENT_RAM=512*1024**2
METADATA_RAM=256*1024**2
RAM_FLOOR=BASE_RAM+CLIENT_RAM+METADATA_RAM
TAR_BYTES=452719104
EXPANDED_BYTES=452635136
DISK_BUDGET=2*TAR_BYTES+2*EXPANDED_BYTES
TAR_SHA='e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67'
ENGINE_COMMIT='0367cbe'
CONTAINERD_COMMIT='b7a25261e029d68978bbf290df0a62fc819b3c51'
ROOTS={'content':'/var/lib/containerd/io.containerd.content.v1.content',
       'snapshot':'/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs'}
EVIDENCE={
 'resource-inner-inventory.json':(9834,'31e09bf729d2e99577bbb734a1e8d892ce91b73dda66c2f0bd667b9b3b7a3e39'),
 'resource-backend.json':(719,'249748a517918532964798e8307fa03e760430c37174c60fa17423c0dc2cd461'),
 'resource-storage.json':(1705,'20db7653964b8e1940c311d214f7bc7b9042cd3a47e6bbf36a7d6be30f5905e9'),
 'resource-plugin-roots.json':(207,'7c3efa18bf1030c28b026790b7c60f9000906ab466787f570f33d35a3d4913d3')}
CONTRACT={'schema':'canonical_app_web_resource_contract/1','archive_sha256':TAR_SHA,
    'archive_bytes':TAR_BYTES,'expanded_layer_bytes':EXPANDED_BYTES,'prospective_disk_bytes':DISK_BUDGET,
    'base_ram_bytes':BASE_RAM,'aggregate_client_memory_max_bytes':CLIENT_RAM,
    'metadata_stream_allowance_bytes':METADATA_RAM,'ram_floor_bytes':RAM_FLOOR,
    'daemon_heap_quota_claimed':False,'native_policy_changed':False,'evidence_sha256':{k:v[1] for k,v in EVIDENCE.items()}}
CONTRACT_SHA=sha256((json.dumps(CONTRACT,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()

def need(ok,code='app_resource_contract'):
    if not ok:raise RuntimeError(code)

def admit_details(value):
    expected={'resource_contract_sha256':CONTRACT_SHA,'resource_ram_floor_bytes':RAM_FLOOR,
        'aggregate_client_memory_max_bytes':CLIENT_RAM,'metadata_stream_allowance_bytes':METADATA_RAM,
        'actual_tmpfs_payload_write_bytes':0,'daemon_heap_quota_claimed':False,'resource_contract_accepted':False}
    need(type(value) is dict and all(k in value and type(value[k]) is type(v) and value[k]==v for k,v in expected.items()))
    return expected

def scope_arguments(phase,nonce):
    need(phase in {'snapshot','admit-existing-web','load-web','stream-web'}
        and type(nonce) is str and re.fullmatch('[a-f0-9]{32}',nonce))
    return ['--unit=market-images-web-'+phase+'-'+nonce,'--property=CPUQuota=100%',
        '--property=MemoryMax=512M','--property=MemorySwapMax=0','--property=TasksMax=64',
        '--property=RuntimeMaxSec=900','--property=TimeoutStopSec=5']

def check_scope(phase,nonce):
    scope_arguments(phase,nonce)
    with Path('/proc/self/cgroup').open('rb') as source:raw=source.read(65537)
    need(0<len(raw)<=65536 and raw.startswith(b'0::/')
        and raw.decode().strip().endswith('/market-images-web-'+phase+'-'+nonce+'.scope'))
    path=raw.decode().strip()[4:];need('..' not in path.split('/'))
    folder=Path('/sys/fs/cgroup')/path
    values={}
    for name in ('memory.max','memory.swap.max','pids.max','cpu.max'):
        with (folder/name).open('rb') as source:raw=source.read(1025)
        need(len(raw)<=1024);values[name]=raw.decode().strip()
    quota,period=values['cpu.max'].split()
    need(values['memory.max']==str(CLIENT_RAM) and values['memory.swap.max']=='0'
        and values['pids.max']=='64' and quota!='max' and 0<int(quota)<=int(period),'scope_limits')

def project_roots(raw):
    """Only the exact public selected ctr Type/ID/Exports.root records."""
    need(type(raw) is bytes and len(raw)<=65536);rows=[];row=None;exports=False
    for line in raw.decode('utf-8').splitlines():
        need(len(line.encode())<=8192);line=line.strip()
        if not line:continue
        if line.startswith('Type:'):
            if row is not None:rows.append(row)
            need(len(rows)<2);row={'type':line[5:].strip()};exports=False
        elif line.startswith('ID:'):
            need(row is not None and 'id' not in row);row['id']=line[3:].strip();exports=False
        elif line=='Exports:':need(row is not None and 'root' not in row);exports=True
        elif line.startswith(('Platforms:','Capabilities:')):exports=False
        else:
            match=re.fullmatch(r'root[ \t]+([^ \t]+)',line)
            need(exports and row is not None and 'root' not in row and match is not None);row['root']=match[1]
    if row is not None:rows.append(row)
    expected={('io.containerd.content.v1','content'):ROOTS['content'],('io.containerd.snapshotter.v1','overlayfs'):ROOTS['snapshot']}
    need(len(rows)==2 and all(set(r)=={'type','id','root'} for r in rows)
        and len({(r['type'],r['id']) for r in rows})==2
        and all(expected.get((r['type'],r['id']))==r['root'] for r in rows))

class WebResource:
    def __init__(self,ctx,nonce,phase):
        self.ctx,self.nonce,self.phase=ctx,nonce,phase;ctx.tmpfs_payload_write_bytes=0;M=ctx.engine;records={}
        for name,(size,pin) in EVIDENCE.items():
            raw,check=M.seal(ctx.stack,ctx.root/'evidence'/name,pin,131072);need(len(raw)==size)
            ctx.checks.append(check);records[name]=M.document(raw)
        inventory=records['resource-inner-inventory.json'];need(inventory['returncode']==0 and inventory['security_accepted'] is False)
        value=inventory['result'];need(value['archive_sha256']==TAR_SHA and type(value['archive_bytes']) is int
            and value['archive_bytes']==TAR_BYTES and value['logical_layers']==33 and len(value['layers'])==33
            and value['image_load_requested'] is value['resource_contract_accepted'] is value['security_accepted'] is False)
        need(sum(r['stream_bytes'] for r in value['layers'])==EXPANDED_BYTES)
        proposal=max(128*1024**2+4096*(r['entries']+r['directories']+r['pax_records']+r['xattr_records'])
            +16*(r['path_bytes']+r['pax_bytes']+r['xattr_bytes']) for r in value['layers'])
        need(0<proposal<=METADATA_RAM)
        backend=records['resource-backend.json'];need(backend['daemon_environment_read'] is False
            and backend['server']['Version']=='29.9.2' and backend['server']['GitCommit']==ENGINE_COMMIT
            and backend['store']=={'driver':'overlayfs','driver_status':[['driver-type','io.containerd.snapshotter.v1']],'root':'/var/lib/docker'})
        roots=records['resource-plugin-roots.json'];need(roots['client_success'] is True
            and roots['daemon_config_or_environment_read'] is roots['raw_metadata_disclosed'] is False
            and roots['public_plugin_roots']=={'content_root_matches_expected':True,'overlayfs_root_matches_expected':True})
        storage=records['resource-storage.json'];need(storage['public_metadata_only'] is True)
        self.paths=storage['paths'];need(len(self.paths)==8 and {r['path'] for r in self.paths}==
            {'/','/tmp','/var/lib/docker','/var/lib/containerd',ROOTS['content'],ROOTS['content']+'/ingest',ROOTS['snapshot'],ROOTS['snapshot']+'/snapshots'})
        root=next(r for r in self.paths if r['path']=='/')
        need(all(r['device']==root['device'] and r['filesystem_id']==root['filesystem_id'] for r in self.paths if r['path']!='/tmp'))
        self.check()
    def check(self):
        check_scope(self.phase,self.nonce)
        for row in self.paths:
            path=Path(row['path']);s=path.lstat();fs=os.statvfs(path)
            need(path.resolve(strict=True)==path and [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid]==
                [row[k] for k in ('device','inode','mode','uid','gid')] and fs.f_fsid==row['filesystem_id'])
    def verify_backend(self):
        ctx=self.ctx;M=ctx.engine
        store=M.store_view(ctx);need(store['root']=='/var/lib/docker' and store['driver']=='overlayfs' and store['version']=='29.9.2')
        template='{"version":{{json .Server.Version}},"commit":{{json .Server.GitCommit}},"containerd":{{range .Server.Components}}{{if eq .Name "containerd"}}{"version":{{json .Version}},"commit":{{json (index .Details "GitCommit")}}}{{end}}{{end}}}'
        version=M.document(M.command(ctx,[*M.DOCKER,'version','--format',template],maximum=8192))
        need(version=={'version':'29.9.2','commit':ENGINE_COMMIT,'containerd':{'version':'v2.4.2','commit':CONTAINERD_COMMIT}})
        project_roots(M.command(ctx,['/usr/bin/ctr','--address','/run/containerd/containerd.sock','plugins','ls','--detailed',
            'type==io.containerd.content.v1,id==content','type==io.containerd.snapshotter.v1,id==overlayfs'],maximum=65536))
        self.check()
    def verify_lifetime(self):
        ctx=self.ctx;M=ctx.engine;unit='market-images-web-'+self.phase+'-'+self.nonce+'.scope'
        raw=M.command(ctx,['/usr/bin/systemctl','show',unit,
            '--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],maximum=4096)
        lines=raw.decode().splitlines();need(len(lines)==4 and all('=' in row for row in lines))
        values=dict(row.split('=',1) for row in lines)
        need(set(values)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
            and values['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and values['TimeoutStopUSec']=='5s'
            and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes')
    def capacity(self,additional=0):
        ctx=self.ctx;M=ctx.engine;ctx.check()
        need(type(ctx.budget) is int and ctx.budget>=0 and type(additional) is int and additional>=0
            and type(ctx.tmpfs_payload_write_bytes) is int and ctx.tmpfs_payload_write_bytes>=0)
        budget=max(DISK_BUDGET,ctx.budget,additional)
        need(M.identity(Path(ctx.baseline['store']['root']).lstat())[:5]==ctx.baseline['store']['identity'],'store_root_changed')
        need(M.temp_metadata()==ctx.baseline['temporary_storage'],'temporary_storage_changed')
        for path,floor in ((ctx.baseline['store']['root'],8*1024**3),('/',2*1024**3),('/tmp',2*1024**3)):
            fs=os.statvfs(path);need(fs.f_bavail*fs.f_frsize>=floor+M.MARGIN+M.LIMIT+budget,'capacity_floor')
        # This exact backend writes blob/snapshot payload to the proven disk
        # roots. No whole-payload tmpfs copy is charged. Any other destination
        # or backend refuses rather than assuming that it has zero RAM cost.
        need(M.memory_available()>=RAM_FLOOR+ctx.tmpfs_payload_write_bytes,'temporary_memory_floor')
        for row in ctx.baseline.get('daemon',{}).get('files',[]):need(M.identity(Path(row['path']).lstat())==row['identity'],'daemon_unit_metadata_changed')
    def details(self):
        return {'resource_contract_sha256':CONTRACT_SHA,'resource_ram_floor_bytes':RAM_FLOOR,
            'aggregate_client_memory_max_bytes':CLIENT_RAM,'metadata_stream_allowance_bytes':METADATA_RAM,
            'actual_tmpfs_payload_write_bytes':self.ctx.tmpfs_payload_write_bytes,'daemon_heap_quota_claimed':False,'resource_contract_accepted':False}
