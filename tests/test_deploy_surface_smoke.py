import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class DeploySurfaceSmokeTests(unittest.TestCase):
    def test_deploy_script_has_valid_bash_syntax(self):
        result = run_checked(['bash', '-n', 'deploy.sh'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_makefile_parses_with_noop_status_target(self):
        result = run_checked(['make', '-n', 'status'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_compose_files_render_valid_config(self):
        for compose_file in ('docker-compose.yml', 'docker-compose.iran.yml'):
            with self.subTest(compose_file=compose_file):
                result = run_checked(['docker', 'compose', '-f', compose_file, 'config', '-q'])
                self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_dockerfiles_pass_docker_build_check(self):
        for dockerfile in ('Dockerfile', 'Dockerfile.iran'):
            with self.subTest(dockerfile=dockerfile):
                result = run_checked(['docker', 'build', '--check', '-f', dockerfile, '.'])
                self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_nginx_config_passes_syntax_validation(self):
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