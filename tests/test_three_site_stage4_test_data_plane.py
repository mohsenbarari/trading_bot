import copy
import unittest
from unittest.mock import patch

from scripts import ensure_three_site_stage4_test_data_plane as subject


class Stage4TestDataPlaneTests(unittest.TestCase):
    def setUp(self):
        self.groups = {
            region: {
                "id": expected["id"],
                "real_name": expected["real_name"],
                "rules": [],
            }
            for region, expected in subject.GROUPS.items()
        }
        self.posts = []

    def _server(self, role):
        expected = subject.SERVERS[role]
        return {
            "id": expected["server_id"],
            "name": expected["name"],
            "status": "ACTIVE",
            "public_ip": expected["public_ip"],
            "security_groups": [{"id": expected["security_group_id"]}],
        }

    def _api_request(self, method, path, _token, payload=None):
        if method == "GET":
            role = next(
                role
                for role, expected in subject.SERVERS.items()
                if expected["server_id"] in path
            )
            return self._server(role)
        self.assertEqual(method, "POST")
        self.posts.append((path, payload))
        region = next(region for region in subject.GROUPS if f"/{region}/" in path)
        self.groups[region]["rules"].append(
            {
                "description": payload["description"],
                "direction": payload["direction"],
                "protocol": payload["protocol"],
                "port_start": int(payload["port_from"]),
                "port_end": int(payload["port_to"]),
                "ip": payload["ips"][0],
                "ether_type": "IPv4",
            }
        )
        return {}

    def _list_data(self, _token, path, _label):
        region = next(region for region in subject.GROUPS if f"/{region}/" in path)
        return [copy.deepcopy(self.groups[region])]

    def _execute(self, *, apply=False, confirm=None):
        with (
            patch.object(subject, "api_request", side_effect=self._api_request),
            patch.object(subject, "list_data", side_effect=self._list_data),
            patch.object(subject, "response_data", side_effect=lambda value, _label: value),
            patch.object(subject, "server_public_ipv4", side_effect=lambda value: value["public_ip"]),
        ):
            return subject.execute("not-logged", apply=apply, confirm=confirm)

    def test_plan_reports_only_seven_pinned_rules(self):
        result = self._execute()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["rule_count"], 7)
        self.assertEqual(result["missing_rule_count"], 7)
        self.assertFalse(result["server_or_volume_lifecycle_operation"])
        self.assertFalse(result["production_overlap"])
        self.assertEqual(self.posts, [])

    def test_apply_requires_exact_confirmation(self):
        with self.assertRaisesRegex(subject.Stage4DataPlaneError, "confirmation mismatch"):
            self._execute(apply=True, confirm="wrong")
        self.assertEqual(self.posts, [])

    def test_apply_adds_and_reverifies_exact_rules(self):
        result = self._execute(apply=True, confirm=subject.confirmation_phrase())
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["added_rule_count"], 7)
        self.assertEqual(len(self.posts), 7)
        self.assertTrue(all(item[1]["direction"] == "ingress" for item in self.posts))
        self.assertTrue(all(item[1]["protocol"] == "tcp" for item in self.posts))

    def test_existing_description_with_drift_is_rejected(self):
        region, description, source, port = subject.RULES[0]
        self.groups[region]["rules"].append(
            {
                "description": description,
                "direction": "ingress",
                "protocol": "tcp",
                "port_start": port,
                "port_end": port + 1,
                "ip": source,
                "ether_type": "IPv4",
            }
        )
        with self.assertRaisesRegex(subject.Stage4DataPlaneError, "drifted"):
            self._execute()


if __name__ == "__main__":
    unittest.main()
