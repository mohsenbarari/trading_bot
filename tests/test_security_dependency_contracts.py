"""Guard the security upgrade's runtime floor and coherent dependency family."""

from importlib.metadata import distribution, version
import unittest

from packaging.requirements import Requirement
from packaging.version import Version


class SecurityDependencyContractTests(unittest.TestCase):
    def test_security_fixed_dependency_floors_are_installed(self):
        floors = {
            "starlette": "1.3.1", "Pillow": "13.0.0", "aiohttp": "3.14.3",
            "urllib3": "2.8.0", "certifi": "2026.10.23",
            "python-multipart": "0.0.32", "PyJWT": "2.15.1",
            "anyio": "4.14.2", "cryptography": "50.0.2",
        }
        for name, floor in floors.items():
            with self.subTest(package=name):
                self.assertGreaterEqual(Version(version(name)), Version(floor))

    def test_framework_bot_and_network_dependencies_are_mutually_compatible(self):
        # A hand-forced transitive upgrade can pass a version-floor check while
        # violating FastAPI/aiogram/botocore's declared supported dependency range.
        for name in ("fastapi", "aiogram", "aiobotocore", "botocore", "pydantic-settings"):
            for raw in distribution(name).requires or ():
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                    continue
                with self.subTest(package=name, dependency=requirement.name):
                    self.assertIn(Version(version(requirement.name)), requirement.specifier)
