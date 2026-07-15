import os
from pathlib import Path
import signal
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import assert_no_unittest_skips as skip_gate
from scripts import run_market_postgres_gate as gate


class MarketPostgresGateRunnerTests(unittest.TestCase):
    def test_admin_url_is_local_postgres_control_database_only(self):
        accepted = gate._validate_admin_url(
            "postgresql://market:secret@127.0.0.1:55432/postgres"
        )
        self.assertEqual(accepted.database, "postgres")

        for unsafe in (
            "postgresql://market:secret@db.example/postgres",
            "postgresql://market:secret@127.0.0.1/trading_bot",
            "mysql://market:secret@127.0.0.1/postgres",
            "postgresql://market:secret@127.0.0.1/postgres?host=db.example",
            "postgresql://market:secret@127.0.0.1/postgres?hostaddr=192.0.2.10",
            "postgresql://market:secret@127.0.0.1/postgres?port=5433",
            "postgresql://market:secret@127.0.0.1/postgres?dbname=trading_bot",
            "postgresql://market:secret@127.0.0.1/postgres?service=production",
            "postgresql://market:secret@127.0.0.1/postgres?servicefile=/tmp/pg_service.conf",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(
                gate.MarketPostgresGateSafetyError
            ):
                gate._validate_admin_url(unsafe)

    def test_database_names_are_run_unique_and_guard_shaped(self):
        first = gate._database_names("run_a")
        second = gate._database_names("run_b")
        self.assertEqual(set(first), {suite[1] for suite in gate.PROOF_SUITES})
        self.assertTrue(set(first.values()).isdisjoint(second.values()))
        for database in first.values():
            self.assertRegex(database, r"^market_stage\d+_ci_run_a_test$")

    def test_checkout_requires_exact_full_head(self):
        completed = [
            SimpleNamespace(stdout=f"{gate.REPO_ROOT}\n"),
            SimpleNamespace(stdout="a" * 40 + "\n"),
            SimpleNamespace(stdout=""),
        ]
        with patch("scripts.run_market_postgres_gate.subprocess.run", side_effect=completed):
            self.assertEqual(gate._validate_checkout("a" * 40), "a" * 40)
        with self.assertRaises(gate.MarketPostgresGateSafetyError):
            gate._validate_checkout("short")

        dirty = [
            SimpleNamespace(stdout=f"{gate.REPO_ROOT}\n"),
            SimpleNamespace(stdout="a" * 40 + "\n"),
            SimpleNamespace(stdout=" M api/routers/offers.py\n"),
        ]
        with patch("scripts.run_market_postgres_gate.subprocess.run", side_effect=dirty):
            with self.assertRaisesRegex(
                gate.MarketPostgresGateSafetyError,
                "uncommitted",
            ):
                gate._validate_checkout("a" * 40)

    def test_redis_requires_local_database_14_and_reachability(self):
        client = MagicMock()
        client.ping.return_value = True
        with patch("scripts.run_market_postgres_gate.redis.Redis.from_url", return_value=client):
            self.assertEqual(
                gate._validate_redis_url("redis://127.0.0.1:56379/14"),
                ("127.0.0.1", 56379),
            )
        client.close.assert_called_once()

        for unsafe in (
            "redis://redis.example/14",
            "redis://127.0.0.1:6379/0",
            "redis://127.0.0.1:6379/14?db=0",
            "redis://127.0.0.1:6379/14?unix_socket_path=/run/redis.sock",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(
                gate.MarketPostgresGateSafetyError
            ):
                gate._validate_redis_url(unsafe)

    def test_main_cleans_partially_created_databases(self):
        created_database = "market_stage6_ci_partial_test"

        def partial_create(_admin, _databases, created, evidence):
            created.append(created_database)
            evidence.append(f"scratch.created={created_database}")
            raise RuntimeError("create failed")

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MARKET_POSTGRES_GATE_REDIS_URL": "redis://127.0.0.1:6379/14"},
            clear=False,
        ), patch.object(gate, "_validate_admin_url"), patch.object(
            gate, "_validate_checkout", return_value="a" * 40
        ), patch.object(gate, "_validate_redis_url"), patch.object(
            gate, "_create_scratch_databases", side_effect=partial_create
        ), patch.object(gate, "_drop_scratch_databases") as drop_mock:
            result = gate.main(
                [
                    "--admin-url",
                    "postgresql://market:secret@127.0.0.1/postgres",
                    "--expected-sha",
                    "a" * 40,
                    "--log-path",
                    str(Path(directory) / "gate.log"),
                ]
            )

        self.assertEqual(result, 2)
        self.assertEqual(drop_mock.call_args.args[1], [created_database])

    def test_preexisting_database_is_refused_without_cleanup_ownership(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)
        created: list[str] = []
        evidence: list[str] = []

        with patch.object(gate, "_connect", return_value=connection):
            with self.assertRaisesRegex(
                gate.MarketPostgresGateSafetyError,
                "refusing pre-existing scratch database",
            ):
                gate._create_scratch_databases(
                    "postgresql://market:secret@127.0.0.1/postgres",
                    {"STAGE": "market_stage9_ci_existing_test"},
                    created,
                    evidence,
                )

        self.assertEqual(created, [])
        self.assertEqual(evidence, [])
        self.assertEqual(cursor.execute.call_count, 1)
        connection.close.assert_called_once()

    def test_signal_interrupt_cleans_only_databases_created_by_run(self):
        created_database = "market_stage6_ci_signal_test"
        installed_handlers: dict[int, object] = {}

        def install_handler(signum, handler):
            installed_handlers[signum] = handler

        def interrupting_create(_admin, _databases, created, evidence):
            created.append(created_database)
            evidence.append(f"scratch.created={created_database}")
            installed_handlers[signal.SIGTERM](signal.SIGTERM, None)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"MARKET_POSTGRES_GATE_REDIS_URL": "redis://127.0.0.1:6379/14"},
            clear=False,
        ), patch.object(gate.signal, "getsignal", return_value=signal.SIG_DFL), patch.object(
            gate.signal, "signal", side_effect=install_handler
        ), patch.object(gate, "_validate_admin_url"), patch.object(
            gate, "_validate_checkout", return_value="a" * 40
        ), patch.object(gate, "_validate_redis_url"), patch.object(
            gate, "_create_scratch_databases", side_effect=interrupting_create
        ), patch.object(gate, "_drop_scratch_databases") as drop_mock:
            result = gate.main(
                [
                    "--admin-url",
                    "postgresql://market:secret@127.0.0.1/postgres",
                    "--expected-sha",
                    "a" * 40,
                    "--log-path",
                    str(Path(directory) / "gate.log"),
                ]
            )

        self.assertEqual(result, 128 + signal.SIGTERM)
        self.assertEqual(drop_mock.call_args.args[1], [created_database])

    def test_run_gate_pins_generic_database_per_module_and_rejects_skip(self):
        databases = gate._database_names("env")
        admin_url = "postgresql://market:secret@127.0.0.1:55432/postgres"
        completed = SimpleNamespace(
            returncode=0,
            stdout="Ran 1 test in 0.1s\nOK\n",
            stderr="",
        )
        with patch.object(
            gate,
            "_validate_redis_url",
            return_value=("127.0.0.1", 56379),
        ), patch("scripts.run_market_postgres_gate.subprocess.run", return_value=completed) as run_mock:
            result, output = gate._run_gate(
                admin_url,
                databases,
                "redis://127.0.0.1:56379/14",
            )

        self.assertEqual(result, 0)
        self.assertIn("proof.summary=modules:9,tests:9,skipped:0", output)
        self.assertEqual(run_mock.call_count, len(gate.PROOF_SUITES))
        for call, (_module, variable, _prefix) in zip(
            run_mock.call_args_list,
            gate.PROOF_SUITES,
        ):
            env = call.kwargs["env"]
            self.assertEqual(
                env["POSTGRES_DB"],
                databases[variable],
            )
            self.assertIn(databases[variable], env["DATABASE_URL"])
            self.assertIn(databases[variable], env["SYNC_DATABASE_URL"])

        skipped = SimpleNamespace(
            returncode=0,
            stdout="Ran 2 tests\nOK (skipped=1)\n",
            stderr="",
        )
        with patch.object(
            gate,
            "_validate_redis_url",
            return_value=("127.0.0.1", 56379),
        ), patch("scripts.run_market_postgres_gate.subprocess.run", return_value=skipped):
            result, _output = gate._run_gate(
                admin_url,
                databases,
                "redis://127.0.0.1:56379/14",
            )
        self.assertEqual(result, 2)

    def test_required_unittest_log_rejects_positive_skip_summary(self):
        self.assertIsNone(skip_gate.executed_test_count("OK\n"))
        self.assertEqual(skip_gate.executed_test_count("Ran 0 tests\nOK\n"), 0)
        self.assertEqual(skip_gate.executed_test_count("Ran 2 tests\nOK\n"), 2)
        self.assertIsNone(
            skip_gate.executed_test_count("Ran 0 tests\nOK\nRan 2 tests\nOK\n")
        )
        self.assertEqual(skip_gate.skipped_test_count("Ran 2 tests\nOK\n"), 0)
        self.assertEqual(
            skip_gate.skipped_test_count("Ran 2 tests\nOK (skipped=1)\n"),
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "unittest.log"
            for output in (
                "OK\n",
                "Ran 0 tests\nOK\n",
                "Ran 2 tests\nOK (skipped=1)\n",
                "Ran 0 tests\nOK\nRan 2 tests\nOK\n",
            ):
                with self.subTest(output=output):
                    log_path.write_text(output, encoding="utf-8")
                    self.assertEqual(skip_gate.main([str(log_path)]), 2)
            log_path.write_text("Ran 2 tests\nOK\n", encoding="utf-8")
            self.assertEqual(skip_gate.main([str(log_path)]), 0)

    def test_run_gate_rejects_missing_or_zero_test_summary(self):
        databases = gate._database_names("zero")
        admin_url = "postgresql://market:secret@127.0.0.1:55432/postgres"
        for output in ("OK\n", "Ran 0 tests\nOK\n"):
            completed = SimpleNamespace(returncode=0, stdout=output, stderr="")
            with self.subTest(output=output), patch.object(
                gate,
                "_validate_redis_url",
                return_value=("127.0.0.1", 56379),
            ), patch(
                "scripts.run_market_postgres_gate.subprocess.run",
                return_value=completed,
            ):
                result, proof = gate._run_gate(
                    admin_url,
                    databases,
                    "redis://127.0.0.1:56379/14",
                )

            self.assertEqual(result, 2)
            self.assertIn(
                "proof.test_count_rejected=tests.test_market_offer_admission_postgres",
                proof,
            )


if __name__ == "__main__":
    unittest.main()
