from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest import mock

import yaml

from scripts import production_shadow_startup_normalization_worker as MODULE


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


def prepared_request(role: str = "bot_fi") -> dict:
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "role": role,
        "expected_host": MODULE.ROLE_HOSTS[role],
        "role_manifest_sha256": digest("manifest"),
        "agent_sha256": digest("inventory"),
        "contract_worker_sha256": digest("contract"),
        "controller_challenge_sha256": digest("inventory-challenge"),
        "expected_database_state": "running-healthy",
    }


def prepared_response(role: str = "bot_fi") -> dict:
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "role": role,
        "expected_host": MODULE.ROLE_HOSTS[role],
        "role_manifest_sha256": digest("manifest"),
        "prepared_database_running": True,
        "prepared_database_healthy": True,
        "operation_resource_counts": {
            "container": 1,
            "network": 1,
            "volume": 0,
            "image": 0,
        },
        "stable_capture_count": 2,
        "descriptors_returned": False,
        "environment_values_returned": False,
        "path_descriptors_returned": False,
        "docker_read_only": True,
        "network_io_performed": False,
        "filesystem_mutated": False,
        "response_sha256": digest("pre-response"),
        "prepared_container_id": "1" * 64,
        "prepared_network_id": "2" * 64,
        "prepared_container_identity_sha256": digest(
            "container-identity"
        ),
        "prepared_container_metadata_sha256": digest(
            "container-metadata"
        ),
        "prepared_network_identity_sha256": digest("network-identity"),
        "prepared_network_metadata_sha256": digest("network-metadata"),
        "prepared_config_sha256": digest("config"),
        "prepared_environment_sha256": digest("environment"),
        "prepared_environment_entry_count": 17,
        "prepared_compose_config_sha256": digest("compose-config"),
        "prepared_host_config_sha256": digest("host-config"),
        "prepared_mounts_sha256": digest("mounts"),
        "prepared_network_attachment_sha256": digest("attachment"),
    }


def manifest_path(role: str) -> Path:
    contract = (
        "wa-ir-operation"
        if role == "webapp_ir"
        else "finland-precommit"
    )
    return MODULE.INVENTORY._prepared_manifest_path(  # noqa: SLF001
        operation_id=OPERATION_ID,
        role=role,
        contract_kind=contract,
    )


def request(role: str = "bot_fi") -> dict:
    release_root = (
        MODULE.INVENTORY.PROJECT_ROOT_PREFIX
        / OPERATION_ID
        / "releases"
        / RELEASE_SHA
    )
    value = {
        "schema": MODULE.REQUEST_SCHEMA,
        "status": "authorized-request",
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": RELEASE_TREE_SHA,
        "role": role,
        "expected_host": MODULE.ROLE_HOSTS[role],
        "controller_challenge_sha256": digest("normalization-challenge"),
        "issued_at": MODULE._timestamp(NOW),
        "expires_at": MODULE._timestamp(NOW + timedelta(seconds=120)),
        "worker_path": str(release_root / MODULE.WORKER_RELATIVE),
        "worker_sha256": digest("worker"),
        "inventory_agent_sha256": digest("inventory"),
        "contract_worker_sha256": digest("contract"),
        "role_manifest_path": str(manifest_path(role)),
        "role_manifest_sha256": digest("manifest"),
        "pre_inventory_request": prepared_request(role),
        "pre_inventory_response": prepared_response(role),
        "constraints": dict(MODULE.EXPECTED_CONSTRAINTS),
        "request_binding_sha256": MODULE.ZERO_SHA256,
    }
    value["request_binding_sha256"] = MODULE._binding(value)
    return value


def logical(seed: str) -> MODULE.LogicalState:
    return MODULE.LogicalState(
        database_fingerprint_sha256=digest(f"database-{seed}"),
        database_row_count=12,
        database_table_count=3,
        uploads_tree_sha256=digest(f"uploads-{seed}"),
        audit_tree_sha256=digest(f"audit-{seed}"),
        redis_tree_sha256=digest("empty-redis"),
    )


