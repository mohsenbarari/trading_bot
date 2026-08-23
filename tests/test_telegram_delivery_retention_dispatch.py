from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from core.services.telegram_delivery_retention_service import (
    TelegramDeliveryRetentionError,
    _commit_terminal_job_dependencies,
    _preflight_terminal_job_purge,
    _purge_membership_pairs,
    _purge_publisher_dispatch_command_for_job,
    _purge_terminal_batch,
)

NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class TelegramDeliveryRetentionDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_command_allows_job_purge(self):
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(None)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=False,
            ),
            "absent",
        )
        db.delete.assert_not_awaited()

    async def test_terminal_command_is_deleted_with_its_job(self):
        command = SimpleNamespace(state="acknowledged", job_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(command)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=False,
            ),
            "purged",
        )
        db.delete.assert_awaited_once_with(command)
        db.flush.assert_awaited_once()

    async def test_live_command_blocks_job_purge(self):
        for state in ("pending", "sent", "retry_due"):
            command = SimpleNamespace(state=state, job_id=9)
            db = SimpleNamespace(
                execute=AsyncMock(return_value=_Result(command)),
                delete=AsyncMock(),
                flush=AsyncMock(),
            )
            with self.subTest(state=state):
                self.assertEqual(
                    await _purge_publisher_dispatch_command_for_job(
                        db,
                        job_id=9,
                        dry_run=False,
                    ),
                    "blocked",
                )
                db.delete.assert_not_awaited()

    async def test_dry_run_does_not_delete_a_terminal_command(self):
        command = SimpleNamespace(state="failed", job_id=9)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_Result(command)),
            delete=AsyncMock(),
            flush=AsyncMock(),
        )

        self.assertEqual(
            await _purge_publisher_dispatch_command_for_job(
                db,
                job_id=9,
                dry_run=True,
            ),
            "purged",
        )
        db.delete.assert_not_awaited()

    async def test_preflight_inspects_without_mutating(self):
        command = AsyncMock(return_value="purged")
        detach = AsyncMock(return_value=True)
        with patch(
            "core.services.telegram_delivery_retention_service._purge_publisher_dispatch_command_for_job",
            new=command,
        ), patch(
            "core.services.telegram_delivery_retention_service._detach_terminal_source_bindings",
            new=detach,
        ):
            self.assertEqual(
                await _preflight_terminal_job_purge(SimpleNamespace(), job_id=11),
                "ready",
            )

        command.assert_awaited_once()
        self.assertTrue(command.await_args.kwargs["dry_run"])
        detach.assert_awaited_once()
        self.assertFalse(detach.await_args.kwargs["mutate"])

    async def test_preflight_reports_source_hold_before_any_delete(self):
        command = AsyncMock(return_value="purged")
        detach = AsyncMock(return_value=False)
        with patch(
            "core.services.telegram_delivery_retention_service._purge_publisher_dispatch_command_for_job",
            new=command,
        ), patch(
            "core.services.telegram_delivery_retention_service._detach_terminal_source_bindings",
            new=detach,
        ):
            self.assertEqual(
                await _preflight_terminal_job_purge(SimpleNamespace(), job_id=11),
                "source_blocked",
            )

        self.assertTrue(command.await_args.kwargs["dry_run"])
        self.assertFalse(detach.await_args.kwargs["mutate"])

    async def test_commit_detaches_sources_before_deleting_the_command(self):
        order: list[str] = []

        async def detach(*_args, **_kwargs):
            order.append("detach")
            return True

        async def purge(*_args, **_kwargs):
            order.append("command")
            return "purged"

        with patch(
            "core.services.telegram_delivery_retention_service._detach_terminal_source_bindings",
            new=detach,
        ), patch(
            "core.services.telegram_delivery_retention_service._purge_publisher_dispatch_command_for_job",
            new=purge,
        ):
            await _commit_terminal_job_dependencies(SimpleNamespace(), job_id=11)

        self.assertEqual(order, ["detach", "command"])

    async def test_commit_fails_closed_if_source_hold_appears_after_preflight(self):
        with patch(
            "core.services.telegram_delivery_retention_service._detach_terminal_source_bindings",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.telegram_delivery_retention_service._purge_publisher_dispatch_command_for_job",
            new=AsyncMock(return_value="purged"),
        ) as command:
            with self.assertRaisesRegex(
                TelegramDeliveryRetentionError,
                "source_hold_after_preflight",
            ):
                await _commit_terminal_job_dependencies(SimpleNamespace(), job_id=11)

        command.assert_not_awaited()

    async def test_source_hold_does_not_delete_terminal_command_or_job(self):
        job = SimpleNamespace(id=11)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult([job])),
            delete=AsyncMock(),
        )
        preflight = AsyncMock(return_value="source_blocked")
        commit = AsyncMock()
        with patch(
            "core.services.telegram_delivery_retention_service._has_pending_provider_outcome",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.telegram_delivery_retention_service._preflight_terminal_job_purge",
            new=preflight,
        ), patch(
            "core.services.telegram_delivery_retention_service._commit_terminal_job_dependencies",
            new=commit,
        ):
            report = await _purge_terminal_batch(
                db,
                cutoff=NOW,
                limit=1,
                dry_run=False,
            )

        self.assertEqual(report, (1, 0, 0, 1, 0))
        preflight.assert_awaited_once()
        commit.assert_not_awaited()
        db.delete.assert_not_awaited()

    async def test_ready_job_commits_dependencies_then_deletes_the_job(self):
        job = SimpleNamespace(id=12)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult([job])),
            delete=AsyncMock(),
        )
        commit = AsyncMock()
        with patch(
            "core.services.telegram_delivery_retention_service._has_pending_provider_outcome",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.telegram_delivery_retention_service._preflight_terminal_job_purge",
            new=AsyncMock(return_value="ready"),
        ), patch(
            "core.services.telegram_delivery_retention_service._commit_terminal_job_dependencies",
            new=commit,
        ):
            report = await _purge_terminal_batch(
                db,
                cutoff=NOW,
                limit=1,
                dry_run=False,
            )

        self.assertEqual(report, (1, 1, 0, 0, 0))
        commit.assert_awaited_once_with(db, job_id=12)
        db.delete.assert_awaited_once_with(job)

    async def test_membership_source_hold_does_not_delete_commands(self):
        saga = SimpleNamespace(ban_job_id=21, unban_job_id=22)
        jobs = [
            SimpleNamespace(
                id=21,
                state="sent",
                terminal_at=NOW.replace(year=2026, month=7),
                payload_redacted_at=NOW,
                retention_legal_hold=False,
            ),
            SimpleNamespace(
                id=22,
                state="sent",
                terminal_at=NOW.replace(year=2026, month=7),
                payload_redacted_at=NOW,
                retention_legal_hold=False,
            ),
        ]
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=(_ScalarResult([saga]), _ScalarResult(jobs))
            ),
            delete=AsyncMock(),
        )
        preflight = AsyncMock(side_effect=("ready", "source_blocked"))
        commit = AsyncMock()
        with patch(
            "core.services.telegram_delivery_retention_service._has_pending_provider_outcome",
            new=AsyncMock(return_value=False),
        ), patch(
            "core.services.telegram_delivery_retention_service._preflight_terminal_job_purge",
            new=preflight,
        ), patch(
            "core.services.telegram_delivery_retention_service._commit_terminal_job_dependencies",
            new=commit,
        ):
            report = await _purge_membership_pairs(
                db,
                cutoff=NOW,
                limit=2,
                dry_run=False,
            )

        self.assertEqual(report, (2, 0, 0, 2, 0))
        commit.assert_not_awaited()
        db.delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
