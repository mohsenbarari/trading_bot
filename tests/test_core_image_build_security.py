"""Hermetic recipe contracts; no Docker, builds, network or app imports."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASE = 'python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf'


def stages(source):
    result = {}
    current = None
    for line in source.replace('\\\n', ' ').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line.startswith('FROM '):
            image, keyword, name = line[5:].split()
            assert keyword == 'AS'
            if name in result:
                raise AssertionError('duplicate stage')
            result[name] = {'parent': image, 'instructions': []}
            current = result[name]
        else:
            current['instructions'].append(line)
    return result


def assert_runtime_isolation(source):
    recipe = stages(source)
    assert list(recipe) == ['patched-base', 'wheel-builder', 'runtime']
    assert recipe['patched-base']['parent'] == BASE
    assert recipe['wheel-builder']['parent'] == 'patched-base'
    assert recipe['runtime']['parent'] == 'patched-base'
    base = '\n'.join(recipe['patched-base']['instructions'])
    runtime = '\n'.join(recipe['runtime']['instructions'])
    install = re.search(r'apt-get[^\n]+ install -y --no-install-recommends (.+?)\s*&&', base).group(1)
    assert set(install.split()) == {'ca-certificates', 'libpq5', 'libmagic1', 'libstdc++6'}
    assert not re.search(r'\b(build-essential|libpq-dev|gcc|g\+\+|make|binutils|perl|libc6-dev)\b', base + '\n' + runtime)
    copies = [line for line in recipe['runtime']['instructions'] if line.startswith('COPY --from=')]
    assert copies == ['COPY --from=wheel-builder /wheelhouse/ /tmp/wheelhouse/']
    return recipe


class CoreImageBuildSecurityTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'Dockerfile').read_text()

    def test_runtime_cannot_inherit_build_tools_or_copy_the_builder_root(self):
        assert_runtime_isolation(self.source)
        for old, new in (
            ('FROM patched-base AS runtime', 'FROM wheel-builder AS runtime'),
            ('COPY --from=wheel-builder /wheelhouse/ /tmp/wheelhouse/', 'COPY --from=wheel-builder /usr/ /usr/'),
            ('ca-certificates libpq5 libmagic1 libstdc++6', 'ca-certificates libpq5 libmagic1 libstdc++6 build-essential'),
            ('ca-certificates libpq5 libmagic1 libstdc++6', 'ca-certificates libpq5 libmagic1'),
        ):
            with self.subTest(mutation=new), self.assertRaises(AssertionError):
                assert_runtime_isolation(self.source.replace(old, new))

    def test_builder_retains_native_build_capability_and_existing_cache_policy(self):
        recipe = assert_runtime_isolation(self.source)
        builder = '\n'.join(recipe['wheel-builder']['instructions'])
        self.assertIn('install -y --no-install-recommends "libpq-dev=$(dpkg-query -W -f=\'${Version}\' libpq5)" build-essential', builder)
        self.assertIn("find /tmp/pip_packages -maxdepth 1 -type f -name '*.whl'", builder)
        self.assertIn('prepare_psycopg2_source.py check', builder)
        self.assertIn('prepare_psycopg2_source.py prepare', builder)
        self.assertIn('pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation', builder)
        self.assertIn('--wheel-dir=/wheelhouse /tmp/pip_packages/psycopg2_binary-2.9.13.tar.gz', builder)
        self.assertIn('pip wheel --no-cache-dir --no-index --find-links=/tmp/pip_packages/ --find-links=/wheelhouse', builder)
        self.assertIn('--wheel-dir=/wheelhouse -r /tmp/pip_packages/.requirements-without-psycopg2.txt', builder)
        self.assertIn('else', builder)
        self.assertIn('pip wheel --no-cache-dir --wheel-dir=/wheelhouse -r /tmp/pip_packages/.requirements-without-psycopg2.txt', builder)

    def test_serving_stage_installs_only_built_wheels_offline_and_checks_consistency(self):
        recipe = assert_runtime_isolation(self.source)
        runtime = '\n'.join(recipe['runtime']['instructions'])
        self.assertIn('pip install --no-cache-dir --no-index --only-binary=:all: --find-links=/tmp/wheelhouse/ -r requirements.txt', runtime)
        self.assertIn('python -m pip check', runtime)
        self.assertIn('rm -rf /tmp/wheelhouse/', runtime)
        self.assertNotIn('pip wheel', runtime)
        self.assertNotIn('apt-get', runtime)

    def test_patched_os_trust_and_pip_tool_security_floors_reach_final_image(self):
        recipe = assert_runtime_isolation(self.source)
        base = '\n'.join(recipe['patched-base']['instructions'])
        for expected in ('https://security.debian.org/debian-security',
                         'Acquire::https::No-Cache=true update', 'upgrade -y --no-install-recommends',
                         "'pip>=26.2.2'", "'setuptools>=78.1.1'", "'wheel>=0.46.2'", 'WORKDIR /app'):
            self.assertIn(expected, base)
        self.assertIn('RUN python -I -B scripts/verify_image_trust.py --require-certifi', recipe['runtime']['instructions'])

    def test_same_application_payload_and_frontend_argument_remain_in_runtime(self):
        runtime = assert_runtime_isolation(self.source)['runtime']['instructions']
        self.assertIn('ARG FRONTEND_DIST_DIR=mini_app_dist', runtime)
        for name in ('api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'scripts'):
            self.assertIn(f'COPY {name}/ ./{name}/', runtime)
        for name in ('alembic.ini', 'main.py', 'manage.py', 'run_bot.py', 'schemas.py',
                     'seed_fake_data.py', 'trading_settings.json', 'requirements.txt'):
            self.assertIn(f'COPY {name} .', runtime)
        self.assertIn('COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/', runtime)
        self.assertFalse(any(line.startswith(('USER ', 'ENTRYPOINT ', 'CMD ')) for line in runtime))


if __name__ == '__main__':
    unittest.main()
