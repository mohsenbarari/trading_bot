"""Pure IMAGE-STORE-ONLY health observations. No I/O, runtime or serving authority."""
from datetime import datetime, timezone
import re

F79='6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
SCHEMA='runtime_image_store_health_observation/1'
FIELDS=frozenset(('schema','operation','operation_sha256','health_policy_sha256','nonce','site',
    'container_id','image_id','pid','started','restarts','baseline_health','observed_health','observed_at',
    'runtime_health_issues_open','security_accepted'))

def same(a,b):
    if type(a) is not type(b):return False
    if type(a) is dict:return set(a)==set(b) and all(same(a[k],b[k]) for k in a)
    if type(a) is list:return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    return a==b

def need(ok):
    if not ok:raise ValueError('runtime_health_policy')

def hex64(value):return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None

def started(value):
    if type(value) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',value) is None:return False
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo==timezone.utc
    except ValueError:return False

def stable_row(row):
    return (type(row) is dict and hex64(row.get('id')) and type(row.get('image')) is str
        and re.fullmatch('sha256:[a-f0-9]{64}',row['image']) is not None
        and row.get('running') is True and all(row.get(k) is False for k in ('restarting','paused','dead'))
        and type(row.get('pid')) is int and row['pid']>0 and type(row.get('restarts')) is int and row['restarts']>=0
        and started(row.get('started')) and type(row.get('health')) is str and row['health'] in {'healthy','unhealthy'})

def health_equivalence(before,after):
    """A changed health is eligible only with exact typed equality of ALL other fields."""
    return stable_row(before) and stable_row(after) and same(dict(before,health=after['health']),after)

def row_map(rows):
    if type(rows) is not list or not 0<len(rows)<=128 or any(type(r) is not dict or not hex64(r.get('id')) for r in rows):return None
    values={r['id']:r for r in rows}
    return values if len(values)==len(rows) else None

def comparison_clone(site,before,after,excluded=(F79,)):
    """Comparison clone only. Excluded F79 keeps its separate sealed-base policy."""
    if type(site) is not str or site not in {'bot','web'}:return False
    old,new=row_map(before),row_map(after)
    if old is None or new is None or set(old)!=set(new):return False
    result=[]
    for row in after:
        prior=old[row['id']]
        if row['id'] in excluded or same(prior,row):result.append(row);continue
        if prior.get('health')==row.get('health') or not health_equivalence(prior,row):return False
        result.append(dict(row,health=prior['health']))
    return result

def scope_valid(scope):
    return (type(scope) is dict and set(scope)=={'operation','operation_sha256','health_policy_sha256','nonce','site','not_before','deadline'}
        and type(scope['operation']) is str and re.fullmatch('[a-z0-9-]{1,120}',scope['operation']) is not None
        and hex64(scope['operation_sha256']) and hex64(scope['health_policy_sha256'])
        and type(scope['nonce']) is str and re.fullmatch('[a-f0-9]{32}',scope['nonce']) is not None
        and type(scope['site']) is str and scope['site'] in {'bot','web'} and type(scope['not_before']) is int and type(scope['deadline']) is int
        and scope['not_before']<scope['deadline'])

def observation(scope,before,after,observed_at):
    need(scope_valid(scope) and type(before) is dict and before.get('id')!=F79 and health_equivalence(before,after)
        and before['health']!=after['health'] and type(observed_at) is int
        and scope['not_before']<=observed_at<=scope['deadline'])
    return {'schema':SCHEMA,**{k:scope[k] for k in ('operation','operation_sha256','health_policy_sha256','nonce','site')},
        'container_id':before['id'],'image_id':before['image'],'pid':before['pid'],'started':before['started'],
        'restarts':before['restarts'],'baseline_health':before['health'],'observed_health':after['health'],
        'observed_at':observed_at,'runtime_health_issues_open':True,'security_accepted':False}

def admit_observations(scope,values,baseline,previous=None):
    """Strict closed records; immutable first observation per CID, monotonic additions."""
    if not scope_valid(scope) or type(values) is not list or len(values)>128:return None
    rows=row_map(baseline)
    if rows is None:return None
    if any(type(v) is not dict or set(v)!=FIELDS or not hex64(v.get('container_id')) for v in values):return None
    ids=[v['container_id'] for v in values]
    if ids!=sorted(set(ids)):return None
    for value in values:
        prior=rows.get(value['container_id'])
        if prior is None or value['container_id']==F79 or not stable_row(prior):return None
        after=dict(prior,health=value['observed_health'])
        try:expected=observation(scope,prior,after,value['observed_at'])
        except (ValueError,TypeError,KeyError):return None
        if not same(value,expected):return None
    if previous is not None:
        admitted=admit_observations(scope,previous,baseline)
        if admitted is None:return None
        by_id={v['container_id']:v for v in values}
        if any(v['container_id'] not in by_id or not same(v,by_id[v['container_id']]) for v in admitted):return None
    return [dict(value) for value in values]

def observations(scope,before,after,observed_at,previous=None):
    """Keep raw snapshots and every already admitted first transition unchanged."""
    need(scope_valid(scope))
    need(comparison_clone(scope['site'],before,after) is not False)
    prior=[] if previous is None else admit_observations(scope,previous,before)
    need(prior is not None);records={v['container_id']:v for v in prior};old=row_map(before)
    for row in after:
        first=old[row['id']]
        if row['id']!=F79 and first.get('health')!=row.get('health') and row['id'] not in records:
            records[row['id']]=observation(scope,first,row,observed_at)
    return [records[key] for key in sorted(records)]
