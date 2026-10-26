import os
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.request import urlopen
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUBPROCESS_ENV = {
    'CERTS_HOST_PATH': '/tmp/trading-bot-cert-placeholder',
    'POSTGRES_USER': 'postgres',
    'POSTGRES_PASSWORD': 'postgres',
    'POSTGRES_DB': 'trading_bot',
}


def run_checked(command: list[str], *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key, value in DEFAULT_SUBPROCESS_ENV.items():
        env.setdefault(key, value)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def resolve_docker_compose_command() -> list[str] | None:
    if shutil.which('docker') is not None:
        docker_compose = run_checked(['docker', 'compose', 'version'])
        if docker_compose.returncode == 0:
            return ['docker', 'compose']

    if shutil.which('docker-compose') is not None:
        legacy_compose = run_checked(['docker-compose', 'version'])
        if legacy_compose.returncode == 0:
            return ['docker-compose']

    return None


class DeploySurfaceSmokeTests(unittest.TestCase):
    def security_os_function(self, source: str, name: str) -> str:
        marker = name + '() {'
        return marker + source.split(marker, 1)[1].split('\n}', 1)[0] + '\n}\n'

    def security_os_shell(self, shell: str, project: Path, **values: str):
        environment = {
            'PATH': os.defpath,
            'LC_ALL': 'C.UTF-8',
            'APP_ENV_FILE': str(REPO_ROOT / 'config/unit-test.env.example'),
            'CASE_ROOT': str(project),
            'TRACE_FILE': str(project / 'trace'),
            **values,
        }
        with patch('socket.socket', side_effect=AssertionError('network denied')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('network denied')), \
             patch('urllib.request.urlopen', side_effect=AssertionError('network denied')), \
             patch('os.system', side_effect=AssertionError('ambient shell denied')):
            result = subprocess.run(
                ['bash', '--noprofile', '--norc', '-euc', shell],
                cwd=project, env=environment, capture_output=True, text=True, timeout=10,
            )
        trace = project / 'trace'
        return result, trace.read_text().splitlines() if trace.exists() else []

    def test_security_os_runtime_hooks_are_offline_readonly_and_use_exact_installer_caps(self):
        expected = [
            '/usr/bin/setpriv', '--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid',
            '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
            '/usr/local/bin/python3', '-I', '-B', '/app/scripts/prepare_app_security_os_cache.py',
            '--install-image-cache', '--cache-dir', '/capsule',
        ]
        for name in ('Dockerfile', 'Dockerfile.iran'):
            source = (REPO_ROOT / name).read_text()
            runtime = source.split('FROM patched-base AS runtime', 1)[1]
            hook = next(line for line in runtime.replace('\\\n', ' ').splitlines() if line.startswith('RUN ') and '--install-image-cache' in line)
            flags, argv = hook.split('[', 1)
            self.assertEqual(flags.split(), [
                'RUN', '--network=none',
                '--mount=type=bind,source=pip_packages/security-os-capsule,target=/capsule,ro',
                '--mount=type=tmpfs,target=/tmp',
            ])
            self.assertEqual(json.loads('[' + argv), expected)
            self.assertLess(runtime.index('python -m pip check'), runtime.index('--install-image-cache'))
            self.assertLess(runtime.index('COPY scripts/ ./scripts/'), runtime.index('--install-image-cache'))
            self.assertLess(runtime.index('--install-image-cache'), runtime.index('scripts/verify_image_trust.py'))
            self.assertNotIn('COPY pip_packages/security-os-capsule', runtime)
        for name in ('scripts/bootstrap_ci_backend.sh', 'scripts/deploy_staging.sh'):
            self.assertIn('export DOCKER_BUILDKIT=1', (REPO_ROOT / name).read_text())
        online = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text()
        self.assertIn('DOCKER_BUILDKIT=1 docker build', self.security_os_function(online, 'build_release'))

    def test_security_os_foreign_build_cache_hits_and_prebuilt_mode_prepare_before_fingerprint(self):
        source = (REPO_ROOT / 'deploy.sh').read_text()
        functions = ''.join(self.security_os_function(source, name) for name in ('prepare_app_security_os_cache', 'deploy_foreign'))
        shell = functions + r'''
record() { printf '%s\n' "$*" >> "$TRACE_FILE"; }
print_header() { :; }
verify_official_source_still_frozen() { :; }
ensure_local_host_timezone() { :; }
resolve_local_compose_cmd() { LOCAL_COMPOSE_CMD=owned-test-double; }
python3() {
    if [[ " $* " != *" --check "* ]]; then
        test -d "$CASE_ROOT/pip_packages" || return 96
    fi
    record "cache $*"; return "$CACHE_FAILURE"
}
foreign_image_signature() { record fingerprint; printf '%s\n' "$SIGNATURE"; }
run_with_local_resource_guard() { record build; }
docker() {
    case "$*" in
        *org.opencontainers.image.revision*) printf '%s\n' "$PRODUCTION_RELEASE_SHA" ;;
        *io.gold-trade.release.tree*) printf '%s\n' "$PRODUCTION_RELEASE_TREE" ;;
        *io.gold-trade.release.input-signature*) printf '%s\n' "$SIGNATURE" ;;
        *'--format {{.Id}}'*) printf '%s\n' "$PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID" ;;
        'image inspect trading_bot_base') : ;;
        *) return 97 ;;
    esac
}
PROJECT_DIR="$CASE_ROOT"
DEPLOY_STATE_DIR="$CASE_ROOT/state"
FOREIGN_IMAGE_SIGNATURE_FILE="$DEPLOY_STATE_DIR/foreign-image.signature"
SIGNATURE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
DEPLOY_FORCE_REBUILD=0
PRODUCTION_PREBUILD_ONLY=1
PRODUCTION_OFFICIAL_DEPLOY_AUTHORIZED="$PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE"
PRODUCTION_EXPECTED_FOREIGN_IMAGE_ID=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
PRODUCTION_EXPECTED_FOREIGN_IMAGE_SIGNATURE="$SIGNATURE"
PRODUCTION_RELEASE_SHA=cccccccccccccccccccccccccccccccccccccccc
PRODUCTION_RELEASE_TREE=dddddddddddddddddddddddddddddddddddddddd
deploy_foreign
'''
        for prebuilt, hit, failure in (('0', False, '0'), ('0', True, '0'), ('1', True, '0'), ('0', False, '17'), ('1', True, '17')):
            with self.subTest(prebuilt=prebuilt, hit=hit, failure=failure), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                if hit:
                    (project / 'state').mkdir()
                    (project / 'state/foreign-image.signature').write_text('a' * 64 + '\n')
                result, events = self.security_os_shell(shell, project,
                    PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE=prebuilt, CACHE_FAILURE=failure)
                self.assertTrue(events[0].startswith('cache '))
                self.assertEqual('--check' in events[0].split(), prebuilt == '1')
                self.assertIn(str(project / 'pip_packages/security-os-capsule'), events[0])
                self.assertEqual((project / 'pip_packages').exists(), prebuilt == '0')
                if failure != '0':
                    self.assertEqual(result.returncode, 17, result.stderr)
                    self.assertEqual(len(events), 1)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    caches=[event for event in events if event.startswith('cache ')]
                    self.assertEqual(len(caches),3)
                    self.assertTrue(all(('--check' in event.split())==(prebuilt=='1')for event in caches))
                    self.assertTrue(all(events.index(event)<events.index('fingerprint')for event in caches))
                    self.assertLess(0, events.index('fingerprint'))
                    self.assertEqual('build' in events, not hit)

    def test_security_os_iran_legacy_cache_precedes_transfer_and_build(self):
        source = (REPO_ROOT / 'deploy.sh').read_text()
        body = self.security_os_function(source, 'deploy_iran')
        self.assertIn('prepare_app_security_os_cache iran', body)
        self.assertLess(body.index('prepare_app_security_os_cache'), body.index('rsync -avz --delete'))
        self.assertLess(body.index('prepare_app_security_os_cache'), body.index('docker build -f Dockerfile.iran'))

    def test_security_os_iran_prepare_ignores_foreign_prebuilt_flag_and_creates_cold_parent(self):
        source = (REPO_ROOT / 'deploy.sh').read_text()
        shell = self.security_os_function(source, 'prepare_app_security_os_cache') + r'''
python3() {
    test -d "$CASE_ROOT/pip_packages" || return 96
    printf '%s\n' "$*" >> "$TRACE_FILE"
}
PROJECT_DIR="$CASE_ROOT"
prepare_app_security_os_cache iran
'''
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertFalse((project / 'pip_packages').exists())
            result, events = self.security_os_shell(shell, project, PRODUCTION_REQUIRE_PREBUILT_FOREIGN_IMAGE='1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / 'pip_packages').is_dir())
            self.assertEqual(len(events), 3)
            self.assertTrue(all('--check'not in event.split()for event in events))
            self.assertNotIn('--check', events[0].split())
            self.assertIn(str(project / 'pip_packages/security-os-capsule'), events[0])

    def test_security_os_online_prepares_both_caches_before_context_and_signature_even_on_hit(self):
        source = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text()
        function = self.security_os_function(source, 'build_release')
        shell = function + r'''
record() { printf '%s\n' "$*" >> "$TRACE_FILE"; }
ensure_frontend_dist() { :; }
verify_frontend_release_contracts() { :; }
log() { :; }
die() { return 98; }
prepare_pip_packages() { mkdir -p "$2"; }
python3() {
    record "cache $*"
    if [[ "$CACHE_FAILURE" == 17 && "$*" == *pip_packages-amd64/security-os-capsule* ]]; then return 17; fi
}
rsync() { record context-copy; local destination="${@: -1}"; [[ "$destination" == "$RELEASE_TMP_DIR/"* ]]; mkdir -p "$destination"; }
build_image_bundle_signature() { record fingerprint; printf '%s\n' "$SIGNATURE"; }
iran_release_image_matches() { return 0; }
ensure_buildx_for_target() { :; }
docker() {
    case "$1" in
        build) record build ;;
        pull) : ;;
        save) touch "$LOCAL_IMAGE_BUNDLE" ;;
        *) return 97 ;;
    esac
}
LOCAL_PROJECT_DIR="$CASE_ROOT/project"
LOCAL_DIST_DIR="$CASE_ROOT/frontend-dist"
RELEASE_TMP_DIR="$CASE_ROOT/release"
LOCAL_IMAGE_BUNDLE="$RELEASE_TMP_DIR/images.tar"
LOCAL_IMAGE_SIGNATURE_FILE="$RELEASE_TMP_DIR/images.signature"
LOCAL_HOST_ARCH=amd64
IRAN_HOST_ARCH=amd64
IRAN_IMAGE_PLATFORM=linux/amd64
IRAN_FORCE_RELEASE_REFRESH=0
RELEASE_SHA=cccccccccccccccccccccccccccccccccccccccc
PRODUCTION_RELEASE_TREE=dddddddddddddddddddddddddddddddddddddddd
SIGNATURE=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
build_release
'''
        for hit, failure in ((False, '0'), (True, '0'), (False, '17')):
            with self.subTest(hit=hit, failure=failure), tempfile.TemporaryDirectory() as directory:
                project = Path(directory)
                for name in ('project', 'frontend-dist', 'release'):
                    (project / name).mkdir()
                if hit:
                    (project / 'release/images.tar').write_bytes(b'public test-double bundle')
                    (project / 'release/images.signature').write_text('a' * 64 + '\n')
                result, events = self.security_os_shell(shell, project, CACHE_FAILURE=failure)
                caches = [event for event in events if event.startswith('cache ')]
                self.assertEqual(len(caches), 4 if failure != '0' else 6)
                self.assertIn(str(project / 'project/pip_packages/security-os-capsule'), caches[0])
                self.assertIn(str(project / 'project/pip_packages/sqlite-wal-capsule'), caches[1])
                self.assertIn(str(project / 'project/pip_packages/tar-acl-capsule'), caches[2])
                self.assertIn(str(project / 'release/pip_packages-amd64/security-os-capsule'), caches[3])
                if failure != '0':
                    self.assertEqual(result.returncode, 17, result.stderr)
                    self.assertEqual(events, caches)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertIn(str(project / 'release/pip_packages-amd64/sqlite-wal-capsule'), caches[4])
                    self.assertIn(str(project / 'release/pip_packages-amd64/tar-acl-capsule'), caches[5])
                    self.assertLess(events.index(caches[-1]), events.index('context-copy'))
                    self.assertLess(events.index(caches[-1]), events.index('fingerprint'))
                    self.assertEqual('build' in events, not hit)

    def test_security_os_prebuilt_online_verification_is_check_only(self):
        source = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text()
        shell = self.security_os_function(source, 'verify_prepared_wheel_cache') + r'''
expected_wheel_cache_signature() { printf 'public-hash\n'; }
die() { return 98; }
python3() { printf '%s\n' "$*" >> "$TRACE_FILE"; }
LOCAL_PROJECT_DIR="$CASE_ROOT"
verify_prepared_wheel_cache amd64 "$CASE_ROOT/cache"
'''
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory); cache = project / 'cache'; cache.mkdir()
            (cache / '.requirements_hash').write_text('public-hash\n')
            (cache / 'public-test-double.whl').write_bytes(b'inert')
            result, events = self.security_os_shell(shell, project)
            self.assertEqual(result.returncode, 0, result.stderr)
            os_cache = next(event for event in events if 'prepare_app_security_os_cache.py' in event)
            self.assertIn('--check', os_cache.split())
            self.assertIn(str(cache / 'security-os-capsule'), os_cache)
            for helper,directory in(('prepare_sqlite_wal_os_cache.py','sqlite-wal-capsule'),('prepare_tar_acl_os_cache.py','tar-acl-capsule')):
                event=next(event for event in events if helper in event)
                self.assertIn('--check',event.split());self.assertIn(str(cache/directory),event)

    def test_security_os_cache_and_lock_drift_change_both_real_image_signatures(self):
        legacy = (REPO_ROOT / 'deploy.sh').read_text()
        online = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text()
        shell = ''.join(self.security_os_function(legacy, name) for name in
            ('hash_file_or_dir', 'hash_release_inputs', 'foreign_image_signature'))
        shell += ''.join(self.security_os_function(online, name) for name in
            ('hash_context_entry', 'build_image_bundle_signature'))
        shell += '''
PROJECT_DIR="$CASE_ROOT"
IRAN_IMAGE_PLATFORM=linux/amd64
IRAN_HOST_ARCH=amd64
foreign_image_signature
build_image_bundle_signature "$CASE_ROOT"
'''
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'scripts').mkdir()
            capsule = project / 'pip_packages/security-os-capsule'; capsule.mkdir(parents=True)
            paths = [capsule / 'manifest.json', capsule / 'public-test-double.deb',
                project / 'scripts/app_security_os_packages.lock.json',
                project / 'scripts/prepare_app_security_os_cache.py']
            for path in paths:
                path.write_bytes(b'public fingerprint fixture v1')
            result, _ = self.security_os_shell(shell, project)
            self.assertEqual(result.returncode, 0, result.stderr)
            previous = result.stdout.splitlines()
            self.assertEqual(len(previous), 2)
            for path in paths:
                path.write_bytes(b'public fingerprint fixture v2')
                result, _ = self.security_os_shell(shell, project)
                self.assertEqual(result.returncode, 0, result.stderr)
                current = result.stdout.splitlines()
                self.assertEqual(len(current), 2)
                self.assertTrue(all(before != after for before, after in zip(previous, current)), path)
                previous = current

    def test_security_os_staging_build_entries_prepare_before_any_compose_build(self):
        source = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text()
        names = ('prepare_app_security_os_cache', 'start_sync_worker', 'build_runtime_image', 'deploy')
        functions = ''.join(self.security_os_function(source, name) for name in names)
        up = source.split('    up)\n', 1)[1].split('        ;;', 1)[0]
        shell = functions + r'''
record() { printf '%s\n' "$*" >> "$TRACE_FILE"; }
python3() {
    test -d "$CASE_ROOT/pip_packages" || return 96
    record "cache $*"; return "$CACHE_FAILURE"
}
compose() { record "compose $*"; }
for function in check validate_staging_runtime_role ensure_env ensure_runtime_env_values require_prebuilt_image_tag_only build_frontend require_prebuilt_image remove_legacy_compose_stateless_containers assert_telegram_bot_split_topology wait_for_app_health wait_for_foreign_app_health_if_enabled install_nginx health; do
    eval "$function() { :; }"
done
PROJECT_DIR="$CASE_ROOT"
STAGING_ENABLE_BOT=0
STAGING_FOREIGN_ONLY=0
case "$ACTION" in
    up) set -- up;
''' + up + r'''
        ;;
    *) "$ACTION" ;;
esac
'''
        for action in ('build_runtime_image', 'start_sync_worker', 'deploy', 'up'):
            for failure in ('0', '17'):
                with self.subTest(action=action, failure=failure), tempfile.TemporaryDirectory() as directory:
                    result, events = self.security_os_shell(shell, Path(directory), ACTION=action, CACHE_FAILURE=failure)
                    self.assertTrue(events[0].startswith('cache '))
                    if failure != '0':
                        self.assertEqual(result.returncode, 17, result.stderr)
                        self.assertEqual(len(events), 1)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                        self.assertTrue(any(event.startswith('compose ') and 'build' in event for event in events))
        for name in ('start_prebuilt_producers', 'start_prebuilt_bot'):
            self.assertNotIn('prepare_app_security_os_cache', self.security_os_function(source, name))

    def test_production_application_code_is_image_only(self):
        foreign = (REPO_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
        iran = (REPO_ROOT / 'docker-compose.iran.yml').read_text(encoding='utf-8')
        forbidden_mounts = (
            '- .:/app',
            './api:/app/api',
            './bot:/app/bot',
            './core:/app/core',
            './src:/app/src',
            './models:/app/models',
            './migrations:/app/migrations',
            './scripts:/app/scripts',
            './main.py:/app/main.py',
            './schemas.py:/app/schemas.py',
            './trading_settings.json:/app/trading_settings.json',
        )
        for compose_path, source in (
            ('docker-compose.yml', foreign),
            ('docker-compose.iran.yml', iran),
        ):
            with self.subTest(compose=compose_path):
                for mount in forbidden_mounts:
                    self.assertNotIn(mount, source)
        for dockerfile in ('Dockerfile', 'Dockerfile.iran'):
            source = (REPO_ROOT / dockerfile).read_text(encoding='utf-8')
            with self.subTest(dockerfile=dockerfile):
                self.assertIn('COPY trading_settings.json .', source)

    def test_stale_js_fallback_is_a_non_executable_410_in_every_deployable_nginx_source(self):
        config_paths = (
            'nginx.conf',
            'deploy/staging/nginx-staging.conf.template',
            'deploy/production/nginx-iran-online.conf.template',
            'deploy/production/nginx-iran-online-https.conf.template',
            'scripts/setup_iran_nginx.sh',
            'scripts/setup_foreign_nginx.sh',
        )

        for relative_path in config_paths:
            source = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
            with self.subTest(relative_path=relative_path):
                block = re.search(
                    r'location @stale_js_chunk \{(?P<body>.*?)\n    \}',
                    source,
                    re.DOTALL,
                )
                self.assertIsNotNone(block)
                body = block.group('body')
                self.assertIn('return 410;', body)
                self.assertNotIn('return 200', body)
                self.assertNotIn('window.location.reload', body)

    def test_invitation_bearing_nginx_routes_disable_access_logs(self):
        config_paths = (
            'nginx.conf',
            'deploy/staging/nginx-staging.conf.template',
            'deploy/production/nginx-iran-online.conf.template',
            'deploy/production/nginx-iran-online-https.conf.template',
            'deploy/production/nginx-iran-recovery-http.conf.template',
            'deploy/production/nginx-iran-recovery-https.conf.template',
            'scripts/setup_iran_nginx.sh',
            'scripts/setup_foreign_nginx.sh',
        )
        for relative_path in config_paths:
            source = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
            with self.subTest(relative_path=relative_path):
                self.assertRegex(
                    source,
                    r'location ~ \^/register\(\?:/\|\$\) \{\s+access_log off;',
                )
                self.assertRegex(
                    source,
                    r'location \^~ /i/ \{\s+access_log off;',
                )
                for location_pattern in (
                    r'location ~ \^/register\(\?:/\|\$\)',
                    r'location \^~ /i/',
                ):
                    block = re.search(
                        rf'{location_pattern} \{{(?P<body>.*?)\n    \}}',
                        source,
                        re.DOTALL,
                    )
                    self.assertIsNotNone(block)
                    self.assertIn(
                        'add_header Referrer-Policy "no-referrer" always;',
                        block.group('body'),
                    )
        for relative_path in config_paths:
            source = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
            with self.subTest(api_relative_path=relative_path):
                self.assertRegex(
                    source,
                    r'location ~ \^/api/invitations/\(lookup\|validate\)/ \{\s+access_log off;',
                )
                api_block = re.search(
                    r'location ~ \^/api/invitations/\(lookup\|validate\)/ '
                    r'\{(?P<body>.*?)\n    \}',
                    source,
                    re.DOTALL,
                )
                self.assertIsNotNone(api_block)
                self.assertIn(
                    'add_header Referrer-Policy "no-referrer" always;',
                    api_block.group('body'),
                )
        foreign_setup = (REPO_ROOT / 'scripts/setup_foreign_nginx.sh').read_text(
            encoding='utf-8'
        )
        for location_pattern in (
            r'location ~ \^/register\(\?:/\|\$\)',
            r'location \^~ /i/',
        ):
            with self.subTest(foreign_location=location_pattern):
                block = re.search(
                    rf'{location_pattern} \{{(?P<body>.*?)\n    \}}',
                    foreign_setup,
                    re.DOTALL,
                )
                self.assertIsNotNone(block)
                body = block.group('body')
                self.assertIn('access_log off;', body)
                self.assertIn('return 404;', body)
                self.assertNotIn('proxy_pass', body)
        root_config = (REPO_ROOT / 'nginx.conf').read_text(encoding='utf-8')
        self.assertIn('error_log /var/log/nginx/trading_bot_error.log warn;', root_config)
        self.assertNotIn('trading_bot_error.log debug', root_config)

    def test_nginx_runtime_logs_normal_route_but_not_invitation_credentials(self):
        if shutil.which('nginx') is None:
            self.skipTest('nginx is not installed')

        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
        except PermissionError:
            self.skipTest('sandbox does not permit local sockets')

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir).chmod(0o755)
            root = Path(temp_dir) / 'root'
            root.mkdir()
            root.chmod(0o755)
            (root / 'index.html').write_text('ok', encoding='utf-8')
            access_log = Path(temp_dir) / 'access.log'
            config_path = Path(temp_dir) / 'nginx.conf'
            pid_path = Path(temp_dir) / 'nginx.pid'
            config_path.write_text(
                'events {}\n'
                'http {\n'
                f'  access_log {access_log};\n'
                '  error_log stderr notice;\n'
                '  server {\n'
                f'    listen 127.0.0.1:{port};\n'
                f'    root {root};\n'
                '    location ~ ^/register(?:/|$) { access_log off; add_header Referrer-Policy "no-referrer" always; try_files /index.html =404; }\n'
                '    location ^~ /i/ { access_log off; add_header Referrer-Policy "no-referrer" always; try_files /index.html =404; }\n'
                '    location ~ ^/api/invitations/(lookup|validate)/ { access_log off; add_header Referrer-Policy "no-referrer" always; return 204; }\n'
                '    location / { try_files $uri /index.html; }\n'
                '  }\n'
                '}\n',
                encoding='utf-8',
            )
            process = subprocess.Popen(
                [
                    'nginx',
                    '-c',
                    str(config_path),
                    '-p',
                    temp_dir,
                    '-g',
                    f'daemon off; pid {pid_path};',
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(40):
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/health?visible=yes', timeout=0.25) as response:
                            self.assertEqual(response.status, 200)
                        break
                    except OSError:
                        if process.poll() is not None:
                            stderr = process.stderr.read() if process.stderr else ''
                            self.fail(f'nginx exited before request: {stderr}')
                        time.sleep(0.05)
                else:
                    self.fail('nginx did not become ready')

                with urlopen(
                    f'http://127.0.0.1:{port}/register?token=INV-runtime-secret',
                    timeout=1,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
                with urlopen(
                    f'http://127.0.0.1:{port}/register/?token=INV-trailing-slash-secret',
                    timeout=1,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
                with urlopen(
                    f'http://127.0.0.1:{port}/register//?token=INV-repeated-slash-secret',
                    timeout=1,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
                with urlopen(
                    f'http://127.0.0.1:{port}/register%2F?token=INV-encoded-slash-secret',
                    timeout=1,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
                with urlopen(
                    f'http://127.0.0.1:{port}/i/runtime-short-secret',
                    timeout=1,
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
                for api_path in (
                    '/api/invitations/lookup/INV-api-path-secret',
                    '/api/invitations/validate/INV-api-query?token=INV-api-query-secret',
                ):
                    with urlopen(
                        f'http://127.0.0.1:{port}{api_path}',
                        timeout=1,
                    ) as response:
                        self.assertEqual(response.status, 204)
                        self.assertEqual(response.headers.get('Referrer-Policy'), 'no-referrer')
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

            logged = access_log.read_text(encoding='utf-8')
            self.assertIn('/health?visible=yes', logged)
            self.assertNotIn('INV-runtime-secret', logged)
            self.assertNotIn('INV-trailing-slash-secret', logged)
            self.assertNotIn('INV-repeated-slash-secret', logged)
            self.assertNotIn('INV-encoded-slash-secret', logged)
            self.assertNotIn('runtime-short-secret', logged)
            self.assertNotIn('INV-api-path-secret', logged)
            self.assertNotIn('INV-api-query-secret', logged)

    def test_run_checked_applies_default_and_extra_environment(self):
        with patch(__name__ + '.subprocess.run') as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=['echo'], returncode=0, stdout='ok', stderr='')
            result = run_checked(['echo'], extra_env={'CUSTOM_FLAG': '1'})

        self.assertEqual(result.returncode, 0)
        env = run_mock.call_args.kwargs['env']
        self.assertEqual(env['CUSTOM_FLAG'], '1')
        self.assertEqual(env['POSTGRES_USER'], DEFAULT_SUBPROCESS_ENV['POSTGRES_USER'])

    def test_resolve_docker_compose_command_helper_paths(self):
        with patch(__name__ + '.shutil.which', side_effect=['/usr/bin/docker', '/usr/bin/docker-compose']), patch(
            __name__ + '.run_checked',
            side_effect=[
                subprocess.CompletedProcess(args=['docker', 'compose', 'version'], returncode=1, stdout='', stderr=''),
                subprocess.CompletedProcess(args=['docker-compose', 'version'], returncode=0, stdout='ok', stderr=''),
            ],
        ):
            self.assertEqual(resolve_docker_compose_command(), ['docker-compose'])

        with patch(__name__ + '.shutil.which', return_value=None):
            self.assertIsNone(resolve_docker_compose_command())

    def test_skip_branches_for_missing_host_tools(self):
        with patch(__name__ + '.shutil.which', return_value=None):
            with self.assertRaises(unittest.SkipTest):
                self.test_makefile_parses_with_noop_status_target()

        with patch(__name__ + '.resolve_docker_compose_command', return_value=None):
            with self.assertRaises(unittest.SkipTest):
                self.test_compose_files_render_valid_config()

        with patch(__name__ + '.shutil.which', side_effect=lambda tool: None if tool in {'docker', 'nginx'} else '/usr/bin/make'):
            with self.assertRaises(unittest.SkipTest):
                self.test_dockerfiles_pass_docker_build_check()
            with self.assertRaises(unittest.SkipTest):
                self.test_nginx_config_passes_syntax_validation()

    def test_docker_build_check_uses_buildx_and_skip_when_check_mode_missing(self):
        with patch(__name__ + '.shutil.which', return_value='/usr/bin/docker'), patch(
            __name__ + '.run_checked',
            side_effect=[
                subprocess.CompletedProcess(args=['docker', 'build', '--help'], returncode=0, stdout='plain help', stderr=''),
                subprocess.CompletedProcess(args=['docker', 'buildx', 'build', '--help'], returncode=0, stdout='supports --check', stderr=''),
                subprocess.CompletedProcess(args=['docker', 'buildx', 'build', '--check'], returncode=0, stdout='', stderr=''),
                subprocess.CompletedProcess(args=['docker', 'buildx', 'build', '--check'], returncode=0, stdout='', stderr=''),
            ],
        ):
            self.test_dockerfiles_pass_docker_build_check()

        with patch(__name__ + '.shutil.which', return_value='/usr/bin/docker'), patch(
            __name__ + '.run_checked',
            side_effect=[
                subprocess.CompletedProcess(args=['docker', 'build', '--help'], returncode=0, stdout='plain help', stderr=''),
                subprocess.CompletedProcess(args=['docker', 'buildx', 'build', '--help'], returncode=0, stdout='plain help', stderr=''),
            ],
        ):
            with self.assertRaises(unittest.SkipTest):
                self.test_dockerfiles_pass_docker_build_check()

    def test_deploy_script_has_valid_bash_syntax(self):
        result = run_checked(['bash', '-n', 'deploy.sh'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_staging_deploy_script_has_valid_bash_syntax(self):
        result = run_checked(['bash', '-n', 'scripts/deploy_staging.sh'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_staging_iran_nginx_acl_is_narrow_and_env_stays_private(self):
        source = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(
            encoding='utf-8'
        )
        acl = source.split('ensure_iran_nginx_web_root_acl() {', 1)[1].split(
            '\n}', 1
        )[0]
        self.assertIn('trading_bot_staging_iran', acl)
        self.assertIn('setfacl -m "u:${worker_user}:--x" "$PROJECT_DIR"', acl)
        self.assertIn('test -r "$STAGING_FRONTEND_DIST_DIR/index.html"', acl)
        self.assertIn('test ! -r "$ENV_FILE"', acl)
        self.assertNotIn('chmod 0711', acl)

    def test_staging_prebuilt_commands_never_rebuild_while_starting(self):
        source = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(
            encoding='utf-8'
        )
        for command in (
            'build-image)',
            'start-prebuilt-producers)',
            'start-prebuilt-bot)',
        ):
            self.assertIn(command, source)
        producers = source.split('start_prebuilt_producers() {', 1)[1].split(
            '\n}', 1
        )[0]
        bot = source.split('start_prebuilt_bot() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('--no-build', producers)
        self.assertIn('--no-build', bot)
        self.assertNotIn(' --build', producers)
        self.assertNotIn(' --build', bot)
        self.assertIn('require_prebuilt_image', producers)
        self.assertIn('require_prebuilt_image', bot)

    def test_staging_iran_sync_start_targets_only_iran_profile_service(self):
        source = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(
            encoding='utf-8'
        )
        self.assertIn('compose up -d --no-build sync_worker', source)
        self.assertIn('compose up -d --build sync_worker', source)
        self.assertNotIn(
            'compose --profile staging-sync up -d --no-build sync_worker',
            source,
        )

    def test_docker_build_context_excludes_all_runtime_env_and_host_data(self):
        ordered_patterns = [
            line.strip()
            for line in (REPO_ROOT / '.dockerignore')
            .read_text(encoding='utf-8')
            .splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        ]
        patterns = set(ordered_patterns)
        for pattern in (
            '.env*',
            '**/.env*',
            '*.env',
            '**/*.env',
            '**/online.env',
            '/mutants/',
            'audit_trail/',
            'app_logs/',
            'uploads/',
        ):
            self.assertIn(pattern, patterns)
        # Documentation examples remain available to Docker builds even though
        # every runtime env spelling and local mutation artifact is excluded.
        for exception in (
            '!.env.example',
            '!**/.env.example',
            '!**/*.env.example',
            '!**/online.env.example',
        ):
            self.assertIn(exception, patterns)
        self.assertNotIn('online.env.example', patterns)
        self.assertNotIn('**/online.env.example', patterns)
        # Dockerignore is last-match-wins, so exceptions must remain below the
        # broad exclusions they override.
        self.assertGreater(
            ordered_patterns.index('!.env.example'),
            ordered_patterns.index('.env*'),
        )
        self.assertGreater(
            ordered_patterns.index('!**/.env.example'),
            ordered_patterns.index('**/.env*'),
        )
        self.assertGreater(
            ordered_patterns.index('!**/online.env.example'),
            ordered_patterns.index('**/online.env'),
        )
        for example_path in (
            'config/unit-test.env.example',
            'deploy/production/online.env.example',
        ):
            self.assertTrue((REPO_ROOT / example_path).is_file())

    def test_staging_deploy_normalizes_existing_frontend_permissions(self):
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            dist_dir = Path(temp_dir) / 'dist'
            dist_dir.mkdir(mode=0o700)
            index_path = dist_dir / 'index.html'
            index_path.write_text('<!doctype html>', encoding='utf-8')
            index_path.chmod(0o600)

            result = run_checked(
                ['scripts/deploy_staging.sh', 'build-frontend'],
                extra_env={
                    'STAGING_FRONTEND_DIST_DIR': str(dist_dir),
                    'STAGING_SKIP_FRONTEND_BUILD': '1',
                },
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertEqual(dist_dir.stat().st_mode & 0o777, 0o755)
            self.assertEqual(index_path.stat().st_mode & 0o777, 0o644)

    def test_staging_deploy_surface_excludes_object_storage_bridge(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')
        staging_readme = (REPO_ROOT / 'deploy/staging/README.md').read_text(encoding='utf-8')

        forbidden_tokens = (
            'STAGING_OBJECT_STORAGE',
            'STAGING_OBJECT_RELEASE',
            'ARVAN_OBJECT_STORAGE',
            'object-storage-',
            'object-release-',
            'staging_object_storage',
            'arvan_object_storage',
        )
        for token in forbidden_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, staging_script)
                self.assertNotIn(token, staging_example)
                self.assertNotIn(token, staging_readme)

        for relative_path in (
            'scripts/arvan_object_storage_probe.py',
            'scripts/staging_object_storage_artifact.py',
            'scripts/staging_object_storage_release.py',
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((REPO_ROOT / relative_path).exists())

    def test_legacy_staging_recreate_removes_only_stateless_services(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        function_body = staging_script.split('remove_legacy_compose_stateless_containers() {', 1)[1].split('\n}', 1)[0]

        self.assertIn('if [[ "${compose_cmd[0]}" != "docker-compose" ]]', function_body)
        self.assertIn(
            'for service in migration app foreign_app bot bot_executor sync_worker foreign_sync_worker',
            function_body,
        )
        self.assertNotIn(' service in db ', function_body)
        self.assertNotIn(' service in redis ', function_body)
        self.assertIn('remove_legacy_compose_stateless_containers\n', staging_script)

    def test_staging_deploy_fails_closed_on_role_ambiguity_and_removes_opposite_role_services(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        role_body = staging_script.split('validate_staging_runtime_role() {', 1)[1].split('\n}', 1)[0]
        cleanup_body = staging_script.split('remove_legacy_compose_stateless_containers() {', 1)[1].split('\n}', 1)[0]

        self.assertIn('STAGING_ENABLE_BOT" != "$STAGING_FOREIGN_ONLY', role_body)
        self.assertIn('trading_bot_staging)', role_body)
        self.assertIn('trading_bot_staging_iran)', role_body)
        self.assertGreaterEqual(staging_script.count('validate_staging_runtime_role\n'), 4)
        self.assertIn('conflicting_services=(app sync_worker)', cleanup_body)
        self.assertIn('conflicting_services=(foreign_app bot bot_executor foreign_sync_worker)', cleanup_body)
        self.assertNotIn('conflicting_services=(db', cleanup_body)
        self.assertNotIn('conflicting_services=(redis', cleanup_body)

    def test_runtime_image_uses_uncached_official_debian_security_mirror(self):
        for dockerfile_name in ('Dockerfile', 'Dockerfile.iran'):
            with self.subTest(dockerfile=dockerfile_name):
                dockerfile = (REPO_ROOT / dockerfile_name).read_text(encoding='utf-8')
                self.assertIn(
                    'https://security.debian.org/debian-security',
                    dockerfile,
                )
                self.assertIn('Acquire::https::No-Cache=true update', dockerfile)
                self.assertIn('Acquire::https::No-Cache=true install', dockerfile)

    def test_staging_frontend_dist_isolated_from_production_artifact(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        vite_config = (REPO_ROOT / 'frontend/vite.config.ts').read_text(encoding='utf-8')
        staging_nginx = (REPO_ROOT / 'deploy/staging/nginx-staging.conf.template').read_text(encoding='utf-8')
        staging_compose = (REPO_ROOT / 'deploy/staging/docker-compose.staging.yml').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')
        dockerfile = (REPO_ROOT / 'Dockerfile').read_text(encoding='utf-8')
        gitignore = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8')

        self.assertIn('process.env.FRONTEND_BUILD_OUT_DIR', vite_config)
        self.assertIn('FRONTEND_BUILD_OUT_DIR="$STAGING_FRONTEND_DIST_DIR"', staging_script)
        self.assertIn('STAGING_SKIP_FRONTEND_BUILD', staging_script)
        self.assertEqual(staging_script.count('normalize_staging_frontend_permissions'), 3)
        self.assertIn('chmod -R u=rwX,go=rX -- "$STAGING_FRONTEND_DIST_DIR"', staging_script)
        self.assertIn('STAGING_FRONTEND_DIST_DIR="${STAGING_FRONTEND_DIST_DIR:-mini_app_dist_staging}"', staging_script)
        self.assertIn('STAGING_INTERNAL_FOREIGN_SERVER_URL', staging_script)
        self.assertIn('STAGING_PUBLIC_FOREIGN_SYNC_URL="${STAGING_PUBLIC_FOREIGN_SYNC_URL:-https://staging.362514.ir/foreign-sync}"', staging_script)
        self.assertIn('STAGING_FOREIGN_IRAN_SERVER_URL="${STAGING_FOREIGN_IRAN_SERVER_URL:-https://staging.gold-trade.ir}"', staging_script)
        self.assertIn('STAGING_FOREIGN_FRONTEND_URL="${STAGING_FOREIGN_FRONTEND_URL:-$STAGING_FOREIGN_IRAN_SERVER_URL}"', staging_script)
        self.assertIn('STAGING_FOREIGN_PUBLIC_SURFACE_GUARD="${STAGING_FOREIGN_PUBLIC_SURFACE_GUARD:-$STAGING_ENABLE_BOT}"', staging_script)
        self.assertIn('STAGING_FOREIGN_ONLY="${STAGING_FOREIGN_ONLY:-0}"', staging_script)
        self.assertIn('STAGING_MIGRATION_SERVER_MODE="$migration_server_mode"', staging_script)
        self.assertIn('STAGING_RELEASE_SHA_OVERRIDE', staging_script)
        self.assertIn('command -v docker-compose', staging_script)
        self.assertIn('STAGING_FOREIGN_APP_PORT="${STAGING_FOREIGN_APP_PORT:-8121}"', staging_script)
        self.assertIn('STAGING_BOT_USERNAME="${STAGING_BOT_USERNAME:-}"', staging_script)
        self.assertIn('BOT_USERNAME=${STAGING_BOT_USERNAME:-staging_bot_placeholder}', staging_script)
        self.assertIn('validate_staging_bot_username', staging_script)
        self.assertIn('set_env_value BOT_USERNAME "$STAGING_BOT_USERNAME"', staging_script)
        self.assertIn('STAGING_FOREIGN_APP_PORT="$STAGING_FOREIGN_APP_PORT"', staging_script)
        self.assertIn('STAGING_FOREIGN_IRAN_SERVER_URL="$STAGING_FOREIGN_IRAN_SERVER_URL"', staging_script)
        self.assertIn('require_staging_peer_url', staging_script)
        self.assertIn('case "$STAGING_INTERNAL_FOREIGN_SERVER_URL" in', staging_script)
        self.assertIn('staging peer URL must start with http:// or https://', staging_script)
        self.assertIn('staging_release_sha', staging_script)
        self.assertIn('STAGING_RELEASE_SHA="$(staging_release_sha)"', staging_script)
        self.assertIn('set_env_value PEER_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"', staging_script)
        self.assertIn('start_sync_worker', staging_script)
        self.assertIn('compose --profile staging-bot --profile staging-sync up -d --build foreign_app bot foreign_sync_worker', staging_script)
        self.assertIn(
            'compose --profile staging-bot --profile staging-sync up -d --build sync_worker foreign_sync_worker',
            staging_script,
        )
        self.assertIn('SERVER_MODE: ${STAGING_MIGRATION_SERVER_MODE:-iran}', staging_compose)
        self.assertIn('python scripts/align_trade_number_sequence.py', staging_compose)
        self.assertIn('compose up -d --build sync_worker', staging_script)
        self.assertIn('set_env_value GERMANY_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"', staging_script)
        self.assertIn('set_env_value AUDIT_TRAIL_PATH /app/audit_trail/audit.jsonl', staging_script)
        self.assertIn('AUDIT_TRAIL_PATH=/app/audit_trail/audit.jsonl', staging_example)
        self.assertIn('ensure_runtime_env_values\n        compose up -d --build "$@"', staging_script)
        self.assertIn('realpath -m "$STAGING_FRONTEND_DIST_DIR"', staging_script)
        self.assertIn('staging frontend dist must not share production mini_app_dist', staging_script)
        self.assertIn('root __FRONTEND_ROOT__;', staging_nginx)
        self.assertIn('__FOREIGN_PUBLIC_SURFACE_GUARD__', staging_nginx)
        self.assertIn('location = /api/config', staging_script)
        self.assertIn('return 404;', staging_script)
        self.assertIn('location = /foreign-sync/api/config', staging_nginx)
        self.assertIn('location = /foreign-sync/api/sync/receive', staging_nginx)
        self.assertIn('auth_basic off;', staging_nginx)
        self.assertIn(
            'offers/internal|auth/internal|invitations/internal|customers/internal',
            staging_nginx,
        )
        self.assertIn(
            '^/foreign-sync/api/(sync|sessions/internal|trades/internal|offers/internal|auth/internal|invitations/internal|customers/internal)',
            staging_nginx,
        )
        self.assertIn('proxy_pass http://127.0.0.1:__FOREIGN_APP_PORT__/api/sync/receive;', staging_nginx)
        self.assertNotIn('root __APP_ROOT__/mini_app_dist;', staging_nginx)
        self.assertIn('FRONTEND_DIST_DIR:', staging_compose)
        self.assertIn('STAGING_FRONTEND_DOCKER_DIST_DIR', staging_compose)
        self.assertIn('STAGING_FOREIGN_APP_PORT', staging_compose)
        self.assertIn('IRAN_SERVER_URL: ${STAGING_FOREIGN_IRAN_SERVER_URL:-https://staging.gold-trade.ir}', staging_compose)
        self.assertIn('FRONTEND_URL: ${STAGING_FOREIGN_FRONTEND_URL:-https://staging.gold-trade.ir}', staging_compose)
        self.assertIn('ARG FRONTEND_DIST_DIR=mini_app_dist', dockerfile)
        self.assertIn('COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/', dockerfile)
        self.assertIn('mini_app_dist_staging/', gitignore)

    def test_staging_env_sets_trusted_proxy_cidrs_for_nginx_container_hop(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')

        self.assertIn('STAGING_TRUSTED_PROXY_CIDRS="${STAGING_TRUSTED_PROXY_CIDRS:-127.0.0.1/32,::1/128,172.16.0.0/12}"', staging_script)
        self.assertIn('TRUSTED_PROXY_CIDRS=$STAGING_TRUSTED_PROXY_CIDRS', staging_script)
        self.assertIn('set_env_value TRUSTED_PROXY_CIDRS "$STAGING_TRUSTED_PROXY_CIDRS"', staging_script)
        self.assertIn('TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.16.0.0/12', staging_example)

    def test_staging_deploy_rejects_shared_production_frontend_dist(self):
        result = run_checked(
            ['scripts/deploy_staging.sh', 'check'],
            extra_env={'STAGING_FRONTEND_DIST_DIR': str(REPO_ROOT / 'mini_app_dist')},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('staging frontend dist must not share production mini_app_dist', result.stderr + result.stdout)

    def test_deploy_script_keeps_foreign_rebuild_steps_cache_aware(self):
        deploy_script = (REPO_ROOT / 'deploy.sh').read_text(encoding='utf-8')

        for expected in (
            'PIP_BOOTSTRAP_REQUIREMENTS',
            'DEPLOY_FORCE_REBUILD',
            'frontend_build_signature',
            'Frontend build inputs unchanged. Skipping npm install/build.',
            'foreign_image_signature',
            'Foreign Docker image inputs unchanged. Skipping docker build.',
            'python3 -m pip download -r "$PIP_BOOTSTRAP_REQUIREMENTS"',
            'python3 -m pip download -r "$PROJECT_DIR/pip_packages/.requirements-without-psycopg2.txt"',
            'scripts/prepare_psycopg2_source.py',
        ):
            self.assertIn(expected, deploy_script)

    def test_release_input_hash_ignores_python_cache_but_detects_source_drift(self):
        deploy_script = (REPO_ROOT / 'deploy.sh').read_text(encoding='utf-8')
        function_body = deploy_script.split('hash_file_or_dir() {', 1)[1].split('\n}', 1)[0]
        shell = f'''hash_file_or_dir() {{{function_body}
}}
PROJECT_DIR="$1"
hash_file_or_dir inputs
'''

        with tempfile.TemporaryDirectory(prefix='deploy-input-signature-') as temporary:
            project = Path(temporary)
            inputs = project / 'inputs'
            cache = inputs / '__pycache__'
            cache.mkdir(parents=True)
            source = inputs / 'module.py'
            bytecode = cache / 'module.cpython-312.pyc'
            source.write_text('VALUE = 1\n', encoding='utf-8')
            bytecode.write_bytes(b'first-cache')

            def signature() -> str:
                result = subprocess.run(
                    ['bash', '-c', shell, 'deploy-hash-test', str(project)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return result.stdout

            initial = signature()
            bytecode.write_bytes(b'rewritten-cache')
            self.assertEqual(signature(), initial)
            source.write_text('VALUE = 2\n', encoding='utf-8')
            self.assertNotEqual(signature(), initial)

    def test_production_iran_deploy_does_not_recreate_stateful_services(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        legacy_script = (REPO_ROOT / 'deploy.sh').read_text(encoding='utf-8')

        for source in (production_script, legacy_script):
            self.assertIn('up -d --no-recreate db redis', source)
            self.assertIn('run --rm --no-deps migration', source)
            self.assertIn('up -d --no-deps', source)
            self.assertIn('app sync_worker', source)

        self.assertNotIn('eval "\\$compose_cmd -f docker-compose.iran.yml up -d \\$wait_args"', production_script)
        self.assertNotIn('up -d --wait --wait-timeout 180"', legacy_script)

    def test_production_deploys_align_trade_numbers_before_starting_apps(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        legacy_script = (REPO_ROOT / 'deploy.sh').read_text(encoding='utf-8')
        foreign_compose = (REPO_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')
        iran_compose = (REPO_ROOT / 'docker-compose.iran.yml').read_text(encoding='utf-8')

        self.assertIn('TRADE_NUMBER_SEQUENCE_ALIGNER=', production_script)
        self.assertIn('Trade-number sequence aligner missing:', production_script)
        self.assertIn('run --rm --no-deps migration', legacy_script)
        foreign_deploy = legacy_script.split('deploy_foreign() {', 1)[1].split('\n}', 1)[0]
        self.assertLess(
            foreign_deploy.index('run --rm --no-deps migration'),
            foreign_deploy.index('Foreign core service startup'),
        )
        self.assertIn('up -d --no-recreate --wait --wait-timeout 180 db redis', foreign_deploy)
        for compose_source in (foreign_compose, iran_compose):
            self.assertIn('python manage.py && python scripts/align_trade_number_sequence.py', compose_source)

    def test_production_receipt_rollout_is_rendered_and_fail_closed(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        production_example = (REPO_ROOT / 'deploy/production/online.env.example').read_text(encoding='utf-8')

        self.assertIn('OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED', production_script)
        self.assertIn('validate_offer_expiry_receipt_env_files', production_script)
        self.assertIn('validate_runtime_release_sha_files', production_script)
        self.assertIn('repair_registry_fingerprint_rollout_quarantine', production_script)
        self.assertIn('Refusing sync-quarantine repair because production release SHAs are not identical', production_script)
        self.assertIn('Refusing sync-quarantine repair because registry fingerprints are not identical', production_script)
        self.assertIn('verify_no_sync_quarantines', production_script)
        self.assertIn('REQUIRE_OFFER_EXPIRY_COMMAND_RECEIPTS=1', production_example)
        self.assertIn('OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED=1', production_example)

    def test_production_release_excludes_runtime_audit_files(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        gitignore = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8')

        self.assertIn('/audit_trail/', gitignore.splitlines())
        self.assertGreaterEqual(production_script.count("--exclude 'audit_trail'"), 2)

    def test_production_preseed_backlog_covers_all_shared_sync_tables(self):
        from core.sync_registry import SyncPolicy, sync_registry_entries

        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        shared_match = re.search(r'^SHARED_SYNC_TABLES_SQL="([^"]+)"', production_script, re.MULTILINE)
        self.assertIsNotNone(shared_match)
        shared_tables = {
            table.strip()
            for table in shared_match.group(1).split(',')
            if table.strip()
        }

        mark_function = production_script.split('mark_foreign_preseed_backlog_synced() {', 1)[1]
        mark_function = mark_function.split('\n}', 1)[0]
        preseed_tables = set(re.findall(r"'([a-z_]+)'", mark_function))
        registry_tables = {
            table_name
            for table_name, entry in sync_registry_entries().items()
            if entry.policy == SyncPolicy.SYNC
        }

        self.assertEqual(preseed_tables, shared_tables)
        self.assertEqual(shared_tables, registry_tables)

    def test_production_release_validates_runtime_identity_files(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')

        self.assertIn('DEPLOYMENT_SURFACE_GUARD="$PROJECT_DIR/scripts/check_deployment_surface_guard.py"', production_script)
        self.assertIn('validate_runtime_identity_files() {', production_script)
        self.assertIn('--runtime-env "foreign=$FOREIGN_RUNTIME_ENV_PATH"', production_script)
        self.assertIn('--runtime-env "iran=$IRAN_RUNTIME_ENV_PATH"', production_script)
        self.assertIn('guard_args+=(--allow-project-env-source)', production_script)
        self.assertIn('validate_runtime_identity_files', production_script.split('ensure_runtime_env_file() {', 1)[1])
        self.assertIn('RUNTIME_ENV_SOURCE_PATH points at a project-root env file', production_script)
        self.assertIn('--source-env-file "$RUNTIME_ENV_SOURCE_PATH"', production_script)
        self.assertIn('--local-output "$FOREIGN_RUNTIME_ENV_PATH"', production_script)
        self.assertIn('--iran-output "$IRAN_RUNTIME_ENV_PATH"', production_script)
        self.assertIn('RUNTIME_ENV_SOURCE_PATH must be different from both rendered runtime output paths', production_script)

    def test_production_release_runs_read_only_data_hygiene_guard(self):
        production_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')

        self.assertIn('PRODUCTION_DATA_HYGIENE_SCRIPT="$PROJECT_DIR/scripts/check_production_data_hygiene.py"', production_script)
        self.assertIn('run_production_data_hygiene_checks() {', production_script)
        self.assertIn('scripts/check_production_data_hygiene.py --role foreign --json --fail-on high', production_script)
        self.assertIn('scripts/check_production_data_hygiene.py --role iran --json --fail-on high', production_script)
        healthcheck_body = production_script.split('healthcheck() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('run_production_data_hygiene_checks', healthcheck_body)

    def test_nginx_setup_scripts_keep_api_proxy_off_websocket_upgrade(self):
        for script_name in ("scripts/setup_iran_nginx.sh", "scripts/setup_foreign_nginx.sh"):
            with self.subTest(script=script_name):
                source = (REPO_ROOT / script_name).read_text(encoding="utf-8")
                self.assertIn("upstream trading_bot_api", source)
                self.assertIn("keepalive 256;", source)
                self.assertIn("proxy_pass http://trading_bot_api;", source)
                self.assertIn('proxy_set_header Connection "";', source)

                api_block = source.split("location /api/ {", 1)[1].split("}", 1)[0]
                self.assertNotIn('proxy_set_header Connection "upgrade";', api_block)

    def test_makefile_parses_with_noop_status_target(self):
        if shutil.which('make') is None:
            self.skipTest('make is not installed')
        result = run_checked(['make', '-n', 'status'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_makefile_reset_sessions_uses_local_compose_fallback(self):
        makefile = (REPO_ROOT / 'Makefile').read_text(encoding='utf-8')
        self.assertIn('LOCAL_COMPOSE ?=', makefile)
        self.assertIn('@$(LOCAL_COMPOSE) exec app python scripts/dev_admin.py reset-sessions', makefile)

    def test_compose_files_render_valid_config(self):
        compose_command = resolve_docker_compose_command()
        if compose_command is None:
            self.skipTest('docker compose is not installed')
        created: list = []
        try:
            for compose_file, env_name in (
                ('docker-compose.yml', '.env'),
                ('docker-compose.iran.yml', '.env'),
                ('deploy/staging/docker-compose.staging.yml', '.env.staging'),
            ):
                placeholder = REPO_ROOT / env_name
                if not placeholder.exists():
                    placeholder.write_text('', encoding='utf-8')
                    created.append(placeholder)
                with self.subTest(compose_file=compose_file):
                    result = run_checked([*compose_command, '--env-file', '/dev/null', '-f', compose_file, 'config', '-q'])
                    self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        finally:
            for placeholder in created:
                if placeholder.exists():
                    placeholder.unlink()

    def test_all_runtime_services_have_bounded_docker_logs(self):
        compose_files = (
            'docker-compose.yml',
            'docker-compose.iran.yml',
            'deploy/staging/docker-compose.staging.yml',
        )

        for compose_file in compose_files:
            with self.subTest(compose_file=compose_file):
                payload = yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding='utf-8'))
                services = payload.get('services') or {}
                self.assertTrue(services)
                for service_name, service in services.items():
                    with self.subTest(service=service_name):
                        logging_config = service.get('logging') or {}
                        self.assertEqual(logging_config.get('driver'), 'json-file')
                        options = logging_config.get('options') or {}
                        self.assertEqual(options.get('max-size'), '${DOCKER_LOG_MAX_SIZE:-20m}')
                        self.assertEqual(options.get('max-file'), '${DOCKER_LOG_MAX_FILE:-5}')

    def test_foreign_compose_pins_current_iran_domain_inside_containers(self):
        compose = (REPO_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')

        self.assertIn(
            '${IRAN_PUBLIC_DOMAIN:-coin.gold-trade.ir}:${IRAN_PUBLIC_IP:-65.109.220.59}',
            compose,
        )
        self.assertGreaterEqual(compose.count('extra_hosts:'), 3)

    def test_staging_foreign_compose_pins_iran_domain_inside_containers(self):
        staging_compose_path = REPO_ROOT / 'deploy/staging/docker-compose.staging.yml'
        staging_payload = yaml.safe_load(staging_compose_path.read_text(encoding='utf-8'))
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        services = staging_payload['services']
        expected = '${STAGING_IRAN_PUBLIC_DOMAIN:-staging.gold-trade.ir}:${STAGING_IRAN_PUBLIC_IP:-65.109.220.59}'

        for service_name in ('foreign_app', 'foreign_sync_worker', 'bot', 'load_telegram_foreign'):
            with self.subTest(service=service_name):
                self.assertIn(expected, services[service_name].get('extra_hosts') or [])
        for service_name in ('app', 'sync_worker', 'migration'):
            with self.subTest(service=service_name):
                self.assertNotIn(expected, services[service_name].get('extra_hosts') or [])
        self.assertIn('STAGING_IRAN_PUBLIC_DOMAIN="${STAGING_IRAN_PUBLIC_DOMAIN:-staging.gold-trade.ir}"', staging_script)
        self.assertIn('STAGING_IRAN_PUBLIC_IP="${STAGING_IRAN_PUBLIC_IP:-65.109.220.59}"', staging_script)
        self.assertIn('export STAGING_IRAN_PUBLIC_DOMAIN STAGING_IRAN_PUBLIC_IP', staging_script)

    def test_staging_iran_load_runner_pins_foreign_domain_for_cold_start_trade_forwarding(self):
        staging_compose_path = REPO_ROOT / 'deploy/staging/docker-compose.staging.yml'
        staging_payload = yaml.safe_load(staging_compose_path.read_text(encoding='utf-8'))
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        webapp_runner = staging_payload['services']['load_webapp_iran']

        self.assertIn(
            '${STAGING_FOREIGN_PUBLIC_DOMAIN:-staging.362514.ir}:${STAGING_FOREIGN_PUBLIC_IP:-65.109.216.187}',
            webapp_runner.get('extra_hosts') or [],
        )
        self.assertIn('STAGING_FOREIGN_PUBLIC_DOMAIN="${STAGING_FOREIGN_PUBLIC_DOMAIN:-staging.362514.ir}"', staging_script)
        self.assertIn('STAGING_FOREIGN_PUBLIC_IP="${STAGING_FOREIGN_PUBLIC_IP:-65.109.216.187}"', staging_script)
        self.assertIn('export STAGING_FOREIGN_PUBLIC_DOMAIN STAGING_FOREIGN_PUBLIC_IP', staging_script)

    def test_production_hosts_sync_restores_standard_permissions(self):
        release_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')

        self.assertIn('chown root:root "$hosts_file"', release_script)
        self.assertIn('chmod 0644 "$hosts_file"', release_script)
        self.assertIn('chown root:root \\"\\$hosts_file\\"', release_script)
        self.assertIn('chmod 0644 \\"\\$hosts_file\\"', release_script)

    def test_production_iran_image_context_includes_verified_frontend_dist(self):
        release_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')

        copy_command = 'rsync -a --delete "$LOCAL_DIST_DIR/" "$iran_context_dir/mini_app_dist/"'
        self.assertIn(copy_command, release_script)
        self.assertLess(
            release_script.index('verify_frontend_release_contracts "$LOCAL_DIST_DIR"'),
            release_script.index(copy_command),
        )

    def test_production_image_context_excludes_local_tooling_and_staging_artifacts(self):
        release_script = (REPO_ROOT / 'scripts/production_deploy_online.sh').read_text(encoding='utf-8')
        dockerignore = (REPO_ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()

        for path in (
            '.agents', '.claude', '.codex', '.cursor', '.githooks', '.lego',
            '.local', '.pytest_cache', 'mini_app_dist_staging', 'mutants',
        ):
            with self.subTest(path=path):
                self.assertIn(f"--exclude '{path}'", release_script)
        for pattern in (
            '.agents/', '.claude/', '.codex/', '.cursor/', '.githooks/',
            '.lego/', '.local/', '.pytest_cache/', 'mini_app_dist_staging/',
            '/mutants/',
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, dockerignore)

    def test_dockerfiles_pass_docker_build_check(self):
        if shutil.which('docker') is None:
            self.skipTest('docker is not installed')

        build_check_command: list[str] | None = None
        docker_help = run_checked(['docker', 'build', '--help'])
        if docker_help.returncode == 0 and '--check' in docker_help.stdout:
            build_check_command = ['docker', 'build', '--check']
        else:
            buildx_help = run_checked(['docker', 'buildx', 'build', '--help'])
            if buildx_help.returncode == 0 and '--check' in buildx_help.stdout:
                build_check_command = ['docker', 'buildx', 'build', '--check']

        if build_check_command is None:
            self.skipTest('docker check mode is unavailable')

        for dockerfile in ('Dockerfile', 'Dockerfile.iran'):
            with self.subTest(dockerfile=dockerfile):
                result = run_checked([*build_check_command, '-f', dockerfile, '.'])
                self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_nginx_config_passes_syntax_validation(self):
        if shutil.which('nginx') is None:
            self.skipTest('nginx is not installed')
        raw_config = (REPO_ROOT / 'nginx.conf').read_text(encoding='utf-8')
        sanitized_config = raw_config
        replacements = {
            '    listen 80;': '    listen 8080;',
            '    listen 443 ssl;': '    listen 8443;',
            '    ssl_certificate /etc/letsencrypt/live/coin.362514.ir/fullchain.pem;': '',
            '    ssl_certificate_key /etc/letsencrypt/live/coin.362514.ir/privkey.pem;': '',
            '    include /etc/letsencrypt/options-ssl-nginx.conf;': '',
            '    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;': '',
            '    access_log /var/log/nginx/trading_bot_access.log;': '',
            '    error_log /var/log/nginx/trading_bot_error.log warn;': '',
        }
        for old, new in replacements.items():
            sanitized_config = sanitized_config.replace(old, new)

        server_name_line = '    server_name coin.362514.ir mini-app.362514.ir;'
        sanitized_config = sanitized_config.replace(server_name_line, '    server_name coverage-primary.test;', 1)
        sanitized_config = sanitized_config.replace(server_name_line, '    server_name coverage-secondary.test;', 1)

        wrapper_config = f'events {{}}\nhttp {{\n    access_log off;\n    error_log stderr notice;\n{sanitized_config}\n}}\n'

        with tempfile.TemporaryDirectory() as temp_dir:
            wrapped_config_path = Path(temp_dir) / 'nginx-wrapper.conf'
            wrapped_config_path.write_text(wrapper_config, encoding='utf-8')
            pid_path = Path(temp_dir) / 'nginx.pid'

            result = run_checked([
                'nginx',
                '-t',
                '-c',
                str(wrapped_config_path),
                '-p',
                temp_dir,
                '-g',
                f'pid {pid_path};',
            ])

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
