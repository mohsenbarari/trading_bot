import os
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

    def test_makefile_parses_with_noop_status_target(self):
        if shutil.which('make') is None:
            self.skipTest('make is not installed')
        result = run_checked(['make', '-n', 'status'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_compose_files_render_valid_config(self):
        compose_command = resolve_docker_compose_command()
        if compose_command is None:
            self.skipTest('docker compose is not installed')
        for compose_file in ('docker-compose.yml', 'docker-compose.iran.yml'):
            with self.subTest(compose_file=compose_file):
                result = run_checked([*compose_command, '--env-file', '/dev/null', '-f', compose_file, 'config', '-q'])
                self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

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
            '    listen 443 ssl;': '    listen 443;',
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