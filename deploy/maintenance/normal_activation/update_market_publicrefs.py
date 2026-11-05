"""Finite selected Compose image/revision CAS updates; no runtime or Git action.
All unrelated configuration remains local and is compared only in RAM.
"""
from pathlib import Path
from copy import deepcopy
import argparse,json,os,re,stat
import yaml
STAMPS=('MARKET_PIPELINE_RELEASE_SHA','MARKET_PIPELINE_IMAGE_REVISION')
PAUSE=('MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC','MARKET_CAPTURE_BACKFILL_SOURCE_CODES')
ROLES={'native':{'db','redis','coin-estimator','estimator-snapshot-sender','market-fact-receiver','market-store-adapter'},'web':{'db','redis','market-database','market-capture-account2','market-capture-account1','market-capture-external','market-fact-sync-worker','market-processor','estimator-snapshot-receiver'}}
MAXIMUM=1048576

def need(value,code):
 if not value:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def unique(pairs):
 result={}
 for key,value in pairs:need(key not in result,'duplicate_key');result[key]=value
 return result
class ComposeLoader(yaml.SafeLoader):pass
ComposeLoader.yaml_implicit_resolvers={k:[(tag,rx)for tag,rx in rows if tag not in('tag:yaml.org,2002:bool','tag:yaml.org,2002:timestamp')]for k,rows in yaml.SafeLoader.yaml_implicit_resolvers.items()}
ComposeLoader.add_implicit_resolver('tag:yaml.org,2002:bool',re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'),list('tTfF'))
def mapping(loader,node,deep=False):
 # Explicit duplicates refuse; an ordinary YAML merge followed by an override is allowed.
 keys=[k.value for k,_ in node.value];need(len(keys)==len(set(keys)),'duplicate_yaml_key');loader.flatten_mapping(node)
 return loader.construct_mapping(node,deep=deep)
ComposeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
def document(raw,path):
 value=json.loads(raw,object_pairs_hook=unique)if path.suffix=='.json'else yaml.load(raw,Loader=ComposeLoader)
 need(type(value)is dict and type(value.get('services'))is dict,'compose_services');return value
def read(path):
 need(path.is_absolute()and path.resolve(strict=True)==path,'compose_path');parent=path.parent.stat();need(parent.st_uid==0 and not parent.st_mode&0o022,'compose_parent')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  before=os.fstat(fd);need(stat.S_ISREG(before.st_mode)and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o022 and 0<before.st_size<=MAXIMUM,'compose_identity')
  raw=os.pread(fd,before.st_size+1,0);need(len(raw)==before.st_size and identity(os.fstat(fd))==identity(before)==identity(path.lstat()),'compose_changed')
  return document(raw,path),raw,before
 finally:os.close(fd)
def labels_public(key):return key.startswith(('org.opencontainers.image.','io.gold-trade.'))
def values(value,keys):
 need(value is None or type(value)in(dict,list),'compose_values');out={}
 if type(value)is dict:
  for key in keys:
   if key in value:out[key]=deepcopy(value[key])
 elif type(value)is list:
  for entry in value:
   need(type(entry)is str and '='in entry,'compose_values');key,val=entry.split('=',1)
   if key in keys:out.setdefault(key,[]).append(val)
 return out
def public(doc,service):
 row=doc['services'].get(service);need(row is None or type(row)is dict,'compose_service')
 if row is None:return {'present':False}
 labels=row.get('labels');keys=set(labels or{})if type(labels)is dict else{entry.split('=',1)[0]for entry in labels or[]}
 return {'present':True,'image':row.get('image'),'stamps':values(row.get('environment'),STAMPS),'provenance_labels':values(labels,{k for k in keys if labels_public(k)})}
def put(values_,updates,remove=()):
 need(values_ is None or type(values_)in(dict,list),'compose_values');value={}if values_ is None else deepcopy(values_)
 if type(value)is dict:
  for key in remove:value.pop(key,None)
  value.update(updates);return value
 seen=set();out=[]
 for entry in value:
  need(type(entry)is str and '='in entry,'compose_values');key=entry.split('=',1)[0]
  if key in remove:continue
  out.append(key+'='+updates[key]if key in updates else entry);seen.add(key)
 out.extend(key+'='+val for key,val in updates.items()if key not in seen);return out
def sanitize(doc,items):
 result=deepcopy(doc)
 for item in items:
  name=item['service'];old=result['services'].get(name)
  if old is None:continue
  row=deepcopy(old);result['services'][name]=row;row.pop('image',None)
  for field,allowed in(('environment',set(STAMPS)if item['kind']=='market'else set()),('labels',set(item.get('set_labels',{}))|set(item.get('remove_labels',[])))):
   if field not in row:continue
   value=row[field]
   if value is None:row.pop(field,None);continue
   if type(value)is dict:value={k:v for k,v in value.items()if k not in allowed}
   elif type(value)is list:value=[entry for entry in value if entry.split('=',1)[0]not in allowed]
   else:need(False,'compose_values')
   if value:row[field]=value
   else:row.pop(field,None)
  if not row:result['services'].pop(name)
 return result
def transform(doc,items,site):
 need(site in ROLES and type(items)is list and 1<=len(items)<=6 and len({i['service']for i in items})==len(items),'closed_scope')
 result=deepcopy(doc)
 for item in items:
  name=item['service'];need(name in ROLES[site]and item['kind']in('market','store')and(item['kind']=='store')==(name in('db','redis','market-database')),'role_site_kind')
  need(public(doc,name)==item['expected_public'],'public_reference_CAS_changed')
  need(re.fullmatch('sha256:[a-f0-9]{64}',item['new_image'])is not None,'new_image')
  row=deepcopy(result['services'].get(name,{}));need(type(row)is dict,'compose_service');result['services'][name]=row;row['image']=item['new_image']
  setlabels=item.get('set_labels',{});remove=item.get('remove_labels',[])
  need(type(setlabels)is dict and type(remove)is list and all(labels_public(k)for k in set(setlabels)|set(remove))and not set(setlabels)&set(remove),'public_label_scope')
  need(all(type(v)is str and 0<len(v)<=1024 and '\n'not in v and '\r'not in v for v in setlabels.values()),'public_label_value')
  if setlabels or remove:row['labels']=put(row.get('labels'),setlabels,remove)
  if item['kind']=='market':
   revision=item['revision'];need(re.fullmatch('[a-f0-9]{40}',revision)is not None,'public_revision')
   need(setlabels.get('org.opencontainers.image.revision')==revision,'market_provenance_revision')
   existing=set(public(doc,name).get('provenance_labels',{}))
   need(existing<=set(setlabels)|set(remove),'market_provenance_keys_unaccounted')
   row['environment']=put(row.get('environment'),{k:revision for k in STAMPS})
  else:need('revision'not in item and not setlabels and not remove,'store_image_only')
 need(sanitize(doc,items)==sanitize(result,items),'nonpublic_configuration_changed')
 # These controls are never an update operand; retain their exact presence/value/order.
 for item in items:
  a=doc['services'].get(item['service'],{}).get('environment');b=result['services'][item['service']].get('environment')
  need(values(a,PAUSE)==values(b,PAUSE),'replay_controls_changed')
 return result

def update(plan):
 need(os.geteuid()==0 and plan['schema']=='selected_existing_compose_publicrefs_update/1'and re.fullmatch('[a-f0-9]{32}',plan['operation_id'])is not None,'Root_scope')
 path=Path(plan['path']);doc,raw,original=read(path);result=transform(doc,plan['items'],plan['site']);payload=(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode();need(len(payload)<=MAXIMUM,'compose_bound')
 # JSON is a YAML subset; the document type/meaning is preserved without installing dependencies.
 need(document(payload,path)==result,'serialized_semantics_changed')
 current,current_raw,observed=read(path);need(identity(observed)==identity(original)and current_raw==raw,'compose_CAS_changed')
 temporary=path.with_name(path.name+'.security-'+plan['operation_id']+'.partial');fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 try:
  os.fchmod(fd,stat.S_IMODE(original.st_mode));os.fchown(fd,original.st_uid,original.st_gid)
  with os.fdopen(fd,'wb',closefd=False)as stream:stream.write(payload);stream.flush();os.fsync(fd)
 finally:os.close(fd)
 current,current_raw,observed=read(path);need(identity(observed)==identity(original)and current_raw==raw,'compose_CAS_changed')
 os.replace(temporary,path);directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(directory)
 finally:os.close(directory)
 actual,_,_=read(path);need(actual==result,'compose_write_verification')
 return {'status':'EXISTING_SELECTED_PUBLICREFS_UPDATED','path':str(path),'public':[{'service':i['service'],**public(actual,i['service'])}for i in plan['items']],'unrelated_configuration_preserved':True,'private_values_or_hashes_emitted':False,'Docker_systemd_Git_action':False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);a=p.parse_args();os.umask(0o077)
 try:
  with a.plan.open('rb')as f:raw=f.read(65537)
  need(len(raw)<=65536,'plan_bound');plan=json.loads(raw,object_pairs_hook=unique);print(json.dumps(update(plan),sort_keys=True));return 0
 except Exception as error:
  code=str(error)if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(error))else'publicrefs_unknown'
  print(json.dumps({'status':'REFUSED_OR_UNKNOWN','code':code,'automatic_retry':False}));return 1
if __name__=='__main__':raise SystemExit(main())
