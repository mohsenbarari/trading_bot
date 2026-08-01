from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core import dedicated_host_preflight_arvan_ecc_readback as adapter_module
from core import dedicated_host_preflight_controller as controller
from core.dedicated_host_preflight_receipt import canonical_json_bytes
from scripts.dedicated_host_preflight_manifest import EXPECTED_HOSTS, ROLE_ORDER


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dedicated_host_preflight_arvan_ecc_readback.py"
)
API_KEY = "QzE2MzU1YmQ4Y2YwYjgxYjJlZTI0YjQ1Njc4OTAxMjM0NTY3ODkw"


def target_for(role: str) -> controller.DedicatedHostTarget:
    expected = EXPECTED_HOSTS[role]
    route, phase = controller.DELIVERY_CONTRACT_BY_ROLE[role]
    return controller.DedicatedHostTarget(
        role=role,
        instance_id=expected["instance_id"],
        public_ipv4=expected["public_ip"],
        region=expected["region"],
        host_key_sha256=(format(ROLE_ORDER.index(role) + 1, "x") * 64),
        delivery_route=route,
        delivery_phase=phase,
    )


def ecc_server(target: controller.DedicatedHostTarget, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": target.instance_id,
        "status": "ACTIVE",
        "addresses": {
            "public": [
                {
                    "addr": target.public_ipv4,
                    "is_public": True,
                    "version": "4",
                }
            ],
            "private": [
                {
                    "addr": "10.0.0.9",
                    "is_public": False,
                    "version": "4",
                }
            ],
        },
    }
    value.update(changes)
    return value


class FakeRunner:
    def __init__(self, body_for=None, *, result=None, raises: Exception | None = None) -> None:
        self.body_for = body_for
        self.result = result
        self.raises = raises
        self.calls: list[adapter_module.ArvanEccGetServerInvocation] = []

    async def run(
        self, *, invocation: adapter_module.ArvanEccGetServerInvocation
    ) -> adapter_module.ArvanEccGetServerRunnerResult:
        self.calls.append(invocation)
        if self.raises is not None:
            raise self.raises
        if self.result is not None:
            return self.result
        assert self.body_for is not None
        return adapter_module.ArvanEccGetServerRunnerResult(
            status_code=200,
            body=canonical_json_bytes(self.body_for(invocation)) + b"\n",
        )


