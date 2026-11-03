"""Tests for canonical PYTHONPATH and explicit tmux env propagation."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "scripts" / "two_site_autopilot_tmux.sh"
MONITOR_PATH = REPO_ROOT / "scripts" / "two_site_autopilot_monitor.sh"


class TestOutsideScriptModeImport(unittest.TestCase):
    def test_outside_probe_positive_and_negative(self) -> None:
        """Probe actual startup module from outside repo with positive and negative PYTHONPATH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            probe_dir = Path(tmpdir).resolve()
            probe_file = probe_dir / "probe.py"
            probe_file.write_text(
                "import scripts.two_site_maintenance_transition_startup\n"
                "print('PROBE_STARTUP_IMPORT_OK')\n",
                encoding="utf-8",
            )

            pos_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "APP_ENV_FILE": "config/unit-test.env.example",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(REPO_ROOT),
            }
            pos_proc = subprocess.run(
                [sys.executable, str(probe_file)],
                cwd=str(probe_dir),
                env=pos_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                pos_proc.returncode,
                0,
                f"Positive probe import failed: stdout={pos_proc.stdout} stderr={pos_proc.stderr}",
            )
            self.assertIn("PROBE_STARTUP_IMPORT_OK", pos_proc.stdout)

            neg_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "APP_ENV_FILE": "config/unit-test.env.example",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(probe_dir),
            }
            neg_proc = subprocess.run(
                [sys.executable, str(probe_file)],
                cwd=str(probe_dir),
                env=neg_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(neg_proc.returncode, 0, "Negative probe import should fail")
            self.assertNotIn("PROBE_STARTUP_IMPORT_OK", neg_proc.stdout)


def _setup_isolated_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "-b", "refactor/two-site-architecture"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "two_site_provider_broker.py").touch()
    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "checkout", "-B", "refactor/two-site-architecture"],
        check=True,
        capture_output=True,
    )


class TestLauncherDirectAndGeneratedEnv(unittest.TestCase):
    def test_launcher_canonical_env_and_guards(self) -> None:
        """Launcher propagates canonical PYTHONPATH to direct children and tmux commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            repo_dir = tmp_path / "repo with spaces"
            repo_dir.mkdir(parents=True, exist_ok=True)
            _setup_isolated_git_repo(repo_dir)

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            py_log = log_dir / "py_calls.jsonl"
            tmux_log = log_dir / "tmux_calls.jsonl"

            python_stub = bin_dir / "python3"
            py_code = f"""#!{sys.executable}
import json, os, sys

record = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
    "APP_ENV_FILE": os.environ.get("APP_ENV_FILE"),
    "PYTHONDONTWRITEBYTECODE": os.environ.get("PYTHONDONTWRITEBYTECODE"),
    "TWO_SITE_QODER_FINAL_REVIEW_REQUIRED": os.environ.get("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED"),
}}
with open({json.dumps(str(py_log))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")

if "--check" in sys.argv:
    flag = os.environ.get("STUB_CHECK_FAIL")
    if flag == "1":
        sys.exit(1)
    sys.exit(0)
if "status" in sys.argv:
    print(json.dumps({{"qoder_final_review_required": os.environ.get("STUB_QODER_REVIEW_READY", "1") == "1",
                      "quota_continuation_ready": True}}))
    sys.exit(0)
if "-c" in sys.argv:
    exec(sys.argv[sys.argv.index("-c") + 1])
sys.exit(0)
"""
            python_stub.write_text(py_code, encoding="utf-8")
            python_stub.chmod(0o755)

            tmux_stub = bin_dir / "tmux"
            tmux_code = f"""#!{sys.executable}
import json, os, sys

record = {{
    "argv": sys.argv[1:],
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
}}
with open({json.dumps(str(tmux_log))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")

if "has-session" in sys.argv:
    if "=two-site-provider-broker" in sys.argv and os.environ.get("STUB_BROKER_PRESENT") == "1":
        sys.exit(0)
    sys.exit(1)
if "new-session" in sys.argv:
    sys.exit(0)
sys.exit(0)
"""
            tmux_stub.write_text(tmux_code, encoding="utf-8")
            tmux_stub.chmod(0o755)

            launcher_src = LAUNCHER_PATH.read_text(encoding="utf-8")
            modified_launcher = launcher_src.replace("/usr/bin/python3", str(python_stub))
            launcher_dest = repo_dir / "scripts" / "two_site_autopilot_tmux.sh"
            launcher_dest.write_text(modified_launcher, encoding="utf-8")
            launcher_dest.chmod(0o755)

            env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "PYTHONPATH": "/hostile/parent/env",
            }

            res = subprocess.run(
                ["bash", str(launcher_dest), "start"],
                cwd=str(repo_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res.returncode, 0, f"start failed: {res.stderr}\\nout: {res.stdout}")

            py_records = [
                json.loads(line)
                for line in py_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            check_calls = [r for r in py_records if "--check" in r["argv"]]
            self.assertTrue(check_calls, "Expected --check direct invocation")
            self.assertEqual(check_calls[0]["PYTHONPATH"], str(repo_dir))
            self.assertEqual(check_calls[0]["APP_ENV_FILE"], "config/unit-test.env.example")
            self.assertEqual(check_calls[0]["PYTHONDONTWRITEBYTECODE"], "1")

            status_calls = [r for r in py_records if "status" in r["argv"]]
            self.assertTrue(status_calls, "Expected status direct invocation")
            self.assertEqual(status_calls[0]["PYTHONPATH"], str(repo_dir))

            tmux_records = [
                json.loads(line)
                for line in tmux_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            new_sessions = [r for r in tmux_records if "new-session" in r["argv"]]
            self.assertEqual(len(new_sessions), 2, "Expected 2 tmux new-session calls")

            broker_session = [r for r in new_sessions if "two-site-provider-broker" in r["argv"]][0]
            sup_session = [r for r in new_sessions if "two-site-autopilot" in r["argv"]][0]

            broker_cmd = broker_session["argv"][-1]
            sup_cmd = sup_session["argv"][-1]

            self.assertIn("PYTHONPATH=", broker_cmd)
            self.assertIn("PYTHONPATH=", sup_cmd)
            self.assertIn("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED=1", broker_cmd)
            self.assertIn("scripts/two_site_autopilot.py", sup_cmd)
            self.assertNotIn("two_site_codex_autopilot.py", sup_cmd)
            escaped_repo = str(repo_dir).replace(" ", r"\ ")
            self.assertTrue(
                escaped_repo in broker_cmd or str(repo_dir) in broker_cmd,
                f"Expected canonical path in broker command: {broker_cmd}",
            )
            self.assertTrue(
                escaped_repo in sup_cmd or str(repo_dir) in sup_cmd,
                f"Expected canonical path in supervisor command: {sup_cmd}",
            )

            eval_env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
            py_log.unlink()

            sub_b = subprocess.run(["bash", "-c", broker_cmd], env=eval_env, capture_output=True, timeout=30)
            self.assertEqual(sub_b.returncode, 0)
            sub_s = subprocess.run(["bash", "-c", sup_cmd], env=eval_env, capture_output=True, timeout=30)
            self.assertEqual(sub_s.returncode, 0)

            eval_records = [
                json.loads(line)
                for line in py_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(eval_records), 2)
            self.assertEqual(eval_records[0]["PYTHONPATH"], str(repo_dir))
            self.assertEqual(eval_records[1]["PYTHONPATH"], str(repo_dir))
            self.assertEqual(eval_records[0]["TWO_SITE_QODER_FINAL_REVIEW_REQUIRED"], "1")

            # An already-running older Broker cannot be silently reused or
            # replaced merely because its heartbeat is otherwise healthy.
            before_count = len(new_sessions)
            obsolete = subprocess.run(
                ["bash", str(launcher_dest), "start"], cwd=repo_dir,
                env=dict(env, STUB_BROKER_PRESENT="1", STUB_QODER_REVIEW_READY="0"),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(obsolete.returncode, 2)
            self.assertIn("existing Broker lacks required Qoder final review", obsolete.stderr)
            after_records = [json.loads(line) for line in tmux_log.read_text().splitlines()]
            self.assertEqual(sum("new-session" in row["argv"] for row in after_records), before_count)

            # Unset inherited-path case for direct launcher
            py_log.unlink()
            env_unset = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            }
            res_unset = subprocess.run(
                ["bash", str(launcher_dest), "status"],
                cwd=str(repo_dir),
                env=env_unset,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res_unset.returncode, 0)
            unset_records = [
                json.loads(line)
                for line in py_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(unset_records, "Expected status direct invocation with unset PYTHONPATH")
            self.assertEqual(unset_records[0]["PYTHONPATH"], str(repo_dir))

    def test_launcher_stop_and_check_prevention(self) -> None:
        """Launcher halts on failed --check and regular/broken STOP files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            _setup_isolated_git_repo(repo_dir)

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            python_stub = bin_dir / "python3"
            py_code = f"""#!{sys.executable}
import os, sys
if "--check" in sys.argv and os.environ.get("STUB_CHECK_FAIL") == "1":
    sys.exit(2)
sys.exit(0)
"""
            python_stub.write_text(py_code, encoding="utf-8")
            python_stub.chmod(0o755)

            tmux_stub = bin_dir / "tmux"
            tmux_stub.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n", encoding="utf-8")
            tmux_stub.chmod(0o755)

            launcher_src = LAUNCHER_PATH.read_text(encoding="utf-8")
            modified_launcher = launcher_src.replace("/usr/bin/python3", str(python_stub))
            launcher_dest = repo_dir / "scripts" / "two_site_autopilot_tmux.sh"
            launcher_dest.write_text(modified_launcher, encoding="utf-8")
            launcher_dest.chmod(0o755)

            env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}

            fail_env = dict(env, STUB_CHECK_FAIL="1")
            res_fail = subprocess.run(
                ["bash", str(launcher_dest), "start"],
                cwd=str(repo_dir),
                env=fail_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(res_fail.returncode, 0)

            state_root = repo_dir / ".local" / "two-site-refactor" / "autopilot"
            state_root.mkdir(parents=True, exist_ok=True)
            stop_file = state_root / "STOP"
            stop_file.write_text("stop\n", encoding="utf-8")
            res_stop = subprocess.run(
                ["bash", str(launcher_dest), "start"],
                cwd=str(repo_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res_stop.returncode, 2)
            self.assertIn("STOP file present", res_stop.stderr)
            stop_file.unlink()

            stop_file.symlink_to(state_root / "nonexistent")
            res_sym = subprocess.run(
                ["bash", str(launcher_dest), "start"],
                cwd=str(repo_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res_sym.returncode, 2)
            self.assertIn("STOP file present", res_sym.stderr)


class TestMonitorRestartRoutesCanonicalEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(MONITOR_PATH.is_file(), f"Missing monitor: {MONITOR_PATH}")
        self.monitor_src = MONITOR_PATH.read_text(encoding="utf-8")

        m_exp = re.search(
            r'(cd "\$integration_root"[^\n]*\n.*?)(?=\nmkdir -p)',
            self.monitor_src,
            re.DOTALL,
        )
        self.assertIsNotNone(m_exp, "Could not extract export fragment from monitor")
        self.export_fragment = m_exp.group(1)

        monitor_lines = self.monitor_src.splitlines()

        preflight_lines = [
            line.strip()
            for line in monitor_lines
            if line.strip().startswith("(ulimit -f 1024;")
            and "two_site_autopilot_monitor_preflight.py" in line
        ]
        self.assertEqual(
            len(preflight_lines),
            1,
            f"Expected exactly 1 preflight line, found {len(preflight_lines)}",
        )
        self.preflight_cmd = preflight_lines[0]

        broker_preflight_lines = [
            line.strip()
            for line in monitor_lines
            if line.strip().startswith("(ulimit -f 1024;")
            and "two_site_autopilot_monitor_broker_preflight.py" in line
        ]
        self.assertEqual(
            len(broker_preflight_lines),
            1,
            f"Expected exactly 1 broker-preflight line, found {len(broker_preflight_lines)}",
        )
        self.broker_preflight_cmd = broker_preflight_lines[0]

        m_rst = re.search(
            r'(if \[\[ "\$recovery_reason" == "provider_broker_missing" \]\]; then.*?)(?=\n\s*if \[\[ -f "\$restart_tmp" \]\]; then)',
            self.monitor_src,
            re.DOTALL,
        )
        self.assertIsNotNone(m_rst, "Could not extract guarded restart branch")
        self.restart_branch = m_rst.group(1)

    def _substitute_boundaries(self, text: str, bin_dir: Path) -> str:
        replacements = {
            "/usr/bin/python3": str(bin_dir / "python3"),
            "/usr/bin/tmux": str(bin_dir / "tmux"),
            "/usr/bin/pgrep": str(bin_dir / "pgrep"),
            "/usr/bin/sleep": "/bin/true",
            "/usr/bin/timeout 20 ": "",
            "/usr/bin/timeout 15 ": "",
            "/usr/bin/timeout 5 ": "",
            "/usr/bin/timeout 45 ": "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def test_monitor_preflight_and_routes_canonical_env(self) -> None:
        """Extracted monitor preflights and restart routes propagate canonical environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir).resolve()
            integration_root = tmp_path / "integration root with spaces"
            integration_root.mkdir(parents=True, exist_ok=True)

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            log_dir = tmp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            py_log = log_dir / "py.jsonl"
            tmux_log = log_dir / "tmux.jsonl"
            sup_log = log_dir / "sup.jsonl"

            py_code = f"""#!{sys.executable}
import json, os, sys
record = {{
    "argv": sys.argv[1:],
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
}}
with open({json.dumps(str(py_log))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")
if any("preflight" in a for a in sys.argv):
    sys.stdout.write("READY\\n")
    sys.exit(0)
if "status" in sys.argv:
    print(json.dumps({{"qoder_final_review_required": True, "quota_continuation_ready": True}}))
    sys.exit(0)
if "-c" in sys.argv:
    exec(sys.argv[sys.argv.index("-c") + 1])
sys.exit(0)
"""
            (bin_dir / "python3").write_text(py_code, encoding="utf-8")
            (bin_dir / "python3").chmod(0o755)

            pgrep_code = f"""#!{sys.executable}
import sys
cmd = " ".join(sys.argv)
if "autopilot.py" in cmd:
    sys.stdout.write("1\\n")
    sys.exit(0)
if "two_site_provider_broker.py" in cmd:
    sys.stdout.write("0\\n")
    sys.exit(0)
sys.stdout.write("0\\n")
sys.exit(0)
"""
            (bin_dir / "pgrep").write_text(pgrep_code, encoding="utf-8")
            (bin_dir / "pgrep").chmod(0o755)

            tmux_code = f"""#!{sys.executable}
import json, os, sys
record = {{
    "argv": sys.argv[1:],
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
}}
with open({json.dumps(str(tmux_log))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")
if "has-session" in sys.argv:
    if "=two-site-autopilot" in sys.argv:
        sys.exit(0)
    sys.exit(1)
if "new-session" in sys.argv:
    sys.exit(0)
sys.exit(0)
"""
            (bin_dir / "tmux").write_text(tmux_code, encoding="utf-8")
            (bin_dir / "tmux").chmod(0o755)

            scripts_dir = integration_root / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            sup_stub = scripts_dir / "two_site_autopilot_tmux.sh"
            sup_code = f"""#!{sys.executable}
import json, os, sys
record = {{
    "argv": sys.argv[1:],
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
}}
with open({json.dumps(str(sup_log))}, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")
sys.exit(0)
"""
            sup_stub.write_text(sup_code, encoding="utf-8")
            sup_stub.chmod(0o755)

            state_root = integration_root / ".local" / "two-site-refactor" / "autopilot"
            report_root = integration_root / ".local" / "two-site-refactor" / "monitor"
            state_root.mkdir(parents=True, exist_ok=True)
            report_root.mkdir(parents=True, exist_ok=True)

            sub_exp = self._substitute_boundaries(self.export_fragment, bin_dir)
            sub_pre = self._substitute_boundaries(self.preflight_cmd, bin_dir)
            sub_bpre = self._substitute_boundaries(self.broker_preflight_cmd, bin_dir)
            sub_rst = self._substitute_boundaries(self.restart_branch, bin_dir)

            base_env = {
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "PYTHONPATH": "/hostile/parent/env",
            }

            preflight_script = f"""set -euo pipefail
integration_root={shlex.quote(str(integration_root))}
preflight_stdout_tmp="{report_root}/pre.stdout"
preflight_stderr_tmp="{report_root}/pre.stderr"
{sub_exp}
{sub_pre}
"""
            res_pre = subprocess.run(["bash", "-c", preflight_script], env=base_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(res_pre.returncode, 0, f"Preflight failed: {res_pre.stderr}")

            bpreflight_script = f"""set -euo pipefail
integration_root={shlex.quote(str(integration_root))}
broker_stdout_tmp="{report_root}/bpre.stdout"
broker_stderr_tmp="{report_root}/bpre.stderr"
{sub_exp}
{sub_bpre}
"""
            res_bpre = subprocess.run(["bash", "-c", bpreflight_script], env=base_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(res_bpre.returncode, 0, f"Broker preflight failed: {res_bpre.stderr}")

            py_records = [
                json.loads(line)
                for line in py_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(py_records), 2)
            self.assertEqual(py_records[0]["PYTHONPATH"], str(integration_root))
            self.assertEqual(py_records[1]["PYTHONPATH"], str(integration_root))

            broker_rst_script = f"""set -euo pipefail
integration_root={shlex.quote(str(integration_root))}
state_root="$integration_root/.local/two-site-refactor/autopilot"
report_root="$integration_root/.local/two-site-refactor/monitor"
session="two-site-autopilot"
broker_session="two-site-provider-broker"
recovery_reason="provider_broker_missing"
restart_tmp="$report_root/restart.log"
{sub_exp}
{sub_rst}
"""
            res_brst = subprocess.run(["bash", "-c", broker_rst_script], env=base_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(res_brst.returncode, 0, f"Broker restart route failed: {res_brst.stderr}")

            tmux_records = [
                json.loads(line)
                for line in tmux_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            new_sessions = [r for r in tmux_records if "new-session" in r["argv"]]
            self.assertEqual(len(new_sessions), 1)
            broker_cmd = new_sessions[0]["argv"][-1]
            self.assertIn("PYTHONPATH=", broker_cmd)
            self.assertIn("TWO_SITE_QODER_FINAL_REVIEW_REQUIRED=1", broker_cmd)
            escaped_int_root = str(integration_root).replace(" ", r"\ ")
            self.assertTrue(
                escaped_int_root in broker_cmd or str(integration_root) in broker_cmd,
                f"Expected integration_root in broker_cmd: {broker_cmd}",
            )

            py_log.unlink()
            eval_env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
            res_eval = subprocess.run(["bash", "-c", broker_cmd], env=eval_env, capture_output=True, timeout=30)
            self.assertEqual(res_eval.returncode, 0)
            eval_record = json.loads(py_log.read_text(encoding="utf-8").strip())
            self.assertEqual(eval_record["PYTHONPATH"], str(integration_root))

            sup_rst_script = f"""set -euo pipefail
integration_root={shlex.quote(str(integration_root))}
state_root="$integration_root/.local/two-site-refactor/autopilot"
report_root="$integration_root/.local/two-site-refactor/monitor"
session="two-site-autopilot"
broker_session="two-site-provider-broker"
recovery_reason="clean_blocked_state"
restart_tmp="$report_root/restart.log"
{sub_exp}
{sub_rst}
"""
            res_srst = subprocess.run(["bash", "-c", sup_rst_script], env=base_env, capture_output=True, text=True, timeout=30)
            self.assertEqual(res_srst.returncode, 0, f"Supervisor restart route failed: {res_srst.stderr}")

            sup_records = [
                json.loads(line)
                for line in sup_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(sup_records), 1)
            self.assertEqual(sup_records[0]["PYTHONPATH"], str(integration_root))

            # STOP file test: must not start new sessions or services
            stop_file = state_root / "STOP"
            stop_file.write_text("stop\n", encoding="utf-8")
            tmux_records_before = [
                json.loads(line)
                for line in tmux_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            new_sessions_before = [r for r in tmux_records_before if "new-session" in r["argv"]]
            sup_count_before = len([
                line for line in sup_log.read_text(encoding="utf-8").splitlines() if line.strip()
            ])

            res_stop = subprocess.run(
                ["bash", "-c", broker_rst_script + "\necho ACTION=$action"],
                env=base_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res_stop.returncode, 0)
            self.assertIn("ACTION=broker_recheck_failed", res_stop.stdout)

            tmux_records_after = [
                json.loads(line)
                for line in tmux_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            new_sessions_after = [r for r in tmux_records_after if "new-session" in r["argv"]]
            self.assertEqual(
                len(new_sessions_before),
                len(new_sessions_after),
                "tmux new-session must not be called when STOP is present",
            )
            has_sessions_after = [r for r in tmux_records_after if "has-session" in r["argv"]]
            self.assertGreater(
                len(has_sessions_after),
                len([r for r in tmux_records_before if "has-session" in r["argv"]]),
                "Observational tmux has-session queries are permitted before STOP check",
            )
            sup_count_after = len([
                line for line in sup_log.read_text(encoding="utf-8").splitlines() if line.strip()
            ])
            self.assertEqual(
                sup_count_before,
                sup_count_after,
                "Supervisor must not be started when STOP is present",
            )
            stop_file.unlink()

            # Dangling symlink STOP test
            stop_file.symlink_to(state_root / "nonexistent")
            res_dangling = subprocess.run(
                ["bash", "-c", broker_rst_script + "\necho ACTION=$action"],
                env=base_env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(res_dangling.returncode, 0)
            self.assertIn("ACTION=broker_recheck_failed", res_dangling.stdout)

            tmux_records_dangling = [
                json.loads(line)
                for line in tmux_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            new_sessions_dangling = [r for r in tmux_records_dangling if "new-session" in r["argv"]]
            self.assertEqual(
                len(new_sessions_before),
                len(new_sessions_dangling),
                "tmux new-session must not be called on dangling STOP symlink",
            )
            sup_count_dangling = len([
                line for line in sup_log.read_text(encoding="utf-8").splitlines() if line.strip()
            ])
            self.assertEqual(
                sup_count_before,
                sup_count_dangling,
                "Supervisor must not be started on dangling STOP symlink",
            )
            stop_file.unlink()


if __name__ == "__main__":
    unittest.main()
