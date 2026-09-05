"""Native Antigravity tool router for the project-local typed gateway.

This module is deliberately dependency-injected. The installed host supervisor owns
the manifest, runtime context, test runner, receipt ledger and operation executor.
Importing repository code is not an installation or authority to activate Stage work.
Unknown tools, paths, arguments and authority states are denied without a shell grant.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

from scripts.two_site_agent_context import PROTECTED_SOURCE
from scripts.two_site_agent_gateway import ActorRole, plan_operation
from scripts.two_site_agent_sandbox import EXCLUDED_ROOTS, SOURCE_SUFFIXES
from scripts.two_site_agent_transport import (
    MODEL, UUID, TransportError, digest, encoded, read_bounded, safe_path, unique_json, write_new,
)

MAX_SOURCE_WRITE = 1_000_000
LOCAL_READABLE = {
    'worktrees.yaml', 'orchestrator-fence.json', 'antigravity-state.json',
    'antigravity-runtime-sessions.json',
}
RESERVED_COMPONENTS = {'credentials', 'secrets', 'private', 'dumps', '.ssh', '.codex', '.gemini'}


def source_path(root: Path, raw: str, *, must_exist: bool = True) -> str:
    """Permit source text, not runtime data, credentials or traversal/symlinks."""
    if not isinstance(raw, str):
        raise TransportError('source path must be absolute text')
    path = safe_path(Path(raw), exists=must_exist)
    if not path.is_relative_to(root) or path == root:
        raise TransportError('source path outside exact workspace')
    relative = path.relative_to(root)
    if relative.parts[0] in EXCLUDED_ROOTS | {'data'}:
        raise TransportError('runtime storage is not Agent source')
    if any(part.lower() in RESERVED_COMPONENTS or (part.startswith('.env') and not part.endswith('.example'))
           for part in relative.parts):
        raise TransportError('private/runtime path is not source')
    if (str(relative).startswith('docs/memory/archive/')
            or (relative.suffix not in SOURCE_SUFFIXES and relative.name not in {'Makefile', 'Dockerfile'})):
        raise TransportError('unsupported source type')
    if must_exist and not path.is_file():
        raise TransportError('source read must target a regular file')
    return relative.as_posix()


class NativeController:
    """Route actual native events; never accept caller-provided role or argv.

    transport callbacks must use policy() and a protected executor. All source-write
    and delegation decisions share its lock, so no test/commit can race a second
    Agent tool mutation. A persistent Pre/Post source reservation blocks commands
    while the native write occurs outside the hook process.
    """

    def __init__(self, context_builder, transport):
        self.builder, self.transport = context_builder, transport

    def policy(self, event: dict, request: dict):
        if self.reservation.exists():
            raise TransportError('a native source write is pending; no command may race it')
        return plan_operation(request, self.builder.build(event, request))

    @property
    def reservation(self) -> Path:
        return self.builder.state / 'source-mutation.json'

    def _write_identity(self, event: dict) -> dict:
        return {'actor': event['conversationId'], 'step': event['stepIdx'],
                'tool_call_sha256': digest(event['toolCall'])}

    def _other_state(self, root: Path, target: str) -> dict:
        snapshot = self.builder.observe(root)
        others = {}
        for relative in snapshot.changed_paths:
            if relative == target:
                continue
            path = safe_path(root / relative, exists=False)
            others[relative] = (hashlib.sha256(read_bounded(path, 8_000_000)).hexdigest()
                                if path.exists() else 'TRACKED_DELETION')
        return {'head': snapshot.head_sha, 'branch': snapshot.branch,
                'staged_paths': list(snapshot.staged_paths),
                'staged_patch_sha256': snapshot.staged_patch_sha256, 'other_changed_files': others}

    def _event(self, event: dict) -> tuple[str, dict]:
        if not isinstance(event, dict) or len(encoded(event)) > 2_000_000:
            raise TransportError('invalid native tool event')
        if (not isinstance(event.get('conversationId'), str)
                or not UUID.fullmatch(event['conversationId']) or event.get('modelName') != MODEL
                or type(event.get('stepIdx')) is not int or event['stepIdx'] < 0
                or event.get('workspacePaths') != self.builder.manifest['workspaces']):
            raise TransportError('native identity/workspace mismatch')
        call = event.get('toolCall')
        if not isinstance(call, dict) or set(call) != {'name', 'args'} or not isinstance(call['args'], dict):
            raise TransportError('unknown native tool-call layout')
        self.builder.validate_installation()
        return call['name'], call['args']

    def _identity(self, event: dict):
        fence, registry, assignment = self.builder.active_assignment()
        actor = self.builder.actor(event, assignment, fence)
        return actor, fence, registry, assignment

    def _source_write(self, event: dict, name: str, args: dict) -> dict:
        if self.reservation.exists():
            raise TransportError('another native source write is still pending')
        actor, fence, _, assignment = self._identity(event)
        if actor.role is not ActorRole.WORKER:
            raise TransportError('only the assigned Worker may edit source')
        generic = {'toolAction', 'toolSummary', 'TargetFile', 'CodeMarkdownLanguage'}
        supported = ({'CodeContent', 'Overwrite', 'Description', 'IsArtifact'} if name == 'write_to_file'
                     else {'TargetContent', 'ReplacementContent', 'AllowMultiple', 'Description',
                           'StartLine', 'EndLine', 'Instruction', 'Complexity'})
        if set(args) - generic - supported or args.get('IsArtifact', False) is not False:
            raise TransportError('unknown source-edit argument or artifact write')
        if name == 'write_to_file' and type(args.get('Overwrite')) is not bool:
            raise TransportError('source-write overwrite policy missing')
        for key in ('CodeContent',) if name == 'write_to_file' else ('TargetContent', 'ReplacementContent'):
            if not isinstance(args.get(key), str) or len(args[key].encode()) > MAX_SOURCE_WRITE:
                raise TransportError('invalid or oversized source edit')
        root = safe_path(Path(assignment['worktree_path']))
        relative = source_path(root, args.get('TargetFile'), must_exist=name != 'write_to_file')
        if relative in PROTECTED_SOURCE or relative.startswith(('.agents/', '.git/', '.local/')):
            raise TransportError('Agent cannot replace its execution authority')
        allowed, forbidden = assignment['allowed_paths'], assignment['forbidden_paths']
        if (not any(fnmatch.fnmatchcase(relative, value) for value in allowed)
                or any(fnmatch.fnmatchcase(relative, value) for value in forbidden)):
            raise TransportError('source write outside the issued Assignment')
        check = {'id': '00000000-0000-4000-8000-000000000001', 'operation': 'worker-check',
                 'actor_id': actor.actor_id, 'assignment': assignment['assignment_id'],
                 'epoch': fence['epoch'], 'integration_head': fence['integration_head_sha']}
        self.policy(event, check)  # shared actual Git/fence/Assignment validation, no mutation
        target = root / relative
        before = read_bounded(target, MAX_SOURCE_WRITE) if target.exists() else None
        if name == 'write_to_file':
            if before is not None and args['Overwrite'] is False:
                raise TransportError('existing source file requires explicit overwrite')
            after = args['CodeContent'].encode()
        else:
            if before is None or not args['TargetContent']:
                raise TransportError('replacement requires existing exact target text')
            old, new = args['TargetContent'].encode(), args['ReplacementContent'].encode()
            occurrences = before.count(old)
            if not occurrences or (occurrences != 1 and args.get('AllowMultiple', False) is not True):
                raise TransportError('replacement is absent or ambiguous')
            if len(before) + occurrences * (len(new) - len(old)) > MAX_SOURCE_WRITE:
                raise TransportError('expanded replacement exceeds the source file budget')
            after = before.replace(old, new)
        write_new(self.reservation, {
            **self._write_identity(event), 'assignment': assignment['assignment_id'],
            'epoch': fence['epoch'], 'integration_head': fence['integration_head_sha'],
            'worktree': str(root), 'target': relative,
            'before_sha256': hashlib.sha256(before).hexdigest() if before is not None else None,
            'after_sha256': hashlib.sha256(after).hexdigest(),
            'other_state': self._other_state(root, relative),
        })
        return {'decision': 'allow', 'reason': 'one reserved Assignment-scoped source write'}

    def _source_post(self, event: dict) -> dict:
        pending = unique_json(read_bounded(self.reservation, 1_000_000))
        if any(pending.get(key) != value for key, value in self._write_identity(event).items()):
            raise TransportError('source write pre/post identity changed')
        actor, fence, _, assignment = self._identity(event)
        if (actor.role is not ActorRole.WORKER or pending['assignment'] != assignment['assignment_id']
                or pending['epoch'] != fence['epoch'] or pending['integration_head'] != fence['integration_head_sha']
                or pending['worktree'] != assignment['worktree_path']):
            raise TransportError('source write authority changed')
        root = safe_path(Path(pending['worktree']))
        target = safe_path(root / pending['target'])
        observed = hashlib.sha256(read_bounded(target, MAX_SOURCE_WRITE)).hexdigest()
        integration = self.builder.observe(self.builder.root)
        if (event.get('error') or observed != pending['after_sha256']
                or integration.head_sha != pending['integration_head'] or not integration.clean
                or self._other_state(root, pending['target']) != pending['other_state']):
            # Do not discard a possibly partial edit or release the reservation.
            raise TransportError('source write failed its exact content/Git postcondition')
        receipts = self.builder.state / 'source-receipts'
        safe_path(receipts, exists=False)
        receipts.mkdir(mode=0o700, exist_ok=True)
        write_new(receipts / (pending['actor'] + '-' + str(pending['step']) + '.json'),
                  {**pending, 'status': 'SUCCESS', 'observed_sha256': observed})
        self.reservation.unlink()
        return {}

    def _read(self, event: dict, name: str, args: dict) -> dict:
        actor, _, _, assignment = self._identity(event)
        if name == 'list_dir':
            if set(args) - {'DirectoryPath', 'toolAction', 'toolSummary'}:
                raise TransportError('unknown directory-list option')
            raw = args.get('DirectoryPath')
            if not isinstance(raw, str):
                raise TransportError('missing exact directory path')
            path = safe_path(Path(raw))
            root = (self.builder.root if path.is_relative_to(self.builder.root)
                    else safe_path(Path(assignment['worktree_path'])))
            if not path.is_relative_to(root) or not path.is_dir():
                raise TransportError('directory is outside the assigned sources')
            parts = path.relative_to(root).parts
            if any(part.lower() in RESERVED_COMPONENTS | EXCLUDED_ROOTS | {'data'} for part in parts):
                raise TransportError('runtime/private directories are not source inventory')
            return {'decision': 'allow', 'reason': 'non-recursive source directory metadata'}
        if name == 'grep_search':
            if set(args) - {'SearchPath', 'Query', 'IsRegex', 'CaseInsensitive', 'MatchPerLine', 'toolAction', 'toolSummary'}:
                raise TransportError('unknown source-search option')
            if not isinstance(args.get('Query'), str) or not 1 <= len(args['Query']) <= 2000:
                raise TransportError('invalid bounded search query')
            raw = args.get('SearchPath')
            path = Path(raw) if isinstance(raw, str) else Path('/')
            root = (self.builder.root if path.is_relative_to(self.builder.root)
                    else safe_path(Path(assignment['worktree_path'])))
            source_path(root, raw)  # exact regular source file, never a recursive root search
            return {'decision': 'allow', 'reason': 'bounded search of a specific source file'}
        if name != 'view_file':
            # Native directory search can traverse unrelated .env/runtime files.
            # Add only a source-inventory-backed implementation, never a broad root grep.
            raise TransportError('use a specific authorized source file')
        if set(args) - {'AbsolutePath', 'StartLine', 'EndLine', 'IncludeSummaryOfOtherLines', 'IsSkillFile',
                        'toolAction', 'toolSummary'}:
            raise TransportError('unknown source-read option')
        raw = args.get('AbsolutePath')
        if not isinstance(raw, str):
            raise TransportError('missing exact source path')
        path = safe_path(Path(raw))
        local = self.builder.root / '.local/two-site-refactor'
        if path.is_relative_to(local):
            relative = path.relative_to(local).as_posix()
            exact_assignment = 'assignments/' + assignment['assignment_id'] + '.md'
            public_result = re.fullmatch(r'gateway/results/([0-9a-f-]{36})\.json', relative)
            if relative not in LOCAL_READABLE | {exact_assignment} and not (
                    public_result and UUID.fullmatch(public_result[1])):
                raise TransportError('local credential/operational evidence is not readable')
            if public_result:
                # Receipt caches may be read; neither they nor model reports can
                # substitute for protected receipt authority in mutation planning.
                if not path.is_file():
                    raise TransportError('result cache is not a regular file')
        else:
            root = (self.builder.root if path.is_relative_to(self.builder.root)
                    else safe_path(Path(assignment['worktree_path'])))
            source_path(root, raw)
        return {'decision': 'allow', 'reason': 'specific source/evidence read for native ' + actor.role.value}

    def _collaboration(self, event: dict, name: str, args: dict) -> dict:
        actor, fence, _, assignment = self._identity(event)
        if name == 'manage_subagents':
            if (actor.role is not ActorRole.COORDINATOR or args.get('Action') != 'list'
                    or set(args) - {'Action', 'toolAction', 'toolSummary'}):
                raise TransportError('only Coordinator may list its native children; no kill grant')
            return {'decision': 'allow', 'reason': 'native Coordinator child status'}
        if set(args) - {'Recipient', 'Message', 'toolAction', 'toolSummary'}:
            raise TransportError('unknown peer-message argument')
        recipient, message = args.get('Recipient'), args.get('Message')
        if (not isinstance(recipient, str) or not UUID.fullmatch(recipient)
                or not isinstance(message, str) or not 1 <= len(message.encode()) <= 150_000):
            raise TransportError('bounded message requires exact peer UUID')
        coordinator = self.builder.manifest['coordinator_id']
        if actor.role is not ActorRole.COORDINATOR:
            if recipient != coordinator:
                raise TransportError('child may report only to its bound Coordinator')
        else:
            entry = unique_json(read_bounded(self.builder.state / 'actors' / (recipient + '.json'), 65536))
            if (entry.get('parent') != coordinator or entry.get('assignment') != assignment['assignment_id']
                    or entry.get('epoch') != fence['epoch'] or entry.get('role') not in {'WORKER', 'VERIFIER'}):
                raise TransportError('recipient is not a child of this Assignment/epoch')
            self.builder._binding(recipient, entry['role'])
        return {'decision': 'allow', 'reason': 'bounded native parent-child handoff'}

    def pre(self, event: dict) -> dict:
        name, args = self._event(event)
        if name == 'run_command':
            raise TransportError('direct shell is disabled; use a typed request file')
        if name == 'write_to_file' and isinstance(args.get('TargetFile'), str) and Path(args['TargetFile']).is_relative_to(self.transport.requests):
            return self.transport.pre(event)
        with self.transport.locked():
            if name == 'invoke_subagent':
                self.transport.admission_check()
                if self.reservation.exists():
                    raise TransportError('source mutation must finish before delegation')
                return self.builder.delegate(event)
            if name in {'write_to_file', 'replace_file_content'}:
                self.transport.admission_check()
                return self._source_write(event, name, args)
            if name in {'view_file', 'list_dir', 'find_by_name', 'grep_search'}:
                return self._read(event, name, args)
            if name in {'send_message', 'manage_subagents'}:
                return self._collaboration(event, name, args)
            raise TransportError('native tool has no reviewed controller adapter')

    def post(self, event: dict) -> dict:
        name, args = self._event(event)
        if name == 'write_to_file' and isinstance(args.get('TargetFile'), str) and Path(args['TargetFile']).is_relative_to(self.transport.requests):
            return self.transport.post(event)
        if name in {'write_to_file', 'replace_file_content'}:
            with self.transport.locked():
                return self._source_post(event)
        return {}


def hook_response(controller: NativeController, phase: str, event: dict) -> dict:
    """No exception text/source content/credentials escape into a native error."""
    try:
        if phase not in {'pre', 'post'}:
            raise TransportError('unknown hook phase')
        return getattr(controller, phase)(event)
    except Exception as error:
        if phase == 'pre':
            return {'decision': 'deny', 'reason': 'typed gateway blocked: ' + type(error).__name__}
        # PostToolUse does not support allow/deny. No success receipt is invented;
        # the absent/indeterminate protected receipt blocks progression and replay.
        return {}
