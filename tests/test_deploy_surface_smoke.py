import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_staging_frontend_dist_isolated_from_production_artifact(self):
        staging_script = (REPO_ROOT / 'scripts/deploy_staging.sh').read_text(encoding='utf-8')
        vite_config = (REPO_ROOT / 'frontend/vite.config.ts').read_text(encoding='utf-8')
        staging_nginx = (REPO_ROOT / 'deploy/staging/nginx-staging.conf.template').read_text(encoding='utf-8')
        staging_compose = (REPO_ROOT / 'deploy/staging/docker-compose.staging.yml').read_text(encoding='utf-8')
        dockerfile = (REPO_ROOT / 'Dockerfile').read_text(encoding='utf-8')
        gitignore = (REPO_ROOT / '.gitignore').read_text(encoding='utf-8')

        self.assertIn('process.env.FRONTEND_BUILD_OUT_DIR', vite_config)
        self.assertIn('FRONTEND_BUILD_OUT_DIR="$STAGING_FRONTEND_DIST_DIR"', staging_script)
        self.assertIn('STAGING_SKIP_FRONTEND_BUILD', staging_script)
        self.assertIn('STAGING_FRONTEND_DIST_DIR="${STAGING_FRONTEND_DIST_DIR:-mini_app_dist_staging}"', staging_script)
        self.assertIn('STAGING_INTERNAL_FOREIGN_SERVER_URL', staging_script)
        self.assertIn('STAGING_PUBLIC_FOREIGN_SYNC_URL="${STAGING_PUBLIC_FOREIGN_SYNC_URL:-https://staging.362514.ir/foreign-sync}"', staging_script)
        self.assertIn('STAGING_FOREIGN_IRAN_SERVER_URL="${STAGING_FOREIGN_IRAN_SERVER_URL:-https://staging.gold-trade.ir}"', staging_script)
        self.assertIn('STAGING_FOREIGN_FRONTEND_URL="${STAGING_FOREIGN_FRONTEND_URL:-$STAGING_FOREIGN_IRAN_SERVER_URL}"', staging_script)
        self.assertIn('STAGING_FOREIGN_PUBLIC_SURFACE_GUARD="${STAGING_FOREIGN_PUBLIC_SURFACE_GUARD:-$STAGING_ENABLE_BOT}"', staging_script)
        self.assertIn('STAGING_FOREIGN_ONLY="${STAGING_FOREIGN_ONLY:-0}"', staging_script)
        self.assertIn('STAGING_RELEASE_SHA_OVERRIDE', staging_script)
        self.assertIn('command -v docker-compose', staging_script)
        self.assertIn('STAGING_OBJECT_STORAGE_PREFIX="${STAGING_OBJECT_STORAGE_PREFIX:-${ARVAN_OBJECT_STORAGE_PREFIX:-staging/deploy-bridge}}"', staging_script)
        self.assertIn('configure_staging_object_storage_env', staging_script)
        self.assertIn('stage_object_storage_artifact', staging_script)
        self.assertIn('stage_object_storage_release', staging_script)
        self.assertIn('stage_object_storage_release_fetch', staging_script)
        self.assertIn('stage_object_storage_release_apply', staging_script)
        self.assertIn('scripts/staging_object_storage_artifact.py', staging_script)
        self.assertIn('scripts/arvan_object_storage_probe.py', staging_script)
        self.assertIn('scripts/staging_object_storage_release.py', staging_script)
        self.assertIn('object-release-package', staging_script)
        self.assertIn('object-release-upload', staging_script)
        self.assertIn('object-release-fetch', staging_script)
        self.assertIn('object-release-fetch-latest', staging_script)
        self.assertIn('object-release-apply', staging_script)
        self.assertIn('object-release-apply-latest', staging_script)
        self.assertIn('STAGING_OBJECT_RELEASE_CHANNEL="${STAGING_OBJECT_RELEASE_CHANNEL:-iran-staging}"', staging_script)
        self.assertIn('--publish-channel "$STAGING_OBJECT_RELEASE_CHANNEL"', staging_script)
        self.assertIn('compose build app >&2', staging_script)
        self.assertIn('-print -quit)', staging_script)
        self.assertIn('--exclude=\'./.env.*\'', staging_script)
        self.assertIn('--exclude=\'./tmp\'', staging_script)
        self.assertIn("'.github'", staging_script)
        self.assertIn("'tests'", staging_script)
        self.assertIn("'pip_packages'", staging_script)
        self.assertIn("'postgres_data'", staging_script)
        self.assertIn("'redis_data'", staging_script)
        self.assertIn('"${STAGING_OBJECT_RELEASE_PROJECT_EXCLUDES[@]}" "${STAGING_OBJECT_RELEASE_RECEIVER_PROTECTED[@]}"', staging_script)
        self.assertIn('STAGING_OBJECT_RELEASE_APPLY_EXECUTE', staging_script)
        self.assertIn('STAGING_FOREIGN_APP_PORT="${STAGING_FOREIGN_APP_PORT:-8121}"', staging_script)
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
        self.assertIn('compose --profile staging-bot up -d --build foreign_sync_worker', staging_script)
        self.assertIn('compose up -d --build sync_worker', staging_script)
        self.assertIn('set_env_value GERMANY_SERVER_URL "$STAGING_INTERNAL_FOREIGN_SERVER_URL"', staging_script)
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

    def test_staging_object_storage_bridge_is_documented_as_staging_only(self):
        staging_readme = (REPO_ROOT / 'deploy/staging/README.md').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')
        production_doc = (REPO_ROOT / 'docs/PRODUCTION_DEPLOYMENT_ONLINE.md').read_text(encoding='utf-8')

        self.assertIn('Object Storage deploy bridge', staging_readme)
        self.assertIn('deployment\nartifacts only', staging_readme)
        self.assertIn('does not replace cross-server sync', staging_readme)
        self.assertIn('ARVAN_OBJECT_STORAGE_ENDPOINT=https://s3.ir-thr-at1.arvanstorage.ir', staging_example)
        self.assertIn('ARVAN_OBJECT_STORAGE_PREFIX=staging/deploy-bridge', staging_example)
        self.assertIn('S3 support has been removed from the production deploy flow', production_doc)

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

    def test_production_preseed_backlog_covers_all_shared_sync_tables(self):
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

        self.assertEqual(preseed_tables, shared_tables)

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

    def test_foreign_compose_pins_iran_domain_inside_containers(self):
        compose = (REPO_ROOT / 'docker-compose.yml').read_text(encoding='utf-8')

        self.assertIn(
            '${IRAN_PUBLIC_DOMAIN:-coin.gold-trade.ir}:${IRAN_PUBLIC_IP:-62.220.124.174}',
            compose,
        )
        self.assertGreaterEqual(compose.count('extra_hosts:'), 3)

    def test_staging_foreign_compose_pins_iran_domain_inside_containers(self):
        staging_compose = (REPO_ROOT / 'deploy/staging/docker-compose.staging.yml').read_text(encoding='utf-8')
        staging_example = (REPO_ROOT / 'deploy/staging/env.staging.example').read_text(encoding='utf-8')

        self.assertIn(
            '${STAGING_IRAN_PUBLIC_DOMAIN:-staging.gold-trade.ir}:${STAGING_IRAN_PUBLIC_IP:-62.220.124.174}',
            staging_compose,
        )
        self.assertGreaterEqual(staging_compose.count('extra_hosts:'), 4)
        self.assertIn('STAGING_IRAN_PUBLIC_DOMAIN=staging.gold-trade.ir', staging_example)
        self.assertIn('STAGING_IRAN_PUBLIC_IP=62.220.124.174', staging_example)

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
            '    error_log /var/log/nginx/trading_bot_error.log debug;': '',
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
