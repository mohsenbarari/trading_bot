"""Real owned public fixture admission/path flow, not an 81-test execution.

Compile the exact runner AST from profile selection through fixture root
selection, then its exact replay-ID expression. The full execute() identity
gate is outside this deliberately narrow SOURCE test; no identity, flags,
runtime version, architecture or production constant is patched or bypassed
to claim an image/test result. No business fixture module is imported.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace
import ast
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
helper_path = ROOT / 'tests/test_market_role_normal_builder.py'
helper = ModuleType('owned_fixture_role_helper')
helper.__file__ = str(helper_path)
exec(compile(helper_path.read_bytes(), str(helper_path), 'exec'), helper.__dict__)
module = helper.module


class FixturePathFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helper.RoleBuilderTests.setUpClass()
        cls.prepared = helper.RoleBuilderTests()
        cls.runner_path = ROOT / 'deploy/market-data/business81/run_business81.py'
        cls.runner = module('deploy/market-data/business81/run_business81.py')
        source = ast.parse(cls.runner_path.read_bytes())
        execute = next(node for node in source.body
                       if isinstance(node, ast.FunctionDef) and node.name == 'execute')
        attempt = next(node for node in execute.body if isinstance(node, ast.Try))
        root_index = next(i for i, node in enumerate(attempt.body)
                          if isinstance(node, ast.Assign) and len(node.targets) == 1
                          and isinstance(node.targets[0], ast.Name)
                          and node.targets[0].id == 'root')
        # These are unchanged production statements, including genuine Held
        # reads, every public byte/origin/inventory check and source selection.
        cls.path_flow = compile(ast.fix_missing_locations(ast.Module(
            body=deepcopy([execute.body[0], *attempt.body[:root_index + 1]]),
            type_ignores=[])), str(cls.runner_path), 'exec')
        tests_block = next(node for node in ast.walk(execute)
                           if isinstance(node, ast.With)
                           and any(isinstance(item.context_expr, ast.Call)
                                   and isinstance(item.context_expr.func, ast.Name)
                                   and item.context_expr.func.id == 'ExitStack'
                                   for item in node.items))
        id_index = next(i for i, node in enumerate(tests_block.body)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'original'
                                for target in node.targets))
        expected_index = next(i for i, node in enumerate(tests_block.body)
                              if i > id_index and isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == 'expected'
                                      for target in node.targets))
        cls.id_flow = compile(ast.fix_missing_locations(ast.Module(
            body=deepcopy(tests_block.body[id_index:expected_index + 1]),
            type_ignores=[])), str(cls.runner_path), 'exec')
        update = next(node for node in ast.walk(execute)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == 'update'
                      and any(keyword.arg == 'fixture_business_provenance'
                              for keyword in node.keywords))
        expression = next(keyword.value for keyword in update.keywords
                          if keyword.arg == 'fixture_business_provenance')
        cls.result_provenance = compile(ast.Expression(expression), str(cls.runner_path), 'eval')

    def test_genuine_public_fixture_path_and_replay_flow_for_every_profile_and_role(self):
        # Release/image fields are existing labelled bundle-codec DTOs only;
        # real public fixture bytes/row hashes/source origins are validated.
        choices = (None, *self.prepared.M.ROLE_MAP)
        observed = set()
        for role in choices:
            _, _, contents, manifest, plans = self.prepared.historical(role)
            with tempfile.TemporaryDirectory(prefix='.owned-fixture-flow-',
                                             dir=ROOT / 'tests') as temporary:
                evidence = Path(temporary)
                evidence.chmod(0o755)
                for name, raw in contents.items():
                    path = evidence / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    for parent in path.parents:
                        if parent == evidence:
                            break
                        parent.chmod(0o755)
                    path.write_bytes(raw)
                    path.chmod(0o644)
                for profile, (fixture_name, uid, count) in self.runner.PROFILES.items():
                    with self.subTest(role=role, profile=profile):
                        held = self.runner.Held()
                        namespace = dict(self.runner.__dict__)
                        namespace.update(args=SimpleNamespace(
                            profile=profile, image_id=manifest['image_id'],
                            manifest_sha256=sha256(contents['suite-manifest.json']).hexdigest()),
                            evidence=evidence, held=held)
                        try:
                            exec(self.path_flow, namespace)
                            self.assertEqual(namespace['fixture'], fixture_name)
                            self.assertIs(type(namespace['fixture']), str)
                            self.assertEqual(namespace['root'], evidence / fixture_name)
                            self.assertTrue(namespace['root'].is_dir())
                            expected = {name.split('/', 1)[1]: raw for name, raw in contents.items()
                                        if name.startswith(fixture_name + '/') and name.endswith('.py')}
                            self.assertEqual(namespace['sources'], expected)
                            for name, raw in namespace['sources'].items():
                                self.assertEqual((namespace['root'] / name).read_bytes(), raw)
                            provenance = self.prepared.B.fixture_business_provenance()
                            self.assertEqual(namespace['fixture_provenance'], provenance)
                            self.assertIs(type(eval(self.result_provenance, namespace)), dict)
                            self.assertEqual(eval(self.result_provenance, namespace), provenance)
                            selection = namespace['selection']
                            # Independent authoritative selection from the
                            # prepared profile plan, not the runner variable.
                            namespace['all_ids'] = sorted(selection['historical_test_ids'] +
                                (selection['capture_added_replay_test_ids']
                                 if profile == 'added-replay' else []))
                            exec(self.id_flow, namespace)
                            self.assertEqual(sorted(namespace['expected']), sorted(plans[profile]['test_ids']))
                            self.assertEqual(len(namespace['expected']), count)
                            self.assertEqual(namespace['uid'], uid)
                            held.check()
                        finally:
                            held.close()
                        observed.add((role, profile))
        self.assertEqual(len(observed), 30)

    def test_runner_helper_pin_and_outer_normal_controller_pin_are_exact_bytes(self):
        B = module('scripts/market_business81.py')
        O = module('scripts/market_business81_outer.py')
        runner = self.runner_path.read_bytes()
        controller = (ROOT / 'scripts/prepare_market_capture2_image.py').read_bytes()
        self.assertEqual(B.HELPERS['run_business81.py'], (len(runner), sha256(runner).hexdigest()))
        self.assertEqual(O.NORMAL_PIN, (len(controller), sha256(controller).hexdigest()))


if __name__ == '__main__':
    unittest.main()
