CASES = (
    "unchanged403-CODEX", "changed404-CODEX", "alias403-CODEX", "batch403404-CODEX",
    "unchanged403-GEMINI-0", "unchanged403-GEMINI-1", "unchanged403-GEMINI-2",
    "changed404-GEMINI-0", "changed404-GEMINI-1", "changed404-GEMINI-2",
    "alias403-GEMINI-0", "alias403-GEMINI-1", "alias403-GEMINI-2",
    "batch403404-GEMINI-0", "batch403404-GEMINI-1", "batch403404-GEMINI-2",
)

BEFORE = "44edbbfc10e4854afdae3d2f0e63e517396d0838"

AFTER = ("c80b7751b4c73cdb9977f7a13ba8bb0f6656f699", "dbf932b7fc4fe26c1a01cc4a2ad63d6485f4279e",
         "2e9ca1f665300d21de5e05d19a767c5dd50fb724", "c80b7751b4c73cdb9977f7a13ba8bb0f6656f699")

TREES = ("55bc00dc6a18bf4c0c3bb892e09f2511be9c4b95", "dc20c1519f8606f89b700a6d2b325d267efc1c20",
         "3a44a4cb97dd2cff7646972e95403f1a220b83de", "55bc00dc6a18bf4c0c3bb892e09f2511be9c4b95")

PINS = (
 ("f2dc2d89af61b883d521c453f18e6b669d110f21e2efe05b83a2f8424c3d88bb", "f2844ef5d03ba70a0e5a1e4684cf1cb7673832835636876373ac6f2506d44654", "5a263d204bd1b0e70b9c8d5155061b89b5ec22b6d470124dee79bb4aa7937cb5", "ff96cfedf692ec737048ce9604d2969fce812922f1874988ced08411c21c39e9", "e3b5bf6e71cd331e3e6de6a11e87a79448a373e251c1b32cfb00f8771c9c108d"),
 ("06ea0289837a29cb8586bfc3e1b8e3c04b67262d1a2e26c9385da1f77cf91501", "00ac1dc0a672ef1ecd245b461f7fd5787aa0616eeddb3380d905a54f3c478ecb", "ab8697f430799b61001e3b3ac304c934e30f423ad4db9b4ae29426e945a6eceb", "8afefa84c22a3498f2ad2da3f4b5d1af1281cd6dfae61650d792d1e28c612645", "c48146e160a3016090f23d2fbe5c128e1878cd1fa437e0177c327d0de8812430"),
 ("01d0f48387c2a171e77b09f4c3d9f89afcd8b639e78cc5fafca90dc00f7a3ae1", "622b9ccc50385a5e28f1eea32d6b2a20d46b02f2c669e16c56a6c11fdeaf7eb8", "77804a4f2e9468c3e0449515b1045359f4d5d41f240643e80a09d9151e14ba5c", "70b841f89c8faa187a357c76d8f162f4f3ae02b1c7786331f59101d116117975", "58caebbba4f7ade43eb19b1ae6528692d66e4f7d47a0e6b5e870510947b13a58"),
 ("98b0cdfc89153dca21fba2c2c89debff01609c8d0c17e7d3f6aefceba2a14c28", "2a622dfffc726524d0ad94e556ac69a10ca9edad3f03296622d963091859b091", "e179d66761a77cc641f714a0ee568efc8bc05bc8b31e4504775f4e555bf80377", "3896b7865cda2044051af558ae4d5548e8c977dbe2b3c72becab39bc554c41f8", "b2e073242ab42dd3810ebe606b23a784cb33f7217542f11407bf6962f7ee5684"),
)

SUPPORT = {
 "config/unit-test.env.example": "801525a75e9d6d9310311cd7b9ba80141551e7b86cd0e720ddb41a37a6f3a770",
 "scripts/two_site_pre_review_pytest_gate.py": "525c300668da01a8b82bd8488de708ee79db63ae0665b07828e29c74105b8535",
 "tests/test_p1_00_characterization_network_isolation.py": "e1aee47359a9f72eac838b000537992434c66eaf903d30cce40634da01da5b69",
}