@unittest.skipUnless(os.geteuid() == 0, "Arvan ECC adapter explicitly requires root")
class DedicatedHostPreflightArvanEccReadbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-ecc-readback-")
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        self.config_path = self.root / "arvan-ecc-readback.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_secret_config(self, *, api_key: str = API_KEY, mode: int = 0o600) -> None:
        self.config_path.write_bytes(
            canonical_json_bytes(
                {
                    "schema": adapter_module.ARVAN_ECC_READBACK_SECRET_CONFIG_SCHEMA,
                    "enabled": True,
                    "api_key": api_key,
                }
            )
            + b"\n"
        )
        os.chmod(self.config_path, mode)

    def _adapter(self, runner: FakeRunner, **config: object) -> adapter_module.RootOwnedArvanEccProviderReadback:
        values = {"enabled": True}
        values.update(config)
        return adapter_module.RootOwnedArvanEccProviderReadback(
            config=adapter_module.RootOwnedArvanEccProviderReadbackConfig(**values),
            runner=runner,
        )

    def _readback(self, adapter: object, target: controller.DedicatedHostTarget):
        with mock.patch.object(
            adapter_module,
            "FIXED_ARVAN_ECC_READBACK_CONFIG_FILE",
            self.config_path,
        ):
            return asyncio.run(adapter.readback(target=target))

    def test_exact_active_server_normalizes_to_controller_canonical_evidence_for_all_four_hosts(self) -> None:
        self._write_secret_config()

        def body_for(invocation: adapter_module.ArvanEccGetServerInvocation) -> dict[str, object]:
            target = next(
                target_for(role)
                for role in ROLE_ORDER
                if invocation.path.endswith("/" + EXPECTED_HOSTS[role]["instance_id"])
            )
            return ecc_server(target)

        runner = FakeRunner(body_for)
        adapter = self._adapter(runner)
        for role in ROLE_ORDER:
            target = target_for(role)
            response = self._readback(adapter, target)
            raw = response["readback_bytes"]
            assert isinstance(raw, bytes)
            expected_document = {
                "schema": controller.PROVIDER_READBACK_SCHEMA,
                "role": role,
                "provider": "arvan_ecc",
                "instance_id": target.instance_id,
                "public_ipv4": target.public_ipv4,
                "region": target.region,
                "status": "running",
            }
            self.assertEqual(canonical_json_bytes(expected_document) + b"\n", raw)
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), response["readback_sha256"]
            )
            self.assertEqual(
                controller.PROVIDER_READBACK_PATH_BY_ROLE[role], response["readback_path"]
            )
            self.assertEqual(
                controller._validate_provider_readback(response, target=target)["readback"],  # type: ignore[attr-defined]
                expected_document,
            )

        self.assertEqual(4, len(runner.calls))
        for target, invocation in zip((target_for(role) for role in ROLE_ORDER), runner.calls, strict=True):
            self.assertEqual(adapter_module.FIXED_ARVAN_ECC_ENDPOINT, invocation.endpoint)
            self.assertEqual("GET", invocation.method)
            self.assertEqual(
                f"/regions/{target.region}/servers/{target.instance_id}", invocation.path
            )
            self.assertEqual("Apikey", invocation.authorization_scheme)
            self.assertEqual(API_KEY, invocation.api_key)
            self.assertNotIn(API_KEY, repr(invocation))
            self.assertNotIn(API_KEY, json.dumps(response, default=str))

    def test_default_off_and_target_source_pin_fail_before_secret_or_runner(self) -> None:
        runner = FakeRunner(lambda _invocation: {})
        disabled = self._adapter(runner, enabled=False)
        with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "DISABLED"):
            self._readback(disabled, target_for("bot_fi"))
        self.assertEqual([], runner.calls)

        self._write_secret_config()
        adapter = self._adapter(runner)
        forged = replace(target_for("bot_fi"), region="https://evil.invalid")
        with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "TARGET"):
            self._readback(adapter, forged)
        self.assertEqual([], runner.calls)

        wrong_source = replace(target_for("bot_fi"), public_ipv4="8.8.8.8")
        with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "SOURCE_PIN"):
            self._readback(adapter, wrong_source)
        self.assertEqual([], runner.calls)

    def test_fixed_private_config_rejects_unsafe_file_and_never_leaks_api_key(self) -> None:
        runner = FakeRunner(lambda _invocation: ecc_server(target_for("bot_fi")))
        adapter = self._adapter(runner)
        unsafe_key = "BADKEY-INJECTED-NEVER-LEAK\r\nX-Header: injected"
        self._write_secret_config(api_key=unsafe_key)
        with self.assertRaises(adapter_module.ArvanEccProviderReadbackError) as raised:
            self._readback(adapter, target_for("bot_fi"))
        self.assertEqual("ARVAN_ECC_READBACK_SECRET_CONFIG_INVALID", raised.exception.code)
        self.assertNotIn("BADKEY-INJECTED", str(raised.exception))
        self.assertEqual([], runner.calls)

        self._write_secret_config(mode=0o644)
        with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "SECRET_CONFIG"):
            self._readback(adapter, target_for("bot_fi"))
        self.assertEqual([], runner.calls)

        self.config_path.unlink()
        linked = self.root / "real-config.json"
        linked.write_bytes(b'{}')
        os.chmod(linked, 0o600)
        self.config_path.symlink_to(linked)
        with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "SECRET_CONFIG"):
            self._readback(adapter, target_for("bot_fi"))
        self.assertEqual([], runner.calls)

    def test_malformed_foreign_or_sensitive_provider_responses_fail_after_only_fixed_get(self) -> None:
        self._write_secret_config()
        target = target_for("webapp_ir")
        cases: list[tuple[object, str]] = [
            (b'{"id":"one","id":"two"}', "RESPONSE_INVALID"),
            (b"not-json", "RESPONSE_INVALID"),
            (ecc_server(target, id=target_for("bot_fi").instance_id), "RESPONSE_INVALID"),
            (ecc_server(target, status="SHUTOFF"), "RESPONSE_INVALID"),
            (ecc_server(target, region="foreign-region"), "RESPONSE_INVALID"),
            (
                ecc_server(
                    target,
                    addresses={
                        "public": [
                            {"addr": "8.8.8.8", "is_public": True, "version": "4"}
                        ]
                    },
                ),
                "RESPONSE_INVALID",
            ),
            (ecc_server(target, authorization="Apikey should-not-appear"), "RESPONSE_INVALID"),
            (ecc_server(target, image_url="https://evil.invalid"), "RESPONSE_INVALID"),
        ]
        for body, code in cases:
            with self.subTest(body=type(body).__name__):
                if isinstance(body, bytes):
                    runner = FakeRunner(
                        result=adapter_module.ArvanEccGetServerRunnerResult(
                            status_code=200, body=body
                        )
                    )
                else:
                    runner = FakeRunner(lambda _invocation, body=body: body)
                with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, code):
                    self._readback(self._adapter(runner), target)
                self.assertEqual(1, len(runner.calls))
                invocation = runner.calls[0]
                self.assertEqual("GET", invocation.method)
                self.assertEqual(adapter_module.FIXED_ARVAN_ECC_ENDPOINT, invocation.endpoint)

    def test_runner_failure_non_200_and_oversize_are_redacted_and_fail_closed(self) -> None:
        self._write_secret_config()
        target = target_for("witness")
        runner = FakeRunner(raises=RuntimeError("APIKEY-NEVER-LEAK"))
        with self.assertRaises(adapter_module.ArvanEccProviderReadbackError) as raised:
            self._readback(self._adapter(runner), target)
        self.assertEqual("ARVAN_ECC_READBACK_RUNNER_FAILED", raised.exception.code)
        self.assertNotIn("APIKEY-NEVER-LEAK", str(raised.exception))

        for result in (
            adapter_module.ArvanEccGetServerRunnerResult(status_code=500, body=b"{}"),
            adapter_module.ArvanEccGetServerRunnerResult(
                status_code=200,
                body=b"x" * (64 * 1024 + 1),
            ),
        ):
            with self.subTest(status=result.status_code, size=len(result.body)):
                failing = FakeRunner(result=result)
                with self.assertRaisesRegex(adapter_module.ArvanEccProviderReadbackError, "RESPONSE_INVALID"):
                    self._readback(self._adapter(failing), target)

    def test_module_has_no_direct_http_or_mutation_surface(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "aiohttp",
                "http",
                "httpx",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        forbidden_calls = {"delete", "patch", "post", "put", "request", "send", "urlopen"}
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
                for node in ast.walk(tree)
            )
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('method=_FIXED_METHOD', source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("three_site_full_matrix", source)


if __name__ == "__main__":
    unittest.main()
