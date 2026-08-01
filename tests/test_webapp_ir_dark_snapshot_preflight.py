from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

import core.webapp_ir_dark_snapshot_preflight as preflight


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def observation(**overrides: object) -> preflight.WebappIrDarkSnapshotPreflightObservation:
    values: dict[str, object] = {
        "services": {"snapshot_db": {"state": "running", "health": "healthy"}},
        "network_mode": "none",
        "published_ports": (),
        "promotion_state": "inactive",
        "promotion_unit_state": "masked",
        "refresh_timer_enabled": True,
        "refresh_timer_state": "active",
        "observed_at": NOW,
    }
    values.update(overrides)
    return preflight.WebappIrDarkSnapshotPreflightObservation(**values)  # type: ignore[arg-type]


class WebappIrDarkSnapshotPreflightTests(unittest.TestCase):
    def test_only_healthy_running_snapshot_db_produces_non_authorizing_result(self) -> None:
        result = preflight.verify_webapp_ir_dark_snapshot_preflight(observation(), now=NOW)

        self.assertEqual(
            {"promotion": False, "writer": False, "execution": False}, dict(result.output)
        )
        self.assertEqual("snapshot_db", next(iter(result.observation.services)))

    def test_exact_mapping_is_accepted_and_unknown_shape_fails_closed(self) -> None:
        raw = {
            "services": {"snapshot_db": {"state": "running", "health": "healthy"}},
            "network_mode": "none",
            "published_ports": (),
            "promotion_state": "inactive",
            "promotion_unit_state": "masked",
            "refresh_timer_enabled": True,
            "refresh_timer_state": "active",
            "observed_at": NOW,
        }
        result = preflight.verify_webapp_ir_dark_snapshot_preflight(raw, now=NOW)
        self.assertFalse(result.execution)
        raw["unexpected"] = True
        with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
            preflight.verify_webapp_ir_dark_snapshot_preflight(raw, now=NOW)

    def test_scalar_observation_fields_require_exact_builtin_types(self) -> None:
        class EqualToEverything:
            def __eq__(self, other: object) -> bool:
                return True

            def __ne__(self, other: object) -> bool:
                return False

        with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
            preflight.verify_webapp_ir_dark_snapshot_preflight(
                observation(network_mode=EqualToEverything()), now=NOW
            )

    def test_services_are_exactly_snapshot_db_and_exclude_all_forbidden_roles(self) -> None:
        for forbidden in ("app", "bot", "redis", "sync_worker", "migration"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
                    preflight.verify_webapp_ir_dark_snapshot_preflight(
                        observation(
                            services={
                                "snapshot_db": {"state": "running", "health": "healthy"},
                                forbidden: {"state": "running", "health": "healthy"},
                            }
                        ),
                        now=NOW,
                    )
        with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
            preflight.verify_webapp_ir_dark_snapshot_preflight(
                observation(services={}), now=NOW
            )

    def test_network_ports_db_promotion_and_timer_are_all_hard_requirements(self) -> None:
        invalid = (
            {"network_mode": "bridge"},
            {"published_ports": ("5432/tcp",)},
            {"services": {"snapshot_db": {"state": "exited", "health": "healthy"}}},
            {"services": {"snapshot_db": {"state": "running", "health": "unhealthy"}}},
            {"promotion_state": "active"},
            {"promotion_unit_state": "disabled"},
            {"refresh_timer_enabled": False},
            {"refresh_timer_state": "inactive"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
                    preflight.verify_webapp_ir_dark_snapshot_preflight(
                        observation(**changes), now=NOW
                    )

    def test_observation_must_be_fresh_and_not_from_the_future(self) -> None:
        for observed_at in (
            NOW - timedelta(seconds=31),
            NOW + timedelta(microseconds=1),
        ):
            with self.subTest(observed_at=observed_at):
                with self.assertRaises(preflight.WebappIrDarkSnapshotPreflightError):
                    preflight.verify_webapp_ir_dark_snapshot_preflight(
                        replace(observation(), observed_at=observed_at), now=NOW
                    )
        preflight.verify_webapp_ir_dark_snapshot_preflight(
            replace(observation(), observed_at=NOW - timedelta(seconds=30)), now=NOW
        )


if __name__ == "__main__":
    unittest.main()
