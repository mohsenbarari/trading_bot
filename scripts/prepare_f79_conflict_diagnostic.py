"""Prepare a diagnostic-only overlay from the exact public historical F79 blob.

No image is built and no runtime state, configuration, session or key is read.
The generated child compares payloads only inside the existing capture role.
"""
import argparse
import ast
from hashlib import sha1,sha256
import json
import os
from pathlib import Path
import stat

SOURCE_BYTES=111954
SOURCE_SHA='1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929'
SOURCE_COMMIT='adca507b261d3e139befc67b4a937c48c96bb949'
SOURCE_PATH='core/market_intelligence/private_capture.py'
PARENT='sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
FOUNDATION_SHA='f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da'
TELEGRAM_SHA='75db6178b8f254ae3af5ca679d6e81dd2c3d72074fff78431e718865488aa327'
RAISE=b'                    raise CaptureRuntimeError("capture_event_identity_conflict")'
NEW_RAISE=b'                    raise _f79_conflict_error(seen, event, base_payload, source_code)'
ACCEPT=b'        result = self.state.stage(document, now=now)'
NEW_ACCEPT=b'''        try:
            result = self.state.stage(document, now=now)
        except CaptureRuntimeError as conflict:
            # stage() has already completed its existing rollback before this
            # diagnostic can run. No diagnostic fault may change its outcome.
            try:
                if (type(conflict) is CaptureRuntimeError
                        and conflict.args == ("capture_event_identity_conflict",)
                        and self.state.connection.in_transaction is False
                        and _f79_diag_admit()):
                    _f79_diag_collect(
                        self.spool.directory, self.state.account,
                        getattr(conflict, "_f79_conflict_context", None))
            except BaseException:
                pass
            raise'''

