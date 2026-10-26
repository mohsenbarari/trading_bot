"""Strict public wire admission for the actual F79 rebase proof, not activation."""
import re

SCHEMA='normal_market_rebased_public_sources/1'
OLD_CID='6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
REBASE_SHA='2d567b9d105dbf21c490ad47587cb7c7352969ce0f7be8673814a408790dd2af'
RESOLVER_SHA='24f0f2e49f2052c1425e60cbb72bfd24bc5d14af44a9646751effb0c4d019236'
CODES=frozenset(('rebase_wire','rebase_lineage','rebase_archive_binding','rebase_metadata','rebase_old_observation'))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def full9(value):
 return type(value) is list and len(value)==9 and all(type(v) is int for v in value) and value[0]>0 and value[1]>0 and value[6]>=0
def admit(R,value,lineage,spec,admitted,association,old_rootfs):
 fields={'schema','composition_model','baseline_image_id','baseline_rootfs','candidate_config_id','candidate_rootfs','lineage',
  'archive_bytes','archive_sha256','tar_identity','source_identities','association','resolver_sha256','candidate_effective_metadata',
  'old_fixed_observation','physical_graph_closed','ancestor_metadata_equal_in_ram','configuration_values_disclosed','security_accepted','activation_authority'}
 need(type(value) is dict and set(value)==fields and value['schema']==SCHEMA and value['composition_model']=='authenticated_rebase_and_profile_copy'
  and value['physical_graph_closed'] is True and value['ancestor_metadata_equal_in_ram'] is True
  and value['configuration_values_disclosed'] is False and value['security_accepted'] is False and value['activation_authority'] is False,'rebase_wire')
 need(R.same(value['lineage'],lineage) and value['baseline_image_id']==R.BASELINE and R.same(value['baseline_rootfs'],R.OLD_ROOTFS)
  and R.same(old_rootfs,R.OLD_ROOTFS) and value['candidate_config_id']==lineage['candidate_config_id']==spec['candidate_config_id']
  and R.same(value['candidate_rootfs'],lineage['candidate_rootfs']) and R.same(value['candidate_rootfs'],spec['candidate_rootfs'])
  and not R.same(value['candidate_rootfs'][:8],R.OLD_ROOTFS),'rebase_lineage')
 need(R.same(value['association'],association) and association['producer_sha256']==REBASE_SHA and value['resolver_sha256']==RESOLVER_SHA
  and type(value['archive_bytes']) is int and value['archive_bytes']==spec['archive']['uncompressed_bytes']==admitted['archive_bytes']
  and value['archive_sha256']==spec['tar_sha256']==admitted['archive_sha256']==association['archive_sha256']
  and full9(value['tar_identity']) and value['tar_identity'][2:7]==[0o100600,0,0,1,value['archive_bytes']]
  and R.same(value['tar_identity'],admitted['tar_identity']) and R.same(value['source_identities'],admitted['source_identities']),'rebase_archive_binding')
 identities=value['source_identities'];need(type(identities) is dict and len(identities)==3,'rebase_archive_binding')
 kinds=set()
 for digest,row in identities.items():
  need(type(digest) is str and re.fullmatch('sha256:[a-f0-9]{64}',digest) and type(row) is dict,'rebase_archive_binding')
  kind=row.get('kind');need(type(kind) is str and kind not in kinds,'rebase_archive_binding');kinds.add(kind)
  if kind=='Docker_config_identity':need(set(row)=={'kind'} and digest==spec['candidate_config_id'],'rebase_archive_binding')
  else:
   media={'OCI_manifest_to_config':'application/vnd.oci.image.manifest.v1+json','OCI_index_to_manifest_to_config':'application/vnd.oci.image.index.v1+json'}
   need(kind in media and set(row)=={'kind','media_type','size'} and row['media_type']==media[kind]
    and type(row['size']) is int and 0<row['size']<=2*1024**2,'rebase_archive_binding')
 need(kinds=={'Docker_config_identity','OCI_manifest_to_config','OCI_index_to_manifest_to_config'},'rebase_archive_binding')
 expected=value['candidate_effective_metadata'];need(type(expected) is dict and set(expected)==set(R.FILES)|set(R.ANCESTORS),'rebase_metadata')
 for path,row in expected.items():
  need(type(row) is dict,'rebase_metadata')
  if path in R.FILES:
   length,pin=R.FILES[path];need(set(row)=={'kind','bytes','sha256','metadata'} and row['kind']=='file'
    and type(row['bytes']) is int and row['bytes']==length and row['sha256']==pin
    and R.same(row['metadata'],[0o644,10001,10001,[]]),'rebase_metadata')
  else:
   uid=0 if path=='/' else 10001
   need(set(row)=={'kind','metadata'} and row['kind']=='directory' and R.same(row['metadata'],[0o755,uid,uid,[]]),'rebase_metadata')
 old=value['old_fixed_observation'];need(type(old) is dict and set(old)=={'schema','container_id','image_id','runtime','files','ancestors','metadata_equal_in_ram'}
  and old['schema']=='f79_rebased_old_fixed_fd/1' and old['container_id']==OLD_CID and old['image_id']==R.BASELINE
  and old['metadata_equal_in_ram'] is True and type(old['files']) is dict and set(old['files'])==set(R.FILES)
  and type(old['ancestors']) is dict and set(old['ancestors'])==set(R.ANCESTORS),'rebase_old_observation')
 runtime=old['runtime'];need(type(runtime) is dict and set(runtime)=={'id','image','running','restarting','pid','started','restarts'}
  and runtime['id']==OLD_CID and runtime['image']==R.BASELINE and runtime['running'] is True and runtime['restarting'] is False
  and type(runtime['pid']) is int and runtime['pid']>0 and type(runtime['restarts']) is int and runtime['restarts']>=0
  and type(runtime['started']) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z',runtime['started']),'rebase_old_observation')
 for path,row in {**old['files'],**old['ancestors']}.items():
  file=path in R.FILES;keys={'identity','xattrs_absent'}|({'bytes','sha256'} if file else set())
  need(type(row) is dict and set(row)==keys and row['xattrs_absent'] is True and full9(row['identity']),'rebase_old_observation')
  ident=row['identity'];meta=expected[path]['metadata'];need(ident[2]==(0o100000 if file else 0o040000)|meta[0]
   and R.same(ident[3:5],meta[1:3]),'rebase_metadata')
  if file:need(ident[5]==1 and type(row['bytes']) is int and row['bytes']==ident[6]==R.FILES[path][0]
   and row['sha256']==R.FILES[path][1],'rebase_metadata')
 return value
