import copy
import unittest
from unittest.mock import patch

from scripts import run_three_site_staging_convergence_observer as subject


class ConvergenceObserverRelayTests(unittest.TestCase):
    def _config(self):
        return {
            "ssh": {
                "binary": "/usr/bin/ssh",
                "identity_file": "/root/.ssh/id_ed25519",
                "known_hosts_file": "/root/known_hosts",
                "connect_timeout_seconds": 10,
            },
            "sites": {
                "webapp_ir": {
                    "host": "188.213.198.115",
                    "port": 22,
                    "user": "root",
                    "proxy": {
                        "binary": "/usr/bin/ssh",
                        **subject.WEBAPP_IR_RELAY,
                    },
                }
            },
        }

    def _sites(self):
        root = "/srv/trading-bot-three-site-staging-data/releases/release/source"
        result = {}
        for site, host in {
            "bot_fi": "130.185.121.98",
            "webapp_fi": "194.5.206.69",
            "webapp_ir": "188.213.198.115",
        }.items():
            cli = site.replace("_", "-")
            result[site] = {
                "host": host,
                "port": 22,
                "user": "root",
                "repo_root": root,
                "compose_file": f"{root}/deploy/staging/docker-compose.three-site.yml",
                "env_file": f"/etc/trading-bot/roles/{cli}.env",
            }
        return result

    def _inventory(self):
        return {
            "roles": [
                {"role": site, "host_ip": host}
                for site, host in {
                    "bot_fi": "130.185.121.98",
                    "webapp_fi": "194.5.206.69",
                    "webapp_ir": "188.213.198.115",
                }.items()
            ]
        }

    def test_proxy_command_is_exact_and_strict(self):
        command = subject._observer_ssh_base(self._config(), "webapp_ir")
        option = next(item for item in command if item.startswith("ProxyCommand="))
        self.assertIn("root@185.231.182.6", option)
        self.assertIn("UserKnownHostsFile=/root/.ssh/known_hosts", option)
        self.assertIn("-i /root/.ssh/id_rsa", option)
        self.assertTrue(option.endswith("root@185.231.182.6"))

    def test_direct_site_has_no_proxy_command(self):
        config = self._config()
        config["sites"] = {
            "bot_fi": {"host": "130.185.121.98", "port": 22, "user": "root"}
        }
        command = subject._observer_ssh_base(config, "bot_fi")
        self.assertFalse(any(item.startswith("ProxyCommand=") for item in command))

    def test_only_webapp_ir_may_use_proxy(self):
        sites = self._sites()
        sites["bot_fi"]["proxy"] = {
            "binary": "/usr/bin/ssh",
            **subject.WEBAPP_IR_RELAY,
        }
        with self.assertRaisesRegex(subject.ConvergenceObserverError, "proxy config"):
            subject._validate_sites(sites, inventory=self._inventory())

    def test_proxy_identity_and_paths_are_pinned(self):
        sites = self._sites()
        sites["webapp_ir"]["proxy"] = {
            "binary": "/usr/bin/ssh",
            **subject.WEBAPP_IR_RELAY,
        }
        sites["webapp_ir"]["proxy"]["identity_file"] = "/tmp/other-key"
        with (
            patch.object(subject, "_secure_regular_file"),
            self.assertRaisesRegex(subject.ConvergenceObserverError, "identity differs"),
        ):
            subject._validate_sites(sites, inventory=self._inventory())

    def test_valid_pinned_proxy_is_accepted(self):
        sites = self._sites()
        sites["webapp_ir"]["proxy"] = {
            "binary": "/usr/bin/ssh",
            **copy.deepcopy(subject.WEBAPP_IR_RELAY),
        }
        with patch.object(subject, "_secure_regular_file"):
            result = subject._validate_sites(sites, inventory=self._inventory())
        self.assertEqual(result["webapp_ir"]["proxy"]["host"], "185.231.182.6")

    def test_remote_command_uses_attested_role_compose_beside_role_env(self):
        config = self._config()
        config["sites"] = self._sites()
        command = subject._remote_command(
            config,
            "webapp_ir",
            "scripts/export_three_site_staging_convergence_snapshot.py",
        )
        compose_index = command.index("-f")
        self.assertEqual(
            command[compose_index + 1],
            "/etc/trading-bot/roles/webapp-ir.compose.yml",
        )
        self.assertNotIn(config["sites"]["webapp_ir"]["compose_file"], command)

    def test_role_env_basename_is_pinned_to_site(self):
        sites = self._sites()
        sites["webapp_ir"]["env_file"] = "/etc/trading-bot/roles/webapp-fi.env"
        with self.assertRaisesRegex(subject.ConvergenceObserverError, "role env path"):
            subject._validate_sites(sites, inventory=self._inventory())


if __name__ == "__main__":
    unittest.main()
