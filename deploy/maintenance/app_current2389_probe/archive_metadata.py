"""Public Config5 and37 layer metadata; no extraction/native/LOAD."""
from hashlib import sha256
import gzip,json,os,resource,tarfile,time
MAX_LAYER=512*1024**2
MAX_PACKET=1024**2
MAX_PAX_RECORDS=4096
MAX_PATH=4096
MAX_ENTRIES=1000000
MAX_LAYERS=37
MAX_OUTPUT=131072
FIELDS=('entries','directories','path_bytes','path_bytes_max','pax_bytes','pax_bytes_max','pax_records','pax_records_max','xattr_bytes','xattr_bytes_max','xattr_records','xattr_records_max','stream_bytes')
def need(ok):
    if not ok:raise RuntimeError('layer_metadata_refused')

def pax(raw):
    """Parse bounded PAX records in RAM; retain nothing in public output."""
    result={};offset=0;records=0
    while offset<len(raw):
        space=raw.find(b' ',offset,min(len(raw),offset+16));need(space>offset)
        token=raw[offset:space];need(token.isdigit() and len(token)<=10)
        length=int(token);need(space-offset+3<=length<=len(raw)-offset)
        record=raw[space+1:offset+length];need(record.endswith(b'\n') and b'=' in record)
        key,value=record[:-1].split(b'=',1);need(key and b'\x00' not in key and b'\n' not in key and not key.startswith(b'GNU.sparse.'))
        records+=1;need(records<=MAX_PAX_RECORDS)
        result[key]=value
        need(len(result)<=MAX_PAX_RECORDS and sum(len(k)+len(v) for k,v in result.items())<=MAX_PACKET)
        offset+=length
    need(offset==len(raw));return result

def scan_layer(stream, maximum=MAX_LAYER, end=None):
    """One raw TAR layer; constant counters plus <=1MiB bounded PAX maps."""
    need(type(maximum) is int and 1024<=maximum<=MAX_LAYER)
    end=time.monotonic()+120 if end is None else end
    need(type(end) in (int,float) and 0<end-time.monotonic()<=120)
    out=dict.fromkeys(FIELDS,0);global_pax={};local_pax={};long={}
    def read(count):
        need(type(count) is int and 0<=count<=65536 and time.monotonic()<end)
        block=stream.read(count);need(type(block) is bytes and len(block)<=count)
        out['stream_bytes']+=len(block);need(out['stream_bytes']<=maximum);return block
    def exact(count):
        need(0<=count<=MAX_PACKET);chunks=[]
        while count:
            block=read(min(65536,count));need(block);chunks.append(block);count-=len(block)
        return b''.join(chunks)
    def discard(count):
        need(type(count) is int and 0<=count<=maximum-out['stream_bytes'])
        while count:
            block=read(min(65536,count));need(block);count-=len(block)
    while True:
        header=exact(512)
        if header==bytes(512):
            need(exact(512)==bytes(512) and not local_pax and not long)
            while block:=read(min(65536,maximum-out['stream_bytes']+1)):
                need(not any(block))
            return out
        try:item=tarfile.TarInfo.frombuf(header,'utf-8','surrogateescape')
        except (tarfile.TarError,ValueError,UnicodeError,OverflowError):raise RuntimeError('layer_metadata_refused') from None
        need(type(item.size) is int and 0<=item.size<=maximum-out['stream_bytes'])
        if item.type in (tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK):
            need(item.size<=MAX_PACKET);raw=exact(item.size);discard((-item.size)%512)
            if item.type in (tarfile.XHDTYPE,tarfile.XGLTYPE):
                values=pax(raw)
                target=global_pax if item.type==tarfile.XGLTYPE else local_pax
                if item.type==tarfile.XGLTYPE:
                    for key,value in values.items():
                        if value:target[key]=value
                        else:target.pop(key,None)
                else:target.update(values)
                need(len(target)<=MAX_PAX_RECORDS and sum(len(k)+len(v) for k,v in target.items())<=MAX_PACKET)
            else:
                raw=raw.rstrip(b'\x00');need(0<len(raw)<=MAX_PATH)
                long[b'path' if item.type==tarfile.GNUTYPE_LONGNAME else b'linkpath']=raw
            continue
        need(item.type in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE,tarfile.SYMTYPE,
            tarfile.LNKTYPE,tarfile.CHRTYPE,tarfile.BLKTYPE,tarfile.FIFOTYPE))
        effective=dict(global_pax)
        for key,value in local_pax.items():
            if value:effective[key]=value
            else:effective.pop(key,None)
        need(len(effective)<=MAX_PAX_RECORDS and sum(len(k)+len(v) for k,v in effective.items())<=MAX_PACKET)
        strings={b'path':item.name.encode('utf-8','surrogateescape'),b'linkpath':item.linkname.encode('utf-8','surrogateescape'),
            b'uname':item.uname.encode('utf-8','surrogateescape'),b'gname':item.gname.encode('utf-8','surrogateescape')}
        strings.update(long)
        for key in strings:
            if key in effective:strings[key]=effective[key]
        need(all(len(value)<=MAX_PATH and b'\x00' not in value for value in strings.values()))
        size=item.size
        if b'size' in effective:
            need(effective[b'size'].isdigit() and len(effective[b'size'])<=12);size=int(effective[b'size'])
        need(0<=size<=maximum-out['stream_bytes'])
        path_bytes=sum(map(len,strings.values()));pax_bytes=sum(len(k)+len(v) for k,v in effective.items())
        xattrs={k:v for k,v in effective.items() if k.startswith((b'SCHILY.xattr.',b'LIBARCHIVE.xattr.'))}
        xbytes=sum(len(k)+len(v) for k,v in xattrs.items())
        out['entries']+=1;need(out['entries']<=MAX_ENTRIES);out['directories']+=int(item.isdir())
        for prefix,value in (('path_bytes',path_bytes),('pax_bytes',pax_bytes),('pax_records',len(effective)),
                ('xattr_bytes',xbytes),('xattr_records',len(xattrs))):
            out[prefix]+=value;out[prefix+'_max']=max(out[prefix+'_max'],value)
        local_pax={};long={};discard(size);discard((-size)%512)