DIAGNOSTIC=r'''

# F79 diagnostic-only code. Event identity, semantic hashing, deduplication,
# quarantine, ownership and rollback semantics above remain unchanged.
import fcntl as _f79_fcntl
import gc as _f79_gc
import resource as _f79_resource
import select as _f79_select
import signal as _f79_signal
import stat as _f79_stat
import time as _f79_time

_F79_DIAG_SECONDS = 1.0
_F79_DIAG_REAP_SECONDS = 0.25
_F79_DIAG_READ_BYTES = 32 * 1024**2
_F79_DIAG_FILES = 8
_F79_DIAG_OUTPUT_BYTES = 4096
_F79_DIAG_MEMORY_BYTES = 32 * 1024**2
_F79_DIAG_NEXT_ALLOWED = 0.0
_F79_DIAG_STATUSES = frozenset(("compared", "not_found", "read_bound", "timeout", "unavailable",
    "old_identity_mismatch", "invalid_record", "context_unavailable"))
_F79_DIAG_ORIGINS = frozenset(("live", "reconcile", "explicit_backfill", "unclassified"))
_F79_DIAG_FIELDS = {
    "schema": (("schema",), ("schema_version",)),
    "event_type": (("event_type",),),
    "occurred_time": (("occurred_at_utc",),),
    "source_policy": (("source",),),
    "message_text": (("message", "text"), ("message", "text_sha256")),
    "message_revision": (("message", "revision_sha256"), ("message", "previous_revision_sha256")),
    "message_publication": (("message", "published_at_utc"),),
    "message_edit": (("message", "edited_at_utc"),),
    "message_content_type": (("message", "content_type"),),
    "sender_peer": (("message", "sender", "peer_id"),),
    "sender_kind": (("message", "sender", "kind"),),
    "sender_telegram_id": (("message", "sender", "telegram_id"),),
    "sender_display_name": (("message", "sender", "display_name"),),
    "reply_status": (("message", "reply", "status"),),
    "reply_parent": (("message", "reply", "message_id"),),
    "reply_topic": (("message", "reply", "top_message_id"),),
    "topic_status": (("message", "topic", "status"),),
    "topic_id": (("message", "topic", "topic_id"),),
    "entities": (("message", "entities"),),
    "media_type": (("message", "media_type"),),
    "action_type": (("message", "action_type"),),
    "grouped_id": (("message", "grouped_id"),),
    "forward_flags": tuple(("message", key) for key in ("is_forwarded", "via_bot", "post", "silent", "pinned", "noforwards")),
    "producer_name": (("producer", "name"),),
    "producer_version": (("producer", "version"),),
}
_F79_DIAG_LABELS = frozenset(_F79_DIAG_FIELDS) | {"other_semantic_fields"}

def _f79_diag_admit():
    # One scalar, process-local diagnostic admission only. No event identity,
    # source identifier or payload material enters the rate state.
    global _F79_DIAG_NEXT_ALLOWED
    now = _f79_time.monotonic()
    if now < _F79_DIAG_NEXT_ALLOWED:
        return False
    _F79_DIAG_NEXT_ALLOWED = now + 60.0
    return True

def _f79_conflict_error(seen, event, payload, source_code):
    error = CaptureRuntimeError("capture_event_identity_conflict")
    try:
        error._f79_conflict_context = (source_code, seen["capture_sequence"],
            seen["payload_sha256"], event.event_id, payload)
    except BaseException:
        pass
    return error

def _f79_diag_context(account, context):
    return (type(account) is str and account in ACCOUNT_SOURCES and type(context) is tuple and len(context) == 5
        and type(context[0]) is str and context[0] in ACCOUNT_SOURCES[account] and type(context[1]) is int and context[1] > 0
        and type(context[2]) is str and len(context[2]) == 64 and all(c in HEX64 for c in context[2])
        and type(context[3]) is str and 0 < len(context[3]) <= 256 and type(context[4]) is dict)

def _f79_diag_meta(account, context):
    document = context[4] if _f79_diag_context(account, context) else {}
    producer = document.get("producer") if type(document.get("producer")) is dict else {}
    choose = lambda value, choices: value if type(value) is str and value in choices else "unclassified"
    return {"schema": "capture_identity_conflict_diagnostic/1", "account": choose(account, ACCOUNT_SOURCES),
        "source_code": context[0] if _f79_diag_context(account, context) else "unclassified",
        "event_schema": choose(document.get("schema"), {"market_channel_event", "coin_group_event"}),
        "schema_version": choose(document.get("schema_version"), {"1.0", "2.0", "2.1"}),
        "event_type": choose(document.get("event_type"), {"message_created", "message_snapshot", "message_edited", "message_deleted"}),
        "incoming_origin": choose(producer.get("origin"), _F79_DIAG_ORIGINS),
        "previous_origin": "unclassified", "branch": "seen", "rollback_confirmed": True,
        "comparison_status": "unavailable", "changed_fields": [], "security_accepted": False}

def _f79_diag_validate(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        return None
    variable = {"previous_origin", "comparison_status", "changed_fields"}
    if any(type(value[k]) is not type(expected[k]) or value[k] != expected[k] for k in expected if k not in variable):
        return None
    if (type(value["previous_origin"]) is not str or value["previous_origin"] not in _F79_DIAG_ORIGINS
            or type(value["comparison_status"]) is not str or value["comparison_status"] not in _F79_DIAG_STATUSES):
        return None
    fields = value["changed_fields"]
    if (type(fields) is not list or len(fields) > len(_F79_DIAG_LABELS)
            or any(type(name) is not str or name not in _F79_DIAG_LABELS for name in fields)
            or fields != sorted(set(fields)) or value["comparison_status"] != "compared" and fields):
        return None
    return value

def _f79_diag_normalized(document):
    # Deliberately diagnostic-only: do not refactor semantic_payload_hash().
    value = copy.deepcopy(document)
    producer = value.get("producer")
    if type(producer) is dict:
        for key in ("capture_sequence", "received_at_utc", "available_at_utc", "origin", "is_backfill", "source_clock_adjusted"):
            producer.pop(key, None)
    message = value.get("message")
    if type(message) is dict:
        message.pop("is_backfill", None)
    if value.get("event_type") in {"message_created", "message_snapshot"}:
        value["event_type"] = "message_upsert"
    return value

def _f79_diag_compare(old, new):
    left, right = _f79_diag_normalized(old), _f79_diag_normalized(new)
    labels = []
    def pop(document, path):
        for key in path[:-1]:
            if type(document) is not dict or key not in document:
                return {"present": False}
            document = document[key]
        if type(document) is not dict or path[-1] not in document:
            return {"present": False}
        return {"present": True, "value": document.pop(path[-1])}
    for label, paths in _F79_DIAG_FIELDS.items():
        differences = [canonical_json(pop(left, path)) != canonical_json(pop(right, path)) for path in paths]
        if any(differences):
            labels.append(label)
    if canonical_json(left) != canonical_json(right):
        labels.append("other_semantic_fields")
    return sorted(labels)

def _f79_diag_json(raw):
    def unique(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError()
            value[key] = item
        return value
    def constant(_):
        raise ValueError()
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    if type(value) is not dict:
        raise ValueError()
    return value

def _f79_diag_lookup(directory_fd, account, context, today, deadline):
    result = _f79_diag_meta(account, context)
    remaining = min(32 * 1024**2, _F79_DIAG_READ_BYTES)
    source, sequence, old_hash, event_id, new = context
    if semantic_payload_hash(new) == old_hash:
        result["comparison_status"] = "context_unavailable"
        return result
    def candidate(raw):
        if not raw:
            return None
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError()
        old = _f79_diag_json(raw)
        producer = old.get("producer")
        if (old.get("event_id") != event_id or type(old.get("event_id")) is not str
                or type(producer) is not dict or type(producer.get("capture_sequence")) is not int
                or producer["capture_sequence"] != sequence):
            return None
        if semantic_payload_hash(old) != old_hash:
            result["comparison_status"] = "old_identity_mismatch"
            return result
        result["comparison_status"] = "compared"
        result["changed_fields"] = _f79_diag_compare(old, new)
        origin = producer.get("origin")
        result["previous_origin"] = origin if type(origin) is str and origin in _F79_DIAG_ORIGINS else "unclassified"
        return result
    for day in range(min(8, _F79_DIAG_FILES)):
        if _f79_time.monotonic() >= deadline:
            result["comparison_status"] = "timeout"
            return result
        name = "events-" + (today - timedelta(days=day)).strftime("%Y-%m-%d") + ".jsonl"
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        except FileNotFoundError:
            continue
        try:
            before = os.fstat(fd)
            stamp = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (not _f79_stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or _f79_stat.S_IMODE(before.st_mode) != 0o600):
                return result
            position = before.st_size
            carry = b""
            first = True
            found = None
            while position:
                if _f79_time.monotonic() >= deadline:
                    result["comparison_status"] = "timeout"
                    return result
                if remaining <= 0:
                    result["comparison_status"] = "read_bound"
                    return result
                count = min(65536, position, remaining)
                block = os.pread(fd, count, position - count)
                if len(block) != count or first and not block.endswith(b"\n"):
                    result["comparison_status"] = "invalid_record"
                    return result
                first = False
                remaining -= len(block)
                position -= len(block)
                pieces = (block + carry).split(b"\n")
                carry = pieces[0]
                for line in reversed(pieces[1:]):
                    found = candidate(line)
                    if found is not None:
                        break
                if found is not None:
                    break
                if len(carry) > MAX_RECORD_BYTES:
                    result["comparison_status"] = "invalid_record"
                    return result
            if found is None and not position:
                found = candidate(carry)
            if stamp(before) != stamp(os.fstat(fd)) or stamp(before) != stamp(os.stat(name, dir_fd=directory_fd, follow_symlinks=False)):
                return _f79_diag_meta(account, context)
            if found is not None:
                return found
        except (ValueError, UnicodeError, json.JSONDecodeError):
            result["comparison_status"] = "invalid_record"
            return result
        finally:
            os.close(fd)
    result["comparison_status"] = "not_found"
    return result

def _f79_diag_limits(virtual_bytes):
    _f79_gc.disable()
    for kind, value in ((_f79_resource.RLIMIT_CORE, 0), (_f79_resource.RLIMIT_FSIZE, 0),
            (_f79_resource.RLIMIT_CPU, 1), (_f79_resource.RLIMIT_NPROC, 0),
            (_f79_resource.RLIMIT_AS, virtual_bytes + min(32 * 1024**2, _F79_DIAG_MEMORY_BYTES))):
        hard = _f79_resource.getrlimit(kind)[1]
        bound = value if hard == _f79_resource.RLIM_INFINITY else min(value, hard)
        _f79_resource.setrlimit(kind, (bound, bound))

def _f79_diag_directory(directory, account, context, today, deadline):
    expected = _f79_diag_meta(account, context)
    if not Path(directory).is_absolute() or Path(directory).resolve(strict=True) != Path(directory):
        return expected
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    if descriptor != 3:
        os.dup2(descriptor, 3)
        os.close(descriptor)
    before = os.fstat(3)
    stamp = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid)
    if before.st_uid != os.geteuid() or _f79_stat.S_IMODE(before.st_mode) != 0o700:
        return expected
    value = _f79_diag_lookup(3, account, context, today, deadline)
    if stamp(before) != stamp(os.fstat(3)) or stamp(before) != stamp(Path(directory).lstat()):
        return expected
    return value

def _f79_diag_collect(directory, account, context):
    expected = _f79_diag_meta(account, context)
    if not _f79_diag_context(account, context):
        expected["comparison_status"] = "context_unavailable"
        return expected
    root = read_fd = write_fd = pid = None
    status = None
    value = expected
    clean = True
    killed = False
    try:
        # Own-process numeric VM metadata only; never environment/session/key
        # bytes. The child gets at most 32MiB additional address space.
        descriptor = os.open("/proc/self/statm", os.O_RDONLY | os.O_CLOEXEC)
        try:
            raw = os.read(descriptor, 129)
        finally:
            os.close(descriptor)
        if len(raw) > 128:
            return expected
        virtual_bytes = int(raw.split()[0]) * os.sysconf("SC_PAGE_SIZE")
        upper = _f79_resource.getrlimit(_f79_resource.RLIMIT_NOFILE)[1]
        if not 0 < virtual_bytes <= 16 * 1024**3 or upper == _f79_resource.RLIM_INFINITY or not 0 < upper <= 1048576:
            return expected
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        deadline = _f79_time.monotonic() + min(1.0, _F79_DIAG_SECONDS)
        today = utc_now().date()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(read_fd)
                pipe_copy = _f79_fcntl.fcntl(write_fd, _f79_fcntl.F_DUPFD_CLOEXEC, 6)
                log_copy = _f79_fcntl.fcntl(2, _f79_fcntl.F_DUPFD_CLOEXEC, 6)
                os.dup2(pipe_copy, 4); os.dup2(log_copy, 5)
                os.closerange(6, upper)
                for descriptor in (0, 1, 2, 3):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                _f79_diag_limits(virtual_bytes)
                try:
                    # All spool path traversal/open/read/final checks are
                    # inside the hard child deadline, including directory I/O.
                    child_value = _f79_diag_directory(directory, account, context, today, deadline)
                except BaseException:
                    child_value = expected
                checked = _f79_diag_validate(child_value, expected)
                checked = checked if checked is not None else expected
                if not _f79_emit_diagnostic(checked):
                    os._exit(1)
                payload = canonical_json(checked)
                if 0 < len(payload) <= min(4096, _F79_DIAG_OUTPUT_BYTES):
                    os.write(4, payload)
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd); write_fd = None
        data = bytearray()
        while True:
            if _f79_time.monotonic() >= deadline:
                value = {**expected, "comparison_status": "timeout"}
                break
            ready, _, _ = _f79_select.select([read_fd], [], [], min(.01, max(0, deadline - _f79_time.monotonic())))
            if ready:
                block = os.read(read_fd, min(4096, _F79_DIAG_OUTPUT_BYTES) + 1 - len(data))
                if not block:
                    break
                data.extend(block)
                if len(data) > min(4096, _F79_DIAG_OUTPUT_BYTES):
                    break
            child, observed = os.waitpid(pid, os.WNOHANG)
            if child:
                status = observed
                # Drain the final small packet only after the child exited.
                try:
                    block = os.read(read_fd, min(4096, _F79_DIAG_OUTPUT_BYTES) + 1 - len(data))
                    data.extend(block)
                except BlockingIOError:
                    pass
                break
        if data and len(data) <= min(4096, _F79_DIAG_OUTPUT_BYTES):
            checked = _f79_diag_validate(_f79_diag_json(bytes(data)), expected)
            if checked is not None:
                value = checked
    except BaseException:
        value = expected
    finally:
        if pid and pid > 0:
            while status is None and _f79_time.monotonic() < deadline:
                try:
                    child, observed = os.waitpid(pid, os.WNOHANG)
                    if child:
                        status = observed
                except BaseException:
                    clean = False
                    break
                if status is None:
                    _f79_time.sleep(.005)
            if status is None:
                try:
                    os.kill(pid, _f79_signal.SIGKILL)
                    killed = True
                except ProcessLookupError:
                    pass
                except BaseException:
                    clean = False
                end = _f79_time.monotonic() + min(.25, _F79_DIAG_REAP_SECONDS)
                while status is None and _f79_time.monotonic() < end:
                    try:
                        child, observed = os.waitpid(pid, os.WNOHANG)
                        if child:
                            status = observed
                    except BaseException:
                        clean = False
                        break
                    if status is None:
                        _f79_time.sleep(.005)
            expected_timeout = (killed and value["comparison_status"] == "timeout" and status is not None
                and os.WIFSIGNALED(status) and os.WTERMSIG(status) == _f79_signal.SIGKILL)
            if status is None or status != 0 and not expected_timeout:
                clean = False
        for descriptor in (root, read_fd, write_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    clean = False
    return value if clean else expected

def _f79_emit_diagnostic(value):
    # Child-only FD5 is an explicit duplicate of the existing stderr writer.
    # Blocking logging remains inside the parent's hard child deadline.
    if type(value) is not dict:
        return False
    account = value.get("account")
    if type(account) is not str or account not in {*ACCOUNT_SOURCES, "unclassified"}:
        return False
    expected = _f79_diag_meta(account, None)
    choices = {"source_code": set(ACCOUNT_SOURCES.get(account, ())) | {"unclassified"},
        "event_schema": {"market_channel_event", "coin_group_event", "unclassified"},
        "schema_version": {"1.0", "2.0", "2.1", "unclassified"},
        "event_type": {"message_created", "message_snapshot", "message_edited", "message_deleted", "unclassified"},
        "incoming_origin": _F79_DIAG_ORIGINS}
    for field, allowed in choices.items():
        if type(value.get(field)) is not str or value[field] not in allowed:
            return False
        expected[field] = value[field]
    checked = _f79_diag_validate(value, expected)
    if checked is None:
        return False
    raw = canonical_json(checked) + b"\n"
    if len(raw) <= min(4096, _F79_DIAG_OUTPUT_BYTES):
        return os.write(5, raw) == len(raw)
    return False
'''

