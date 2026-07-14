import unittest
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

import main
from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import WriterStateSnapshot
from core.writer_fencing import WriterFenceError, current_writer_fence_context


def request(method: str, path: str, *, client: str = "127.0.0.1", headers=None) -> Request:
    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": encoded_headers,
            "client": (client, 12345),
            "server": ("origin.example", 443),
        }
    )


def webapp_identity(site: str = "webapp_fi") -> RuntimeIdentity:
    return RuntimeIdentity("webapp", site, "iran", False)


def writer_snapshot(site: str = "webapp_fi", *, evidence: bool = False):
    now = datetime.now(timezone.utc)
    return WriterStateSnapshot(
        active_site=site,
        writer_epoch=4,
        control_state="active",
        transition_id="transition-current",
        readiness_evidence_hash="a" * 64 if evidence else None,
        readiness_evidence_id="origin-ready-current" if evidence else None,
        readiness_approved_by="operator@example" if evidence else None,
        readiness_approved_at=now - timedelta(seconds=10) if evidence else None,
        readiness_expires_at=now + timedelta(minutes=5) if evidence else None,
    )


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeHealthDb:
    async def execute(self, _statement):
        return ScalarResult("d1c6e7f8a9b0")


class MainPublicConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_public_config_returns_non_sensitive_settings(self):
        with patch.object(main.settings, "bot_username", "bot_user"), patch.object(main.settings, "frontend_url", "https://front.example"):
            result = await main.get_public_config()

        self.assertEqual(result, {"bot_username": "bot_user", "frontend_url": "https://front.example"})

    async def test_standby_rejects_unsafe_webapp_request_before_router(self):
        downstream = AsyncMock(return_value=Response(status_code=201))
        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity("webapp_ir")), patch(
            "main._load_runtime_writer_snapshot",
            new=AsyncMock(return_value=writer_snapshot("webapp_fi")),
        ):
            response = await main.enforce_webapp_writer_fence(
                request("POST", "/api/offers"),
                downstream,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["X-WebApp-Writer-State"], "fenced")
        downstream.assert_not_awaited()

    async def test_active_writer_context_reaches_downstream_request(self):
        observed = []

        async def downstream(_request):
            observed.append(current_writer_fence_context())
            return Response(status_code=201)

        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity()), patch(
            "main._load_runtime_writer_snapshot",
            new=AsyncMock(return_value=writer_snapshot()),
        ):
            response = await main.enforce_webapp_writer_fence(
                request("POST", "/api/offers"),
                downstream,
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(observed[0].physical_site, "webapp_fi")
        self.assertEqual(observed[0].writer_epoch, 4)
        self.assertIsNone(current_writer_fence_context())

    async def test_term_change_during_request_returns_controlled_503(self):
        async def downstream(_request):
            raise WriterFenceError("term changed")

        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity()), patch(
            "main._load_runtime_writer_snapshot",
            new=AsyncMock(return_value=writer_snapshot()),
        ):
            response = await main.enforce_webapp_writer_fence(
                request("PATCH", "/api/users/1"),
                downstream,
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("writer_term_changed", response.body.decode("utf-8"))

    async def test_sync_projection_and_safe_reads_are_not_writer_fenced(self):
        downstream = AsyncMock(return_value=Response(status_code=204))
        loader = AsyncMock(side_effect=AssertionError("writer state must not be loaded"))
        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity()), patch(
            "main._load_runtime_writer_snapshot",
            new=loader,
        ):
            sync_response = await main.enforce_webapp_writer_fence(
                request("POST", "/api/sync/receive"),
                downstream,
            )
            read_response = await main.enforce_webapp_writer_fence(
                request("GET", "/api/offers"),
                downstream,
            )

        self.assertEqual(sync_response.status_code, 204)
        self.assertEqual(read_response.status_code, 204)
        loader.assert_not_awaited()

    async def test_origin_readiness_is_hidden_without_key_or_loopback(self):
        with patch.object(main.settings, "origin_readiness_api_key", "configured-secret"):
            with self.assertRaises(HTTPException) as caught:
                await main.get_health_origin_ready(
                    request("GET", "/health/origin-ready", client="203.0.113.8"),
                    FakeHealthDb(),
                )

        self.assertEqual(caught.exception.status_code, 404)

    async def test_origin_readiness_requires_active_approved_exact_release(self):
        ready_snapshot = writer_snapshot(evidence=True)
        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity()), patch(
            "main._local_dependency_health",
            new=AsyncMock(return_value=(True, True, ())),
        ), patch(
            "main.load_writer_snapshot",
            new=AsyncMock(return_value=ready_snapshot),
        ), patch.object(
            main.settings, "release_sha", "release-current"
        ), patch.object(
            main.settings, "origin_expected_migration_revision", "d1c6e7f8a9b0"
        ), patch.object(
            main.settings, "background_jobs_enabled", True
        ), patch(
            "main.Path.is_file", return_value=True
        ):
            response = await main.get_health_origin_ready(
                request("GET", "/health/origin-ready"),
                FakeHealthDb(),
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["origin_ready"])
        self.assertEqual(payload["writer_epoch"], 4)
        self.assertEqual(payload["migration_revision"], "d1c6e7f8a9b0")

    async def test_origin_readiness_fails_for_standby_even_when_dependencies_work(self):
        with patch.object(main, "RUNTIME_IDENTITY", webapp_identity("webapp_ir")), patch(
            "main._local_dependency_health",
            new=AsyncMock(return_value=(True, True, ())),
        ), patch(
            "main.load_writer_snapshot",
            new=AsyncMock(return_value=writer_snapshot("webapp_fi", evidence=True)),
        ), patch.object(
            main.settings, "release_sha", "release-current"
        ), patch.object(
            main.settings, "origin_expected_migration_revision", "d1c6e7f8a9b0"
        ), patch.object(
            main.settings, "background_jobs_enabled", True
        ), patch(
            "main.Path.is_file", return_value=True
        ):
            response = await main.get_health_origin_ready(
                request("GET", "/health/origin-ready"),
                FakeHealthDb(),
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(payload["origin_ready"])
        self.assertIn("writer_active_site_mismatch", payload["reasons"])


if __name__ == "__main__":
    unittest.main()