class FakeBackend:
    def __init__(
        self,
        *,
        states: list[MODULE.LogicalState] | None = None,
        normalize_error: BaseException | None = None,
        stop_error: BaseException | None = None,
        database_running: bool = True,
    ) -> None:
        stable = logical("normalized")
        self.states = list(states or [logical("before"), stable, stable])
        self.normalize_error = normalize_error
        self.stop_error = stop_error
        self.calls: list[str] = []
        self.normalizations = 0
        self.stops = 0
        self.database_running = database_running

    def reconcile_prepared_database_running(self) -> MODULE.StartState:
        self.calls.append("start-reconcile")
        was_running = self.database_running
        self.database_running = True
        return MODULE.StartState(
            database_container_id="1" * 64,
            network_id="2" * 64,
            database_was_running=was_running,
            database_start_performed=not was_running,
            oneoff_residue_count=0,
        )

    def logical_state(self) -> MODULE.LogicalState:
        self.calls.append("state")
        if not self.states:
            raise AssertionError("unexpected state read")
        return self.states.pop(0)

    def normalize_once(self) -> dict:
        self.calls.append("normalize")
        self.normalizations += 1
        if self.normalize_error is not None:
            raise self.normalize_error
        return {
            "schema": (
                "production-shadow-startup-normalization-invocation-v1"
            ),
            "status": "normalized",
            "role": "bot_fi",
            "background_jobs_enabled": False,
            "provider_credentials_present": False,
            "provider_network_used": False,
            "redis_started": False,
            "public_service_started": False,
        }

    def stop_operation_containers(self) -> MODULE.StopState:
        self.calls.append("stop")
        self.stops += 1
        if self.stop_error is not None:
            raise self.stop_error
        self.database_running = False
        return MODULE.StopState(
            database_container_id="1" * 64,
            network_id="2" * 64,
            operation_owned_running_container_count=0,
            oneoff_residue_count=0,
        )


