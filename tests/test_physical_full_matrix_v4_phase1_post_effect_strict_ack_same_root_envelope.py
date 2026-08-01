"""Adversarial tests for P1's non-candidate live-root diagnostic.

The diagnostic observes an actual local ``AsyncSession`` root without opening
a database connection.  It intentionally cannot make prepare/project
available: Gen2's pending result has no session/root provenance in its public
API, so a Python-object association would not be a DB causal fence.
"""

from __future__ import annotations

import ast
import copy
import importlib
from pathlib import Path
import pickle
import unittest

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core import (
    physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope as subject,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope.py"
)


class _FakeSession:
    """A convincing shape is still not an exact AsyncSession."""

    def in_transaction(self) -> bool:
        return True

    def in_nested_transaction(self) -> bool:
        return False


class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeTests(
    unittest.IsolatedAsyncioTestCase
):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_class = getattr(
            importlib.import_module(
                "tests.test_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint"
            ),
            "PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointTests",
        )
        fixture_class.setUpClass()
        cls._fixture_class = fixture_class
        cls.fixture = fixture_class
        cls.fixture_case = fixture_class("runTest")
        cls.config = (
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig(
                checkpoint_config=cls.fixture.config,
                enabled=True,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fixture_class.tearDownClass()

    async def _session(self) -> tuple[AsyncSession, object]:
        engine = create_async_engine(
            "postgresql+asyncpg://p1-envelope:p1-envelope@127.0.0.1:1/p1_envelope"
        )
        session = AsyncSession(bind=engine)
        root = await session.begin()

        async def cleanup() -> None:
            try:
                if session.in_transaction():
                    await session.rollback()
            finally:
                await session.close()
                await engine.dispose()

        self.addAsyncCleanup(cleanup)
        return session, root

    async def test_live_root_only_mints_an_unavailable_non_authorizing_diagnostic(self) -> None:
        session, root = await self._session()
        capture = self.fixture_case._capture()
        value = subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
            config=self.config,
            session=session,
            request=self.fixture.request,
            capture=capture,
        )
        self.assertIs(
            value,
            subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                value,
                config=self.config,
                session=session,
                request=self.fixture.request,
            ),
        )
        self.assertTrue(root.is_active)
        self.assertTrue(value.live_root_transaction_observed)
        self.assertFalse(value.gen2_pending_session_provenance_available)
        self.assertFalse(value.db_causal_fence_established)
        self.assertFalse(value.checkpoint_prepare_available)
        self.assertFalse(value.checkpoint_projection_available)
        self.assertFalse(value.checkpoint_durable)
        self.assertFalse(value.phase_completion_evidenced)
        self.assertFalse(value.writer_authorized)
        self.assertFalse(value.promotion_authorized)
        self.assertFalse(value.execution_authorized)
        self.assertFalse(value.full_matrix_authorized)
        self.assertFalse(value.full_matrix_executed)
        self.assertFalse(hasattr(value, "pending"))
        self.assertFalse(hasattr(value, "session"))
        self.assertFalse(hasattr(value, "prepare"))
        self.assertFalse(hasattr(value, "project"))

    async def test_fake_foreign_nested_terminal_and_replaced_contexts_fail_closed(self) -> None:
        capture = self.fixture_case._capture()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "SESSION_INVALID",
        ):
            subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                config=self.config,
                session=_FakeSession(),
                request=self.fixture.request,
                capture=capture,
            )

        nested_session, _nested_root = await self._session()
        nested = await nested_session.begin_nested()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "NESTED_TRANSACTION_FORBIDDEN",
        ):
            subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                config=self.config,
                session=nested_session,
                request=self.fixture.request,
                capture=self.fixture_case._capture(),
            )
        await nested.rollback()

        first, first_root = await self._session()
        value = subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
            config=self.config,
            session=first,
            request=self.fixture.request,
            capture=self.fixture_case._capture(),
        )
        foreign, _foreign_root = await self._session()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "FOREIGN_SESSION",
        ):
            subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                value,
                config=self.config,
                session=foreign,
                request=self.fixture.request,
            )
        await first_root.commit()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "ROOT_TRANSACTION_TERMINAL",
        ):
            subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                value,
                config=self.config,
                session=first,
                request=self.fixture.request,
            )
        replacement = await first.begin()
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "ROOT_TRANSACTION_REPLACED",
        ):
            subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                value,
                config=self.config,
                session=first,
                request=self.fixture.request,
            )
        await replacement.rollback()

    async def test_default_off_public_tamper_and_copy_fail_closed(self) -> None:
        session, _root = await self._session()
        disabled = subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig(
            checkpoint_config=self.fixture.config,
            enabled=False,
        )
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "CONFIG_INVALID",
        ):
            subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                config=disabled,
                session=session,
                request=self.fixture.request,
                capture=self.fixture_case._capture(),
            )

        session, _root = await self._session()
        value = subject.record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
            config=self.config,
            session=session,
            request=self.fixture.request,
            capture=self.fixture_case._capture(),
        )
        object.__setattr__(value, "checkpoint_prepare_available", True)
        with self.assertRaisesRegex(
            subject.PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError,
            "TAMPERED",
        ):
            subject.require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
                value,
                config=self.config,
                session=session,
                request=self.fixture.request,
            )
        object.__setattr__(value, "checkpoint_prepare_available", False)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(value)
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.copy(value)
        with self.assertRaisesRegex(TypeError, "COPY_FORBIDDEN"):
            copy.deepcopy(value)

    def test_no_pending_adapter_or_transaction_lifecycle_surface_exists(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("PendingPhysical", source)
        self.assertNotIn("persist_bound_writer_response", source)
        self.assertNotIn("prepare_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_from_same_root_envelope", source)
        self.assertNotIn("project_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_from_same_root_envelope", source)
        tree = ast.parse(source)
        forbidden_import_roots = {
            "asyncio", "boto3", "botocore", "docker", "httpx", "os",
            "paramiko", "requests", "socket", "subprocess", "urllib",
        }
        forbidden_calls = {
            "add", "begin", "close", "commit", "connect", "execute",
            "flush", "rollback", "send",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden_import_roots)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden_import_roots)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, forbidden_calls)


if __name__ == "__main__":
    unittest.main()
