"""Joined SOURCE and explicitly inert DTOs; no Git/image/provider operations."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / name
    value = ModuleType('owned_' + path.stem)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), value.__dict__)
    return value


class CoherentSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = load('scripts/prepare_market_product_source.py')
        cls.M = load('scripts/prepare_market_capture2_image.py')
        cls.H = load('scripts/prepare_coreutils_acl_os_cache.py')
        cls.F = load('tests/market_security_source_fixtures.py')

    def test_complete_release_and_role_inputs_are_owned_repository_paths(self):
        # Cohort lineage belongs to SOURCE QA outside this tracked test tree.
        # Functional input closure must work in a checkout without its parents.
        names = set(self.S.RELEASE_FILES) | set(self.M.CONTROLLER_FILES)
        for role in self.M.ROLE_MAP:
            names.update(self.S.release_files(role))
            names.update(self.M.controller_files(role))
        for name in names:
            path = ROOT / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(path.resolve().relative_to(ROOT), Path(name))
            self.assertNotIn('..', Path(name).parts)
            self.assertNotIn('.local', Path(name).parts)

    def test_owned_unarmed_coreutils_policy_fixture_is_rejected(self):
        value = self.F.unarmed_lock((ROOT / 'scripts/coreutils_acl_package.lock.json').read_bytes())
        self.assertIs(value['armed'], False)
        for key in ('artifact', 'outputs', 'source', 'runtime_payload', 'installed_callers'):
            self.assertIsNone(value[key])
        with self.assertRaisesRegex(RuntimeError, 'lock_unarmed'):
            self.H.validate_lock(value)

    def test_default_and_all_role_release_cache_and_controller_counts_are_coherent(self):
        self.assertEqual(len(self.S.RELEASE_FILES), 20)
        self.assertEqual(len(self.M.CONTROLLER_FILES), 30)
        self.assertEqual(len(self.M.CACHE_ROOTS), 4)
        self.assertTrue(self.S.copy_contract((ROOT / 'deploy/market-data/Dockerfile.capture2').read_bytes()))
        for role in self.M.ROLE_MAP:
            self.assertEqual(len(self.S.release_files(role)), 21)
            self.assertEqual(len(self.M.controller_files(role)), 33)

    def test_all_three_exact_role_parcel_sizes_have_one_extra_index(self):
        helper = load('tests/test_market_role_normal_builder.py')
        helper.RoleBuilderTests.setUpClass()
        fixture = helper.RoleBuilderTests()
        # These are only codec inputs. Actual production cache preparation
        # is independently gated by its authentic current lock; no artifact is
        # invented or accepted by this codec-only test.
        cache = {'pip_packages/OWNED-DTO-ONLY/cache-' + str(i):
                 {'bytes': 1, 'sha256': sha256(b'X').hexdigest()} for i in range(26)}
        sizes = set()
        for role, spec in self.M.ROLE_MAP.items():
            receipt, signature = fixture.receipt(role)
            controllers = [fixture.row(name) for name in sorted(self.M.controller_files(role))]
            self.M.receipt_contract(receipt, signature, controllers, cache, 1793085646)
            values = self.M.controller_parcel_inputs(Path('/OWNED-NOT-OPENED'),
                                                      receipt, controllers, cache)
            self.assertEqual(len(values), {93: 173, 94: 174, 95: 175}[spec['count']])
            sizes.add((len(values), len(values) + 1))
        self.assertEqual(sizes, {(173, 174), (174, 175), (175, 176)})


if __name__ == '__main__':
    unittest.main()
