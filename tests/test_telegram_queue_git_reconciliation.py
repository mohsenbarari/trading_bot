from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.report_telegram_queue_git_reconciliation import build_report


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


class TelegramQueueGitReconciliationTests(unittest.TestCase):
    def test_maps_patch_equivalent_commits_with_different_shas(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init", "-q")
            _git(repo, "config", "user.name", "Queue Test")
            _git(repo, "config", "user.email", "queue-test@example.invalid")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            _git(repo, "add", "base.txt")
            _git(repo, "commit", "-qm", "base")
            base = _git(repo, "rev-parse", "HEAD")

            _git(repo, "switch", "-qc", "main-test")
            (repo / "feature.txt").write_text("same patch\n", encoding="utf-8")
            _git(repo, "add", "feature.txt")
            _git(repo, "commit", "-qm", "main wording")
            main_sha = _git(repo, "rev-parse", "HEAD")

            _git(repo, "switch", "-qc", "candidate-test", base)
            (repo / "feature.txt").write_text("same patch\n", encoding="utf-8")
            _git(repo, "add", "feature.txt")
            _git(repo, "commit", "-qm", "candidate wording")
            candidate_sha = _git(repo, "rev-parse", "HEAD")

            report = build_report(
                repo,
                candidate_ref=candidate_sha,
                main_ref=main_sha,
            )

        self.assertEqual(report["status"], "clean")
        self.assertTrue(report["all_main_patches_present_in_candidate"])
        self.assertEqual(report["main_commit_count"], 1)
        self.assertEqual(
            report["mappings"][0]["candidate_counterparts"],
            [candidate_sha],
        )
        self.assertTrue(report["git_cherry_raw"][0].startswith("- "))


if __name__ == "__main__":
    unittest.main()