ORIGINS = [
 {"path": "AE00_686_ROOT_ACTUAL_NATIVE_LAB_FIXTURE_20261013.json", "sha256": "11b6949ea091565b2db7c5cca2b79eaab0772745796b0bf6a3197a4c8ebb4c56"},
 {"path": "AE00_686_ROOT_NATIVE_FIXTURE_EXECUTION_20261013.json", "sha256": "c36d062f940396ee882e77372e9844abb2724286cded3c96673157a46e3de7dc"},
 {"path": "AE00_686_ROOT_ACTUAL_NATIVE_BATCH_FIXTURE_20261013.json", "sha256": "a503bc9f25a17b61a971cb017e23662148fa2abb1cfbb67dca41f5bd10a39881"},
]

HANDLER = "api/handlers.py"

ANCHOR = "scripts/lab_anchor.py"

TEST = "tests/test_p1_00b_characterization_acceptance.py"

GAP = "docs/behavior.json#get-users"

ANCHOR_TEXT = 'def lab_anchor():\n    """Readonly semantic anchor for a disclosed laboratory task."""\n    return "get-users"\n'

PROBE = '''import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.handlers import get_users


class TestLab:
    def test_403(self):
        assert get_users(403) == 403

    def test_404(self):
        assert get_users(404) == 404

    def test_403_alias(self):
        assert get_users(403) == 403
'''

RAW_FAULTS = ("source", "handler", "proposal", "task", "scope", "native", "selector", "model", "session", "controller",
              "prior", "feedback", "reviewer", "root", "prompt", "json", "yaml", "mode", "symlink", "hardlink", "oversize", "count", "parent")
NATIVE = (
    (r'''{
  "state": "PASSED",
  "proposal_sha256": "5a263d204bd1b0e70b9c8d5155061b89b5ec22b6d470124dee79bb4aa7937cb5",
  "selectors": [
    "tests/test_p1_00b_characterization_acceptance.py::TestLab::test_403"
  ],
  "unsupported_specs": [],
  "returncode": 0,
  "stdout_tail": ".                                                                        [100%]\n1 passed in 0.09s\n",
  "stderr_tail": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17747,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
''', r'''{
  "returncode": 0,
  "stdout": ".                                                                        [100%]\n1 passed in 0.08s\n",
  "stderr": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17747,
  "git_ancestry": null,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
'''),
    (r'''{
  "state": "PASSED",
  "proposal_sha256": "ab8697f430799b61001e3b3ac304c934e30f423ad4db9b4ae29426e945a6eceb",
  "selectors": [
    "tests/test_p1_00b_characterization_acceptance.py::TestLab::test_404"
  ],
  "unsupported_specs": [],
  "returncode": 0,
  "stdout_tail": ".                                                                        [100%]\n1 passed in 0.07s\n",
  "stderr_tail": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17822,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
''', r'''{
  "returncode": 0,
  "stdout": ".                                                                        [100%]\n1 passed in 0.09s\n",
  "stderr": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17822,
  "git_ancestry": null,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
'''),
    (r'''{
  "state": "PASSED",
  "proposal_sha256": "77804a4f2e9468c3e0449515b1045359f4d5d41f240643e80a09d9151e14ba5c",
  "selectors": [
    "tests/test_p1_00b_characterization_acceptance.py::TestLab::test_403_alias"
  ],
  "unsupported_specs": [],
  "returncode": 0,
  "stdout_tail": ".                                                                        [100%]\n1 passed in 0.08s\n",
  "stderr_tail": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17812,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
''', r'''{
  "returncode": 0,
  "stdout": ".                                                                        [100%]\n1 passed in 0.08s\n",
  "stderr": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17812,
  "git_ancestry": null,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
'''),
    (r'''{
  "state": "PASSED",
  "proposal_sha256": "e179d66761a77cc641f714a0ee568efc8bc05bc8b31e4504775f4e555bf80377",
  "selectors": [
    "tests/test_p1_00b_characterization_acceptance.py::TestLab::test_403",
    "tests/test_p1_00b_characterization_acceptance.py::TestLab::test_404"
  ],
  "unsupported_specs": [],
  "returncode": 0,
  "stdout_tail": "..                                                                       [100%]\n2 passed in 0.09s\n",
  "stderr_tail": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17747,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
''', r'''{
  "returncode": 0,
  "stdout": "..                                                                       [100%]\n2 passed in 0.12s\n",
  "stderr": "",
  "snapshot_files": 10,
  "snapshot_bytes": 17747,
  "git_ancestry": null,
  "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"
}
'''),
)
