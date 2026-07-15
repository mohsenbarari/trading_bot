import os
from pathlib import Path
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
        self.assertEqual(skip_gate.skipped_test_count("Ran 2 tests\nOK\n"), 0)
        self.assertEqual(
            skip_gate.skipped_test_count("Ran 2 tests\nOK (skipped=1)\n"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
