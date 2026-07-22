from __future__ import annotations

import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.measure_three_site_host_clock import ClockMeasurementError, _chrony, measure


class MeasureThreeSiteHostClockTests(unittest.TestCase):
    @staticmethod
    def _completed(stdout: str, returncode: int = 0):  # noqa: ANN205
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    def test_chrony_measurement_is_machine_derived_and_hashed(self):
        chrony = (
            "AABBCCDD,3,1784635200.0,0.000400,-0.000700,0.000800,"
            "1.0,0.0,0.1,0.000200,0.000300,64.0,Normal"
        )

        def run(arguments, **_kwargs):  # noqa: ANN001, ANN202
            if arguments[0] == "/usr/bin/timedatectl":
                return self._completed("yes\n")
            return self._completed(chrony + "\n")

        with patch("scripts.measure_three_site_host_clock.shutil.which", return_value="/usr/bin/chronyc"):
            with patch("scripts.measure_three_site_host_clock.subprocess.run", side_effect=run):
                result = measure(site="bot_fi", release_sha="a" * 40)
        self.assertEqual(result["offset_ms"], 0.8)
        self.assertEqual(result["measurement_source"], "chronyc_tracking_csv")
        self.assertEqual(
            result["measurement_raw_sha256"],
            hashlib.sha256(chrony.encode()).hexdigest(),
        )

    def test_unsynchronized_host_fails_closed(self):
        with patch(
            "scripts.measure_three_site_host_clock.subprocess.run",
            return_value=self._completed("no\n"),
        ):
            with self.assertRaisesRegex(ClockMeasurementError, "NTPSynchronized=no"):
                measure(site="webapp_ir", release_sha="a" * 40)

    def test_chrony_local_reference_mode_is_not_accepted_as_external_sync(self):
        local = (
            "7F7F0101,3,1784635200.0,0.000001,0.000001,0.000001,"
            "1.0,0.0,0.1,0.000200,0.000300,64.0,Normal"
        )
        with patch(
            "scripts.measure_three_site_host_clock.shutil.which",
            return_value="/usr/bin/chronyc",
        ):
            with patch(
                "scripts.measure_three_site_host_clock.subprocess.run",
                return_value=self._completed(local + "\n"),
            ):
                with self.assertRaisesRegex(ClockMeasurementError, "not synchronized"):
                    _chrony()

    def test_ntpq_fallback_uses_system_accuracy_bound(self):
        ntpq = (
            "associd=0 status=0615, leap=00, stratum=3, refid=192.0.2.1, "
            "rootdelay=2.0, rootdisp=1.5, offset=-0.4"
        )

        def run(arguments, **_kwargs):  # noqa: ANN001, ANN202
            if arguments[0] == "/usr/bin/timedatectl":
                return self._completed("yes\n")
            return self._completed(ntpq + "\n")

        def which(name, **_kwargs):  # noqa: ANN001, ANN202
            return None if name == "chronyc" else "/usr/bin/ntpq"

        with patch("scripts.measure_three_site_host_clock.shutil.which", side_effect=which):
            with patch("scripts.measure_three_site_host_clock.subprocess.run", side_effect=run):
                result = measure(site="webapp_fi", release_sha="a" * 40)
        self.assertEqual(result["offset_ms"], 2.9)
        self.assertEqual(result["measurement_source"], "ntpq_system_variables")


if __name__ == "__main__":
    unittest.main()
