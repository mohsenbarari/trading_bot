import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


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

        wrapper_config = f'events {{}}\nhttp {{\n{sanitized_config}\n}}\n'

        with tempfile.TemporaryDirectory() as temp_dir:
            wrapped_config_path = Path(temp_dir) / 'nginx-wrapper.conf'
            wrapped_config_path.write_text(wrapper_config, encoding='utf-8')

            result = run_checked([
                'nginx',
                '-t',
                '-c',
                str(wrapped_config_path),
                '-p',
                temp_dir,
            ])

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()