def need(ok,code):
    if not ok:raise RuntimeError(code)

def make_overlay(raw):
    # Cold public source identity is checked BEFORE AST parsing/compilation.
    need(type(raw) is bytes and len(raw)==SOURCE_BYTES and sha256(raw).hexdigest()==SOURCE_SHA,'historical_source_pin')
    need(raw.count(RAISE)==raw.count(ACCEPT)==1,'diagnostic_anchor')
    overlay=raw.replace(RAISE,NEW_RAISE).replace(ACCEPT,NEW_ACCEPT)+DIAGNOSTIC.encode()
    restored=overlay[:-len(DIAGNOSTIC.encode())].replace(NEW_RAISE,RAISE).replace(NEW_ACCEPT,ACCEPT)
    need(restored==raw,'diagnostic_scope')
    ast.parse(overlay);return overlay

def prepare(source,output):
    source=Path(source).absolute();output=Path(output).absolute()
    need(source.resolve(strict=True)==source and not source.is_symlink(),'source_path')
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==os.geteuid() and before.st_nlink==1
            and not before.st_mode&0o022 and before.st_size==SOURCE_BYTES,'source_metadata')
        raw=os.pread(fd,SOURCE_BYTES+1,0);overlay=make_overlay(raw)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        need(identity(before)==identity(os.fstat(fd))==identity(source.lstat()),'source_changed')
    finally:os.close(fd)
    output.mkdir(mode=0o700)
    dockerfile=b'ARG F79_PARENT_ALIAS\nFROM ${F79_PARENT_ALIAS}\nCOPY --chown=10001:10001 --chmod=0644 private_capture.py /app/core/market_intelligence/private_capture.py\n'
    manifest={'schema':'f79_conflict_diagnostic_overlay/1','source_commit':SOURCE_COMMIT,'source_git_path':SOURCE_PATH,
        'source_git_blob':sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'source_bytes':SOURCE_BYTES,'source_sha256':SOURCE_SHA,
        'required_parent_config_id':PARENT,'preserved_source_overrides':{'private_pipeline_foundation.py':FOUNDATION_SHA},
        'unchanged_telegram_source_sha256':TELEGRAM_SHA,'overlay_bytes':len(overlay),'overlay_sha256':sha256(overlay).hexdigest(),
        'dockerfile_bytes':len(dockerfile),'dockerfile_sha256':sha256(dockerfile).hexdigest(),
        'copy_path':'/app/'+SOURCE_PATH,'scope':'diagnostic_only_no_identity_dedupe_rollback_quarantine_or_ownership_change',
        'limits':{'wall_seconds':1,'cleanup_seconds':.25,'read_bytes':32*1024**2,'files':8,'output_bytes':4096,
            'additional_address_space_bytes':32*1024**2,'record_bytes':256*1024,
            'child_fds':['own_spool_directory','bounded_reply_pipe','duplicate_existing_stderr_writer'],
            'parent_emits_diagnostic':False,'process_ram_rate_limit':{'attempts':1,'seconds':60,'event_identifiers_stored':False}},
        'parent_alias_requires_existing_image_admission':True,'image_built':False,'runtime_changed':False,'functional_fix_claimed':False,'security_accepted':False}
    for name,body,mode in [('private_capture.py',overlay,0o644),('Dockerfile',dockerfile,0o644),
            ('manifest.json',(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode(),0o600)]:
        descriptor=os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        try:
            view=memoryview(body)
            while view:
                written=os.write(descriptor,view);need(written>0,'write_short');view=view[written:]
            os.fchmod(descriptor,mode);os.fsync(descriptor)
        finally:os.close(descriptor)
    return manifest

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.error=lambda _:need(False,'arguments')
    try:
        args=parser.parse_args(argv);value=prepare(args.source,args.output)
    except BaseException:
        print('{"status":"refused","code":"diagnostic_preparation_refused","security_accepted":false}');return 1
    print(json.dumps({'status':'public_source_overlay_prepared','overlay_sha256':value['overlay_sha256'],'image_built':False,'security_accepted':False},sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
