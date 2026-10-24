#!/usr/bin/env python3
"""Prepare one public Git-bound file overlay; never build or alter runtime."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
import tempfile

REVISION = 'e6283962b57de53bbb4f69dc4ffa72ed67b21885'
SOURCE_PATH = 'core/market_intelligence/private_pipeline_foundation.py'
BASE_IMAGE = 'sha256:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6'
BASE_TAG = 'capture2-observability-base:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6'
SOURCE_SHA = '2b9bcc1b92626cbc1378003bad75f68fb55826cacc45c46e31aadf07be92965f'
SOURCE_BYTES = 35143
OLD = b'''    except (OSError, ValueError, sqlite3.Error):
        print(
            safe_json({"status": "fail", "reason_code": "runtime_dependency_failure"}),
            file=sys.stderr,
        )
        return 1
'''
NEW = b'''    except (OSError, ValueError, sqlite3.Error) as exc:
        # Static bounded code identity only: never message, locals or paths.
        allowed_types = {
            "OSError", "ValueError", "FileNotFoundError", "PermissionError",
            "NotADirectoryError", "IsADirectoryError", "FileExistsError",
            "TimeoutError", "ConnectionError", "ConnectionResetError",
            "ConnectionRefusedError", "ConnectionAbortedError", "BrokenPipeError",
            "BlockingIOError", "InterruptedError", "UnicodeDecodeError",
            "Error", "DatabaseError", "OperationalError", "IntegrityError",
            "ProgrammingError", "InterfaceError", "DataError", "NotSupportedError",
        }
        name = type(exc).__name__
        evidence = {"status": "fail", "reason_code": "runtime_dependency_failure",
                    "error_type": name if len(name) <= 64 and name in allowed_types else "OtherDependencyError",
                    "origin": "other_runtime", "origin_line": 0}
        origins = {
            "/core/market_intelligence/private_pipeline_foundation.py": "pipeline_foundation",
            "/core/market_intelligence/private_capture.py": "private_capture",
            "/core/market_intelligence/private_capture_service.py": "capture_service",
            "/core/market_intelligence/private_capture_telegram.py": "telegram_capture",
            "/telethon/sessions/sqlite.py": "telethon_sqlite_session",
            "/telethon/client/telegrambaseclient.py": "telethon_client",
            "/telethon/client/auth.py": "telethon_auth",
            "/telethon/network/connection/connection.py": "telethon_connection",
        }
        trace = exc.__traceback__
        for _ in range(64):
            if trace is None:
                break
            filename = trace.tb_frame.f_code.co_filename
            for suffix, origin in origins.items():
                if filename.endswith(suffix) and 1 <= trace.tb_lineno <= 1000000:
                    evidence.update(origin=origin, origin_line=trace.tb_lineno)
            trace = trace.tb_next
        if trace is not None:
            evidence.update(origin="trace_depth_limit", origin_line=0)
        print(safe_json(evidence), file=sys.stderr)
        return 1
'''
DOCKERFILE = ('FROM ' + BASE_TAG + '\n'
              'COPY --chown=10001:10001 private_pipeline_foundation.py '
              '/app/core/market_intelligence/private_pipeline_foundation.py\n').encode()


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def public_source(repo):
    def bounds():
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(['/usr/bin/git', '--no-optional-locks', '-C', str(repo), 'show', REVISION + ':' + SOURCE_PATH],
                           stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                           check=True, timeout=10, preexec_fn=bounds)
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('public_git_source_unavailable') from None
        require(output.tell() <= 65536, 'public_source_bound')
        output.seek(0)
        return output.read(65537)


def patch_source(raw):
    require(len(raw) == SOURCE_BYTES and sha256(raw).hexdigest() == SOURCE_SHA, 'actual_base_source_mismatch')
    require(raw.count(OLD) == 1, 'exact_handler_missing')
    result = raw.replace(OLD, NEW, 1)
    compile(result, SOURCE_PATH, 'exec')  # Parse only; never import application code.
    return result


def write(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, 'artifact_write_stalled')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare(repo, output=None):
    raw = patch_source(public_source(repo))
    manifest = {'schema': 'capture2_observability_overlay/1', 'source_commit': REVISION,
                'source_path': SOURCE_PATH, 'base_image_id': BASE_IMAGE, 'required_local_base_tag': BASE_TAG,
                'original_source_sha256': SOURCE_SHA, 'patched_source_sha256': sha256(raw).hexdigest(),
                'changed_runtime_paths': ['/app/' + SOURCE_PATH], 'runtime_uid': 10001, 'runtime_gid': 10001,
                'runtime_file_mode': '0644', 'error_fields': ['error_type', 'origin', 'origin_line'],
                'exception_message_included': False, 'traceback_included': False,
                'build_performed': False, 'runtime_changed': False}
    if output is None:
        return manifest
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent, 'output_redirected')
    for parent in output.parents:
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid in {0, os.geteuid()} and not value.st_mode & 0o022, 'unsafe_output_parent')
    require(not os.path.lexists(output), 'existing_output_refused')
    output.mkdir(mode=0o700)
    write(output / 'private_pipeline_foundation.py', raw, 0o644)
    write(output / 'Dockerfile', DOCKERFILE)
    write(output / 'overlay.json', (json.dumps(manifest, sort_keys=True) + '\n').encode())
    fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo, args.output), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"refused","code":"capture2_observability_preparation_refused"}', file=sys.stderr)
        raise SystemExit(1)
