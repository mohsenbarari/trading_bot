"""Fixed public R2 documentation facts from the authenticated retained flat TAR.

No filesystem extraction/lookup, image code, source-flags review or policy-layer
causality is claimed. This file cannot mint the later exact-absence authority.
"""
from pathlib import PurePosixPath
from hashlib import sha256
import time
MAX_POLICY=65536
MAX_DOC=1024**2
PUBLIC_FAILURE_CODES=frozenset(('policy_contract','policy_parent_alias_or_type','policy_implicit_parent_ambiguous','policy_target_alias_or_type','policy_text'))
TARGETS=('/etc/dpkg/dpkg.cfg.d/docker','/usr/share/doc/util-linux/examples/getopt-example.bash','/usr/share/doc/libacl1/copyright','/usr/share/doc/libacl1/changelog.gz','/usr/share/doc/libacl1/changelog.Debian.gz')
def need(ok,code):
 if not ok:raise RuntimeError(code)
def collect(A,stream,size,*,image_id,source_commit,export_sha256,check,end):
 need(callable(check)and type(end)in(int,float)and 0<end-time.monotonic()<=600,'policy_contract')
 source=A.Source(stream,size,check,end)
 try:
  source.verify(export_sha256);source.digest_pin=export_sha256
  rows,targets,physical=A.index(source);observations=[]
  for path in TARGETS:
   source.check();parts=PurePosixPath(path).parts;prefix='/';first_missing=None;parents=[]
   for part in parts[1:-1]:
    candidate=(prefix.rstrip('/')+'/'+part);row=rows.get(candidate)
    if row is None:first_missing=candidate;break
    need(row['type']=='directory','policy_parent_alias_or_type');parents.append(row);prefix=candidate
   row=rows.get(path)
   if first_missing is not None:need(row is None,'policy_implicit_parent_ambiguous')
   if row is None:
    observations.append({'path':path,'status':'ABSENT_FROM_AUTHENTICATED_FLAT_TAR','first_missing_archive_path':first_missing or path,'existing_explicit_directory_prefix':prefix,'explicit_directory_members':parents,'filesystem_ENOENT_measured':False});continue
   need(row['type']=='regular'and targets.get(path)==path and row['uid']==row['gid']==0 and not row['mode']&0o022,'policy_target_alias_or_type')
   maximum=MAX_POLICY if path==TARGETS[0]else MAX_DOC
   raw=A.member_bytes(source,rows,targets,path,maximum)
   observation={'path':path,'status':'REGULAR_PUBLIC_FILE_OBSERVED','bytes':len(raw),'sha256':sha256(raw).hexdigest(),'archive_member':row,'explicit_directory_members':parents}
   if path==TARGETS[0]:
    value=raw.decode('utf-8');need(all(ord(c)>=32 or c in'\t\r\n'for c in value),'policy_text');observation['public_policy_text']=value
   observations.append(observation)
  source.check()
  return {'schema':'App_R2_public_documentation_policy_flat_facts/1','image_id':image_id,'source_commit':source_commit,'flat_export_sha256':export_sha256,'flat_export_bytes':size,'observations':observations,'physical_headers_examined':physical,'fixed_public_targets_only':True,'archive_member_metadata_is_filesystem_full9':False,'policy_origin_layer':None,'policy_layer_attribution_completed':False,'distribution_filtered_absence_authority_created':False,'source_flags_review_created':False,'filesystem_extracted':False,'inspected_code_executed':False,'production_paths_or_data_used':False,'security_accepted':False}
 finally:source.close()
if __name__=='__main__':print({'status':'UNARMED','collection_performed':False,'security_accepted':False})
