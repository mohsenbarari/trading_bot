import os
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
            'for service in migration app foreign_app bot sync_worker foreign_sync_worker',
            function_body,
        )
        self.assertNotIn(' service in db ', function_body)
        self.assertNotIn(' service in redis ', function_body)
        self.assertIn('remove_legacy_compose_stateless_containers\n', staging_script)

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

    def test_application_image_includes_staging_witness_schema_files(self):
        dockerfile = (REPO_ROOT / 'Dockerfile').read_text(encoding='utf-8')

        self.assertIn(
            'COPY deploy/writer-witness/001_initial.sql '
            './deploy/writer-witness/001_initial.sql',
            dockerfile,
        )
        self.assertIn(
            'COPY deploy/writer-witness/002_failover_operation_ledger.sql '
            './deploy/writer-witness/002_failover_operation_ledger.sql',
            dockerfile,
        )

    def test_staging_env_sets_trusted_proxy_cidrs_for_nginx_container_hop(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')
        three_site_compose = (REPO_ROOT / 'deploy/staging/docker-compose.three-site.yml').read_text(encoding='utf-8')
        three_site_example = (REPO_ROOT / 'deploy/staging/env.three-site.staging.example').read_text(encoding='utf-8')

        self.assertIn('STAGING_TRUSTED_PROXY_CIDRS="${STAGING_TRUSTED_PROXY_CIDRS:-127.0.0.1/32,::1/128,172.16.0.0/12}"', staging_script)
        self.assertIn('TRUSTED_PROXY_CIDRS=$STAGING_TRUSTED_PROXY_CIDRS', staging_script)
        self.assertIn('set_env_value TRUSTED_PROXY_CIDRS "$STAGING_TRUSTED_PROXY_CIDRS"', staging_script)
        self.assertIn('TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.16.0.0/12', staging_example)
        self.assertIn('TRUSTED_PROXY_CIDRS: ${STAGING_TRUSTED_PROXY_CIDRS:-127.0.0.1/32,::1/128,172.16.0.0/12}', three_site_compose)
        self.assertIn('STAGING_TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.16.0.0/12', three_site_example)

    def test_staging_nginx_supports_scoped_origin_proxy_cidrs_and_private_site_config(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        staging_nginx = (REPO_ROOT / 'deploy/staging/nginx-staging.conf.template').read_text(encoding='utf-8')

        self.assertIn('STAGING_TRUSTED_ORIGIN_PROXY_CIDRS="${STAGING_TRUSTED_ORIGIN_PROXY_CIDRS:-}"', staging_script)
        self.assertIn('set_real_ip_from {network};', staging_script)
        self.assertIn('real_ip_header X-Forwarded-For;', staging_script)
        self.assertIn('-v trusted_origin_proxy_directives="$trusted_origin_proxy_directives"', staging_script)
        self.assertIn('__TRUSTED_ORIGIN_PROXY_DIRECTIVES__', staging_nginx)
        self.assertIn('install -o root -g root -m 0600 "$tmp" "$available"', staging_script)

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
            'python3 -m pip download -r "$PROJECT_DIR/requirements.txt"',
        ):
            self.assertIn(expected, deploy_script)

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

    def test_replacement_wa_ir_is_blocked_from_legacy_rsync_scp_flow(self):
        production_script = (
            REPO_ROOT / 'scripts/production_deploy_online.sh'
        ).read_text(encoding='utf-8')

        self.assertIn('WA_IR_OBJECT_STORAGE_ONLY_HOST="95.38.164.29"', production_script)
        self.assertIn('Object-Storage-only for every file/data transfer', production_script)
        self.assertIn('publish_wa_ir_object_storage_preflight.py', production_script)

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
        self.assertIn('--runtime-env "foreign=$LOCAL_ENV_SOURCE_PATH"', production_script)
        self.assertIn('--runtime-env "iran=$IRAN_ENV_SOURCE_PATH"', production_script)
        self.assertIn('guard_args+=(--allow-project-env-source)', production_script)
        self.assertIn('validate_runtime_identity_files', production_script.split('ensure_runtime_env_file() {', 1)[1])
        self.assertIn('IRAN_ENV_SOURCE_PATH points at a project-root env file', production_script)

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
        for compose_file in ('docker-compose.yml', 'docker-compose.iran.yml'):
            with self.subTest(compose_file=compose_file):
                result = run_checked([*compose_command, '--env-file', '/dev/null', '-f', compose_file, 'config', '-q'])
                self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

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
