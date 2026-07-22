from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.verify_three_site_queue_activation_transition import (
    QueueActivationTransitionError,
    TARGET,
    verify_transition,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(
        repo,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid",
        "commit", "-m", message,
    )
    return _git(repo, "rev-parse", "HEAD")


class ThreeSiteQueueActivationTransitionTests(unittest.TestCase):
    def test_exact_single_gate_commit_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init")
            target = repo / TARGET
            target.parent.mkdir(parents=True)
            target.write_text(
                "before = 1\nTELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = False\nafter = 2\n"
            )
            baseline = _commit(repo, "baseline")
            target.write_text(target.read_text().replace("False", "True"))
            activation = _commit(repo, "activate queue")
            result = verify_transition(
                repo,
                baseline_sha=baseline,
                activation_sha=activation,
                require_checkout=True,
            )
            self.assertEqual(result["activation_sha"], activation)

    def test_any_second_change_or_non_direct_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init")
            target = repo / TARGET
            target.parent.mkdir(parents=True)
            target.write_text("TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = False\n")
            baseline = _commit(repo, "baseline")
            target.write_text("TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY = True\n")
            (repo / "extra.txt").write_text("not permitted\n")
            activation = _commit(repo, "unsafe activation")
            with self.assertRaisesRegex(QueueActivationTransitionError, "outside"):
                verify_transition(
                    repo,
                    baseline_sha=baseline,
                    activation_sha=activation,
                    require_checkout=True,
                )


if __name__ == "__main__":
    unittest.main()