class Slice:
    def __init__(self, fd, start, size):self.fd,self.start,self.size,self.offset=fd,start,size,0
    def read(self,count):
        need(type(count) is int and count>=0)
        raw=os.pread(self.fd,min(count,65536,self.size-self.offset),self.start+self.offset);self.offset+=len(raw);return raw

def public_defaults(config,control,default_five):
 names=('PATH','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256');env=config.get('config',{}).get('Env');need(type(env)is list and len(env)<=64);observed={}
 for line in env:
  need(type(line)is str and '='in line);key,value=line.split('=',1)
  if key in names:need(key not in observed);observed[key]=value
 need(set(observed)==set(names)and observed==default_five['public_defaults']and default_five['image_id']==control['image_id'])
 public={'schema':'current2389_actual_public_default_five/1','image_id':control['image_id'],'source_commit':control['source_commit'],'public_defaults':observed,'derived_from_actual_authenticated_original_Config':True,'private_configuration_read':False,'security_accepted':False}
 return public

def collect(ctx,source,size,config,proof,control,default_five):
 need(size==476681728 and proof['archive_sha256']=='5f98cb3c408e8b58e8ed576ac8cf459e25cb409a87f4c559fed9e890ea708194'and len(control['rootfs']['Layers'])==37)
 public=public_defaults(config,control,default_five)
 end=min(ctx.end,time.monotonic()+120);reader=ctx.T.ArchiveReader(source,size,end);members,_=ctx.T.inventory(reader);manifest='blobs/sha256/'+proof['manifest_id'][7:];offset,length=members[manifest];need(0<length<=2*1024**2);value=json.loads(os.pread(source.fileno(),length,offset));descriptors=value['layers'];need(type(descriptors)is list and len(descriptors)==37);rows=[]
 for index,desc in enumerate(descriptors):
  name='blobs/sha256/'+desc['digest'][7:];offset,length=members[name];need(length==desc['size']and desc['mediaType']in ctx.D.MEDIA);part=Slice(source.fileno(),offset,length)
  if ctx.D.MEDIA[desc['mediaType']]:
   with gzip.GzipFile(fileobj=part,mode='rb')as expanded:counts=scan_layer(expanded,end=end)
  else:counts=scan_layer(part,end=end)
  rows.append({'layer_index':index,**counts});ctx.check()
 need(sum(r['stream_bytes']for r in rows)==476590080 and time.monotonic()<end);source.seek(0);need(ctx.C.stream_digest(source,size)==proof['archive_sha256']);source.seek(0);ctx.check()
 result={'schema':'canonical_app_layer_metadata_inventory/1','status':'counted_not_admitted','archive_bytes':size,'archive_sha256':proof['archive_sha256'],'logical_layers':37,'layers':rows,'limits':{'layer_bytes':MAX_LAYER,'metadata_packet_bytes':MAX_PACKET,'pax_records':MAX_PAX_RECORDS,'path_bytes':MAX_PATH,'entries_per_layer':MAX_ENTRIES,'logical_layers':37},'python_peak_rss_bytes':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024,'image_load_requested':False,'resource_contract_accepted':False,'security_accepted':False}
 wrapper={'returncode':0,'security_accepted':False,'result':result};need(len((json.dumps(wrapper)+'\n').encode())<=MAX_OUTPUT);return public,wrapper

CODES=frozenset(('layer_metadata_refused',))
