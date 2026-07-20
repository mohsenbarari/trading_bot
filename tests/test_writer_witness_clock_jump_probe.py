import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "scripts/run_writer_witness_clock_jump_probe.py"
spec = importlib.util.spec_from_file_location("writer_witness_real_clock_probe_test", PROBE_PATH)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class WriterWitnessRealClockProbeTests(unittest.TestCase):
    def test_database_url_is_unix_socket_only(self):
        url = probe._database_url(Path("/run/wwm_0123456789ab-clock/socket"))
        rendered = url.render_as_string(hide_password=False)
        self.assertIn("host=%2Frun%2Fwwm_0123456789ab-clock%2Fsocket", rendered)
        self.assertNotIn("127.0.0.1", rendered)
        self.assertNotIn("localhost", rendered)

    def test_clock_control_update_is_atomic_owner_only_and_bounded(self):
        with tempfile.TemporaryDirectory(prefix="clock-control-") as directory:
            root = Path(directory)
            control = root / "faketime.rc"
            control.write_text("+0\n", encoding="ascii")
            control.chmod(0o600)
            probe._write_clock_control(control, "-60s")
            self.assertEqual(control.read_text(encoding="ascii"), "-60s\n")
            self.assertEqual(control.stat().st_mode & 0o777, 0o600)
            self.assertEqual(tuple(root.glob(".faketime.rc.*.tmp")), ())
            with self.assertRaisesRegex(probe.ClockProbeError, "unsupported"):
                probe._write_clock_control(control, "+1year")

    def test_control_file_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory(prefix="clock-control-") as directory:
            root = Path(directory)
            control = root / "faketime.rc"
            control.write_text("+0\n", encoding="ascii")
            control.chmod(0o600)
            link = root / "link.rc"
            link.symlink_to(control)
            with self.assertRaises(probe.ClockProbeError):
                probe._validate_control_file(link, expected_root=root)
            hardlink = root / "hard.rc"
            os.link(control, hardlink)
            with self.assertRaisesRegex(probe.ClockProbeError, "one owner-only"):
                probe._validate_control_file(control, expected_root=root)

    def test_probe_source_never_injects_a_synthetic_transition_time(self):
        source = PROBE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("now" + "=", source)
        self.assertIn('"production_clock_path": "SELECT clock_timestamp()"', source)
        self.assertIn('"synthetic_time_argument_used": False', source)
        for forbidden in (
            "date -s",
            "timedatectl set-",
            "hwclock",
            "chronyc makestep",
            "CAP_SYS_TIME",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