class StartupNormalizationWorkerTests(unittest.TestCase):
    def inventory_validation(self):
        return mock.patch.object(
            MODULE,
            "_validate_inventory_pair",
            side_effect=lambda req, resp, now: (dict(req), dict(resp)),
        )

    def test_request_is_exact_and_challenge_bound(self):
        value = request()
        with self.inventory_validation():
            observed = MODULE.validate_request(value, now=NOW)
        self.assertEqual(observed, value)
        self.assertNotEqual(
            observed["controller_challenge_sha256"],
            observed["pre_inventory_request"][
                "controller_challenge_sha256"
            ],
        )

    def test_build_request_uses_operation_derived_worker_path(self):
        with self.inventory_validation():
            observed = MODULE.build_request(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                release_tree_sha=RELEASE_TREE_SHA,
                role="bot_fi",
                worker_sha256=digest("worker"),
                inventory_agent_sha256=digest("inventory"),
                contract_worker_sha256=digest("contract"),
                role_manifest_path=manifest_path("bot_fi"),
                role_manifest_sha256=digest("manifest"),
                pre_inventory_request=prepared_request(),
                pre_inventory_response=prepared_response(),
                controller_challenge_sha256=digest(
                    "normalization-challenge"
                ),
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=120),
            )
        self.assertTrue(
            observed["worker_path"].endswith(
                MODULE.WORKER_RELATIVE.as_posix()
            )
        )

    def test_request_rejects_expiry_and_future_replay(self):
        value = request()
        with self.inventory_validation():
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationError,
                "stale",
            ):
                MODULE.validate_request(
                    value,
                    now=NOW + timedelta(seconds=121),
                )
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationError,
                "stale",
            ):
                MODULE.validate_request(
                    value,
                    now=NOW - timedelta(seconds=6),
                )

    def test_request_rejects_reused_inventory_challenge(self):
        value = request()
        value["controller_challenge_sha256"] = value[
            "pre_inventory_request"
        ]["controller_challenge_sha256"]
        value["request_binding_sha256"] = MODULE._binding(value)
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "challenge",
        ):
            MODULE.validate_request(value, now=NOW)

    def test_request_rejects_cross_role_receipt(self):
        value = request()
        value["pre_inventory_request"]["role"] = "webapp_fi"
        value["request_binding_sha256"] = MODULE._binding(value)
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "identity",
        ):
            MODULE.validate_request(value, now=NOW)

    def test_request_rejects_binding_tamper(self):
        value = request()
        value["expected_host"] = MODULE.ROLE_HOSTS["webapp_fi"]
        with self.inventory_validation(), self.assertRaises(
            MODULE.StartupNormalizationError,
        ):
            MODULE.validate_request(value, now=NOW)

    def test_request_rejects_untrusted_inventory_validator(self):
        with mock.patch.object(
            MODULE.INVENTORY,
            "validate_prepared_response",
            None,
            create=True,
        ), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "validator is unavailable",
        ):
            MODULE.validate_request(request(), now=NOW)

    def test_plan_is_non_mutating(self):
        backend = FakeBackend()
        with self.inventory_validation():
            observed = MODULE.execute(
                request(),
                backend=backend,
                now=NOW,
            )
        self.assertEqual(observed["status"], "planned")
        self.assertFalse(observed["live_actions_performed"])
        self.assertEqual(backend.calls, [])

    def test_execute_runs_twice_and_leaves_stopped(self):
        backend = FakeBackend()
        value = request()
        checkpoints: list[str] = []
        times = iter(
            [
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=2),
            ]
        )
        with self.inventory_validation():
            observed = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda checkpoint: (
                    checkpoints.append(checkpoint) or True
                ),
                backend=backend,
                now=NOW,
                clock=lambda: next(times),
            )
        self.assertEqual(observed["status"], "normalized-stopped")
        self.assertEqual(backend.normalizations, 2)
        self.assertEqual(backend.stops, 1)
        self.assertEqual(
            backend.calls,
            [
                "start-reconcile",
                "state",
                "normalize",
                "state",
                "normalize",
                "state",
                "stop",
            ],
        )
        self.assertEqual(
            checkpoints,
            [
                "before-initial-state",
                "before-first-startup-normalization",
                "before-first-state",
                "before-second-startup-normalization",
                "before-second-state",
                "before-database-stop",
            ],
        )
        self.assertEqual(
            observed["second_start_database_delta_count"],
            0,
        )
        self.assertEqual(
            observed["operation_owned_running_container_count"],
            0,
        )
        self.assertTrue(
            observed["database_was_running_at_reconciliation"]
        )
        self.assertFalse(observed["database_start_performed"])

    def test_lost_result_retry_restarts_exact_stopped_database(self):
        stable = logical("normalized")
        backend = FakeBackend(
            states=[
                logical("before-first"),
                stable,
                stable,
                logical("before-retry"),
                stable,
                stable,
            ]
        )
        value = request()
        with self.inventory_validation():
            first = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
                clock=iter(
                    [
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                    ]
                ).__next__,
            )
            # Simulate a controller crash after the host completed but before
            # the returned result became durable.
            del first
            retried = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW + timedelta(seconds=3),
                clock=iter(
                    [
                        NOW + timedelta(seconds=4),
                        NOW + timedelta(seconds=5),
                    ]
                ).__next__,
            )
        self.assertTrue(retried["database_start_performed"])
        self.assertFalse(
            retried["database_was_running_at_reconciliation"]
        )
        self.assertEqual(backend.normalizations, 4)
        self.assertEqual(backend.stops, 2)
        self.assertFalse(backend.database_running)

    def test_exact_stopped_database_reconciliation_starts_only_bound_id(self):
        backend = MODULE.ExactReleaseBackend.__new__(
            MODULE.ExactReleaseBackend
        )
        backend.role = "bot_fi"
        backend.request = {
            "pre_inventory_response": {
                "prepared_container_id": "1" * 64,
                "prepared_network_id": "2" * 64,
            }
        }
        with (
            mock.patch.object(
                backend,
                "_database_id",
                return_value="1" * 64,
            ),
            mock.patch.object(
                backend,
                "_validated_database_running",
                side_effect=[False, True],
            ),
            mock.patch.object(
                backend,
                "_network_id",
                return_value="2" * 64,
            ),
            mock.patch.object(
                backend,
                "_oneoff_ids",
                return_value=[],
            ),
            mock.patch.object(
                backend,
                "_database_healthy",
                return_value=True,
            ),
            mock.patch.object(MODULE.PRECOMMIT, "_run") as run,
        ):
            observed = backend.reconcile_prepared_database_running()
        self.assertTrue(observed.database_start_performed)
        run.assert_called_once_with(
            [MODULE.PRECOMMIT.DOCKER, "start", "1" * 64],
            timeout=300,
        )

    def test_foreign_stopped_database_is_rejected_without_start(self):
        backend = MODULE.ExactReleaseBackend.__new__(
            MODULE.ExactReleaseBackend
        )
        backend.role = "bot_fi"
        backend.request = {
            "pre_inventory_response": {
                "prepared_container_id": "1" * 64,
                "prepared_network_id": "2" * 64,
            }
        }
        with (
            mock.patch.object(
                backend,
                "_database_id",
                return_value="3" * 64,
            ),
            mock.patch.object(MODULE.PRECOMMIT, "_run") as run,
            self.assertRaisesRegex(
                MODULE.StartupNormalizationError,
                "prepared identity",
            ),
        ):
            backend.reconcile_prepared_database_running()
        run.assert_not_called()

    def test_first_invocation_may_normalize_data(self):
        normalized = logical("normalized")
        backend = FakeBackend(
            states=[logical("legacy-startup"), normalized, normalized]
        )
        value = request()
        with self.inventory_validation():
            observed = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
                clock=iter(
                    [
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                    ]
                ).__next__,
            )
        self.assertNotEqual(
            observed["before_state"],
            observed["first_invocation_state"],
        )

    def test_second_invocation_delta_fails_and_stops(self):
        backend = FakeBackend(
            states=[
                logical("before"),
                logical("first"),
                logical("second"),
            ]
        )
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "second startup",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.stops, 1)

    def test_normalization_failure_stops_database(self):
        backend = FakeBackend(
            normalize_error=MODULE.StartupNormalizationError("boom")
        )
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "boom",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.stops, 1)

    def test_baseexception_preserves_cleanup(self):
        backend = FakeBackend(normalize_error=KeyboardInterrupt())
        value = request()
        with self.inventory_validation(), self.assertRaises(
            KeyboardInterrupt,
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.stops, 1)

    def test_authority_loss_at_every_checkpoint_stops(self):
        value = request()
        checkpoints = [
            "before-initial-state",
            "before-first-startup-normalization",
            "before-first-state",
            "before-second-startup-normalization",
            "before-second-state",
            "before-database-stop",
        ]
        for denied in checkpoints:
            backend = FakeBackend()

            def authority(checkpoint: str) -> bool:
                return checkpoint != denied

            with self.subTest(denied=denied), self.inventory_validation():
                with self.assertRaises(
                    MODULE.StartupNormalizationCancellation,
                ):
                    MODULE.execute(
                        value,
                        apply=True,
                        confirm=MODULE.confirmation_phrase(value),
                        authority=authority,
                        backend=backend,
                        now=NOW,
                    )
            self.assertEqual(backend.stops, 1)

    def test_apply_requires_exact_confirmation(self):
        backend = FakeBackend()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "confirmation",
        ):
            MODULE.execute(
                request(),
                apply=True,
                confirm="wrong",
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.calls, [])

    def test_cleanup_failure_fails_closed(self):
        backend = FakeBackend(
            stop_error=MODULE.StartupNormalizationError("stop failed")
        )
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "stop failed",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )

    def test_cancellation_during_stop_is_deferred_until_reconciled(self):
        class DeferredAuthority:
            def __init__(self) -> None:
                self.pending = False
                self.deferred = False

            def __call__(self, _checkpoint: str) -> bool:
                return True

            def defer_cancellation(self):
                @contextlib.contextmanager
                def scope():
                    self.deferred = True
                    try:
                        yield
                    finally:
                        self.deferred = False

                return scope()

            def check(self) -> None:
                if self.pending:
                    raise MODULE.StartupNormalizationCancellation(
                        "controller EOF during stop"
                    )

        authority = DeferredAuthority()
        backend = FakeBackend()
        original = backend.stop_operation_containers

        def stop_with_eof() -> MODULE.StopState:
            self.assertTrue(authority.deferred)
            authority.pending = True
            return original()

        backend.stop_operation_containers = stop_with_eof  # type: ignore[method-assign]
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationCancellation,
            "EOF during stop",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=authority,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.stops, 1)
        self.assertEqual(backend.calls[-1], "stop")

    def test_stdio_signal_is_recorded_while_stop_is_deferred(self):
        authority = MODULE.StdioAuthority(digest("request"))
        with (
            mock.patch.object(authority, "_stop_monitor"),
            authority.defer_cancellation(),
        ):
            authority._handle_signal(MODULE.signal.SIGTERM, None)
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationCancellation,
            "signal",
        ):
            authority.check()

    def test_stdio_eof_is_observed_while_stop_is_deferred(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)
        authority = MODULE.StdioAuthority(digest("request"))

        def deliver(_thread_id: int, signum: int) -> None:
            authority._handle_signal(signum, None)

        try:
            with (
                mock.patch.object(
                    MODULE.sys,
                    "stdin",
                    SimpleNamespace(buffer=reader),
                ),
                mock.patch.object(
                    MODULE.signal,
                    "pthread_kill",
                    side_effect=deliver,
                ),
                authority,
            ):
                authority._start_monitor()
                with authority.defer_cancellation():
                    os.close(write_fd)
                    write_fd = -1
                    deadline = time.monotonic() + 1.0
                    while (
                        not authority._cancelled.is_set()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    self.assertTrue(authority._cancelled.is_set())
                    if authority._monitor is not None:
                        authority._monitor.join(timeout=1.0)
                with self.assertRaisesRegex(
                    MODULE.StartupNormalizationCancellation,
                    "EOF",
                ):
                    authority.check()
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            reader.close()

    def test_original_error_precedes_stop_time_cancellation(self):
        class DeferredAuthority:
            def __init__(self) -> None:
                self.pending = False

            def __call__(self, _checkpoint: str) -> bool:
                return True

            def defer_cancellation(self):
                return contextlib.nullcontext()

            def check(self) -> None:
                if self.pending:
                    raise MODULE.StartupNormalizationCancellation(
                        "controller EOF during stop"
                    )

        authority = DeferredAuthority()
        backend = FakeBackend(
            normalize_error=MODULE.StartupNormalizationError(
                "primary normalization error"
            )
        )
        original_stop = backend.stop_operation_containers

        def stop_with_eof() -> MODULE.StopState:
            authority.pending = True
            return original_stop()

        backend.stop_operation_containers = stop_with_eof  # type: ignore[method-assign]
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "primary normalization error",
        ) as caught:
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=authority,
                backend=backend,
                now=NOW,
            )
        self.assertEqual(backend.stops, 1)
        self.assertTrue(
            any(
                "authority was lost" in note
                for note in getattr(caught.exception, "__notes__", ())
            )
        )

    def test_stdio_restore_attaches_cleanup_failure_to_primary(self):
        authority = MODULE.StdioAuthority(digest("request"))
        with (
            mock.patch.object(
                authority,
                "_stop_monitor",
                side_effect=RuntimeError("monitor cleanup failed"),
            ),
            self.assertRaisesRegex(ValueError, "primary") as caught,
        ):
            with authority:
                raise ValueError("primary")
        self.assertTrue(
            any(
                "stdio authority cleanup also failed" in note
                for note in getattr(caught.exception, "__notes__", ())
            )
        )

    def test_stopped_identity_must_match_prepared(self):
        backend = FakeBackend()
        original = backend.stop_operation_containers

        def wrong_stop() -> MODULE.StopState:
            state = original()
            return MODULE.StopState(
                database_container_id="3" * 64,
                network_id=state.network_id,
                operation_owned_running_container_count=0,
                oneoff_residue_count=0,
            )

        backend.stop_operation_containers = wrong_stop  # type: ignore[method-assign]
        value = request()
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "prepared identities",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
            )

    def test_completion_after_expiry_fails_closed(self):
        backend = FakeBackend()
        value = request()
        times = iter(
            [
                NOW + timedelta(seconds=1),
                NOW + timedelta(seconds=121),
            ]
        )
        with self.inventory_validation(), self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "outside",
        ):
            MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
                clock=lambda: next(times),
            )
        self.assertEqual(backend.stops, 1)

    def test_result_rejects_tampered_safety_claim(self):
        backend = FakeBackend()
        value = request()
        with self.inventory_validation():
            observed = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
                clock=iter(
                    [
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                    ]
                ).__next__,
            )
            observed["redis_started"] = True
            observed["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in observed.items()
                        if key != "response_sha256"
                    }
                )
            )
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationError,
                "safety closure",
            ):
                MODULE.validate_result(
                    observed,
                    request=value,
                    now=NOW + timedelta(seconds=2),
                )

    def test_result_rejects_second_state_copy_tamper(self):
        backend = FakeBackend()
        value = request()
        with self.inventory_validation():
            observed = MODULE.execute(
                value,
                apply=True,
                confirm=MODULE.confirmation_phrase(value),
                authority=lambda _checkpoint: True,
                backend=backend,
                now=NOW,
                clock=iter(
                    [
                        NOW + timedelta(seconds=1),
                        NOW + timedelta(seconds=2),
                    ]
                ).__next__,
            )
            observed["second_invocation_state"] = logical(
                "different"
            ).document()
            observed["response_sha256"] = MODULE._sha256(
                MODULE._canonical_json(
                    {
                        key: item
                        for key, item in observed.items()
                        if key != "response_sha256"
                    }
                )
            )
            with self.assertRaisesRegex(
                MODULE.StartupNormalizationError,
                "second startup",
            ):
                MODULE.validate_result(
                    observed,
                    request=value,
                    now=NOW + timedelta(seconds=2),
                )

    def test_normalization_program_is_db_only(self):
        program = MODULE.NORMALIZATION_PROGRAM
        self.assertIn("await init_db()", program)
        self.assertIn(
            "await verify_three_site_database_role_bindings()",
            program,
        )
        self.assertIn(
            "await _run_authorized_startup_mutations(_snapshot)",
            program,
        )
        self.assertIn('"BACKGROUND_JOBS_ENABLED"', program)
        self.assertNotIn("init_redis", program)
        self.assertNotIn("uvicorn", program)
        self.assertNotIn("requests.", program)

    def test_actual_compose_service_env_has_runtime_prerequisites(self):
        compose = yaml.safe_load(
            (
                MODULE.REPO_ROOT
                / "deploy/production/docker-compose.three-site-shadow.yml"
            ).read_text(encoding="utf-8")
        )
        services = compose["services"]
        selections = {
            "bot_fi": services["bot_fi_migration"],
            "webapp_fi": services["webapp_fi_migration"],
            "webapp_ir": services["webapp_ir_migration"],
        }
        for role, service in selections.items():
            with self.subTest(role=role):
                environment = service["environment"]
                self.assertEqual(environment["PHYSICAL_SITE"], role)
                self.assertEqual(
                    environment["LOGICAL_AUTHORITY"],
                    "foreign" if role == "bot_fi" else "webapp",
                )
                self.assertEqual(
                    environment["THREE_SITE_DR_ENABLED"],
                    "true",
                )
                self.assertEqual(
                    environment["DR_EVENT_PROTOCOL_STRICT"],
                    "true",
                )
                self.assertEqual(
                    environment["BACKGROUND_JOBS_ENABLED"],
                    "false",
                )
                self.assertIn("DATABASE_URL", environment)
                self.assertIn("SYNC_DATABASE_URL", environment)
                self.assertIn("JWT_SECRET_KEY", environment)
                self.assertFalse(
                    MODULE.FORBIDDEN_PROVIDER_ENV & set(environment)
                )
                self.assertEqual(service["networks"], [role])

    def test_wa_normalization_command_satisfies_real_command_validator(self):
        prefix = [
            MODULE.WA_OPERATION.DOCKER,
            "compose",
            "--project-name",
            "three-site-shadow-operation",
            "--file",
            "/srv/operation/compose.yml",
            "--env-file",
            "/srv/operation/runtime.env",
        ]
        arguments = [
            *prefix,
            "--profile",
            "webapp-ir-prepare",
            "run",
            "--rm",
            "--no-deps",
            "--label",
            "trading-bot.production.operation-id="
            "7fb08095-7a9e-4a92-9fa9-3f9a301b2944",
            "-T",
            "webapp_ir_db_roles",
            "python",
            "-c",
            MODULE.NORMALIZATION_PROGRAM,
        ]
        MODULE.WA_OPERATION._validate_command(
            arguments,
            timeout=900,
            env=MODULE.WA_OPERATION._SAFE_ENV,
        )
        serialized = "\0".join(arguments)
        self.assertNotIn("WEBAPP_IR_APP_DB_PASSWORD", serialized)
        self.assertNotIn("--env", arguments)

    def test_wa_normalization_rejects_host_secret_environment(self):
        with self.assertRaises(
            MODULE.WA_OPERATION.ProductionOperationError,
        ):
            MODULE.WA_OPERATION._validate_command(
                [MODULE.WA_OPERATION.DOCKER, "version"],
                timeout=30,
                env={
                    **MODULE.WA_OPERATION._SAFE_ENV,
                    "THREE_SITE_APP_DB_PASSWORD": "must-not-cross-host-env",
                },
            )

    def test_wa_backend_uses_roles_service_without_host_secret(self):
        backend = MODULE.ExactReleaseBackend.__new__(
            MODULE.ExactReleaseBackend
        )
        backend.role = "webapp_ir"
        backend.prefix = [
            MODULE.WA_OPERATION.DOCKER,
            "compose",
            "--project-name",
            "three-site-shadow-operation",
            "--file",
            "/srv/operation/compose.yml",
            "--env-file",
            "/srv/operation/runtime.env",
        ]
        backend.profile = "webapp-ir-prepare"
        backend.services = {
            "database": "webapp_ir_db",
            "roles": "webapp_ir_db_roles",
            "migration": "webapp_ir_migration",
        }
        backend.manifest = SimpleNamespace(operation_id=OPERATION_ID)
        backend.runtime_env = {
            "WEBAPP_IR_APP_DB_PASSWORD": "never-serialize-this-secret"
        }
        backend.paths = SimpleNamespace(
            project_root=Path("/srv/operation")
        )
        invocation = json.dumps(
            {
                "schema": (
                    "production-shadow-startup-normalization-invocation-v1"
                ),
                "status": "normalized",
                "role": "webapp_ir",
                "background_jobs_enabled": False,
                "provider_credentials_present": False,
                "provider_network_used": False,
                "redis_started": False,
                "public_service_started": False,
            }
        )
        with (
            mock.patch.object(
                backend,
                "_ensure_running_database",
                return_value=("1" * 64, "2" * 64),
            ),
            mock.patch.object(backend, "_oneoff_ids", return_value=[]),
            mock.patch.object(
                MODULE.WA_OPERATION,
                "_run",
                return_value=invocation,
            ) as run,
            mock.patch.object(
                MODULE.WA_OPERATION,
                "_cleanup_operation_oneoffs",
            ),
            mock.patch.object(
                MODULE.WA_OPERATION,
                "_late_reconciliation_scope",
                return_value=contextlib.nullcontext(),
            ),
        ):
            observed = backend.normalize_once()
        self.assertEqual(observed["status"], "normalized")
        arguments = run.call_args.args[0]
        command_env = run.call_args.kwargs["env"]
        self.assertIn("webapp_ir_db_roles", arguments)
        self.assertNotIn("webapp_ir_migration", arguments)
        self.assertNotIn("--env", arguments)
        self.assertEqual(command_env, MODULE.WA_OPERATION._SAFE_ENV)
        self.assertNotIn(
            "never-serialize-this-secret",
            "\0".join(arguments),
        )
        self.assertNotIn(
            "never-serialize-this-secret",
            command_env.values(),
        )

    def test_worker_source_has_no_raw_subprocess_or_child_signal(self):
        source = Path(MODULE.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "subprocess.run(",
            "subprocess.Popen(",
            "os.kill(",
            "os.killpg(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_audited_runner_timeout_reaps_detached_descendant(self):
        marker = "startup-normalization-timeout-" + digest("timeout")
        program = (
            "import os,time\n"
            f"marker={marker!r}\n"
            "child=os.fork()\n"
            "if child == 0:\n"
            " os.setsid(); time.sleep(60); raise SystemExit(0)\n"
            "time.sleep(60)\n"
        )
        with self.assertRaises(
            MODULE.WA_OPERATION.ProductionOperationError,
        ):
            MODULE.WA_OPERATION._run(
                ["/usr/bin/python3", "-c", program],
                timeout=1,
                env=MODULE.WA_OPERATION._SAFE_ENV,
            )
        self._assert_process_marker_absent(marker)

    def test_audited_runner_baseexception_reaps_detached_descendant(self):
        class InjectedBaseException(BaseException):
            pass

        marker = "startup-normalization-baseexception-" + digest("fatal")
        program = (
            "import os,time\n"
            f"marker={marker!r}\n"
            "child=os.fork()\n"
            "if child == 0:\n"
            " os.setsid(); time.sleep(60); raise SystemExit(0)\n"
            "time.sleep(60)\n"
        )
        checks = 0

        def authority() -> None:
            nonlocal checks
            checks += 1
            if checks > 2:
                raise InjectedBaseException()

        with (
            mock.patch.object(
                MODULE.WA_OPERATION,
                "_check_controller_authority",
                side_effect=authority,
            ),
            self.assertRaises(InjectedBaseException),
        ):
            MODULE.WA_OPERATION._run(
                ["/usr/bin/python3", "-c", program],
                timeout=30,
                env=MODULE.WA_OPERATION._SAFE_ENV,
            )
        self._assert_process_marker_absent(marker)

    def _assert_process_marker_absent(self, marker: str) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            found = False
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    command = (entry / "cmdline").read_bytes()
                except OSError:
                    continue
                if marker.encode("ascii") in command:
                    found = True
                    break
            if not found:
                return
            if time.monotonic() >= deadline:
                self.fail("audited runner left a detached descendant")
            time.sleep(0.02)

    def test_invocation_output_is_exact(self):
        value = {
            "schema": (
                "production-shadow-startup-normalization-invocation-v1"
            ),
            "status": "normalized",
            "role": "bot_fi",
            "background_jobs_enabled": False,
            "provider_credentials_present": False,
            "provider_network_used": False,
            "redis_started": False,
            "public_service_started": False,
        }
        self.assertEqual(
            MODULE._normalization_output(
                json.dumps(value),
                role="bot_fi",
            ),
            value,
        )
        value["provider_network_used"] = True
        with self.assertRaises(
            MODULE.StartupNormalizationError,
        ):
            MODULE._normalization_output(
                json.dumps(value),
                role="bot_fi",
            )

    def test_state_digest_binds_all_roots(self):
        state = logical("same").document()
        MODULE._validate_state(state, label="state")
        state["uploads_tree_sha256"] = digest("other")
        with self.assertRaisesRegex(
            MODULE.StartupNormalizationError,
            "digest differs",
        ):
            MODULE._validate_state(state, label="state")

    def test_stdio_authority_requires_exact_echo(self):
        authority = MODULE.StdioAuthority(digest("request"))
        self.assertEqual(authority.sequence, 0)
        self.assertEqual(
            MODULE.AUTHORITY_REQUEST_SCHEMA,
            "production-shadow-startup-normalization-authority-request-v1",
        )


if __name__ == "__main__":
    unittest.main()
