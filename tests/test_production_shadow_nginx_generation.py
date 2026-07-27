from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

from core.canonical_json import canonical_json_bytes
import scripts.production_shadow_nginx_generation as nginx_generation
from scripts.production_shadow_nginx_generation import (
    DEFAULT_RELOAD_ARGV,
    GENERATION_STATES,
    HostLayout,
    CommandResult,
    NginxGenerationError,
    _journal_hash,
    confirmation_phrase,
    default_sources,
    execute_host_action,
    parse_nginx,
    produce_generations,
    render_generation,
    tokenize_nginx,
)


OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
RELEASE_TREE_SHA = "b" * 40
SHADOW_RELEASE_ROOT = (
    Path("/srv/trading-bot-three-site-production-shadow")
    / OPERATION_ID
    / "releases"
    / RELEASE_SHA
)
BOT_MINI_ROOT = "/root/trading-bot/trading_bot/mini_app_dist"


BOT_COIN = b"""# braces in comments must not affect parsing: { }
server {
    listen 80;
    server_name coin.362514.ir;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name coin.362514.ir;
    ssl_certificate /etc/letsencrypt/live/coin/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/coin/privkey.pem;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Test "quoted { value # remains data }";
    }
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Upgrade $http_upgrade;
    }
}
"""

BOT_MINI = f"""server {{
    listen 80;
    server_name mini-app.362514.ir;
    return 301 https://$host$request_uri;
}}
server {{
    listen 443 ssl;
    server_name mini-app.362514.ir;
    ssl_certificate /etc/letsencrypt/live/mini/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mini/privkey.pem;
    root {BOT_MINI_ROOT};
    location /api/ {{
        proxy_pass http://127.0.0.1:8000;
    }}
    location / {{
        try_files $uri /index.html;
    }}
}}
""".encode()

WEBAPP = b"""upstream trading_bot_api {
    server 127.0.0.1:8000;
}
server {
    listen 80;
    server_name coin.gold-trade.ir;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl;
    server_name coin.gold-trade.ir;
    ssl_certificate /etc/letsencrypt/live/gold/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gold/privkey.pem;
    root /srv/trading-bot/current/mini_app_dist;
    location /api/ {
        proxy_pass http://trading_bot_api;
    }
    location /api/ws {
        proxy_pass http://trading_bot_api;
        proxy_set_header Upgrade $http_upgrade;
    }
    location / {
        try_files $uri /index.html;
    }
}
"""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


class FakeRunner:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def __call__(self, argv, timeout: int) -> CommandResult:
        self.calls.append((tuple(argv), timeout))
        if self.results:
            return self.results.pop(0)
        return CommandResult(0, b"ok", b"")


class ProducerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sources_root = root / "sources"
        self.bot_coin = self.sources_root / "bot-coin.conf"
        self.bot_mini = self.sources_root / "bot-mini.conf"
        self.webapp = self.sources_root / "webapp.conf"
        write_private(self.bot_coin, BOT_COIN)
        write_private(self.bot_mini, BOT_MINI)
        write_private(self.webapp, WEBAPP)

    def sources(self):
        return default_sources(
            bot_coin_source=self.bot_coin,
            bot_mini_source=self.bot_mini,
            bot_mini_legacy_root=BOT_MINI_ROOT,
            webapp_source=self.webapp,
        )

    def produce(self, name: str = "output") -> tuple[Path, dict]:
        output = self.root / name
        result = produce_generations(
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            shadow_release_root=SHADOW_RELEASE_ROOT,
            role_api_ports={"bot_fi": 18001, "webapp_fi": 18002},
            sources=self.sources(),
            output_root=output,
            owner_uid=os.geteuid(),
        )
        return output, result


class NginxParserAndRenderTests(unittest.TestCase):
    def test_tokenizer_and_parser_are_comment_quote_and_brace_aware(self):
        text = BOT_COIN.decode()
        tokens = tokenize_nginx(text)
        parsed = parse_nginx(text)

        self.assertFalse(any(token.value == "comments" for token in tokens))
        self.assertTrue(
            any(token.value == "quoted { value # remains data }" for token in tokens)
        )
        self.assertEqual(sum(node.name == "server" for node in parsed), 2)

    def test_four_states_preserve_redirect_and_transform_only_ssl_server(self):
        rendered = {
            state: render_generation(
                BOT_MINI,
                operation_id=OPERATION_ID,
                vhost="mini-app.362514.ir",
                state=state,
                legacy_upstream="http://127.0.0.1:8000",
                legacy_static_root=BOT_MINI_ROOT,
                shadow_api_port=18001,
                shadow_static_root=SHADOW_RELEASE_ROOT / "mini_app_dist",
            )
            for state in GENERATION_STATES
        }

        self.assertEqual(rendered["legacy-normal"], BOT_MINI)
        redirect = """server {
    listen 80;
    server_name mini-app.362514.ir;
    return 301 https://$host$request_uri;
}"""
        for state, payload in rendered.items():
            text = payload.decode()
            self.assertIn(redirect, text)
            if state == "legacy-normal":
                self.assertNotIn("production-shadow-generation", text)
            else:
                self.assertEqual(text.count("production-shadow-generation"), 1)
        for state in ("legacy-frozen", "shadow-readonly"):
            text = rendered[state].decode()
            self.assertIn("$request_method !~ ^(GET|HEAD|OPTIONS)$", text)
            self.assertIn('$http_upgrade != ""', text)
            self.assertEqual(text.count("return 503;"), 2)
        self.assertNotIn("return 503;", rendered["shadow-writable"].decode())
        self.assertIn(
            "proxy_pass http://127.0.0.1:18001;",
            rendered["shadow-readonly"].decode(),
        )
        self.assertIn(
            f"root {SHADOW_RELEASE_ROOT}/mini_app_dist;",
            rendered["shadow-writable"].decode(),
        )
        self.assertIn(
            f"root {BOT_MINI_ROOT};",
            rendered["legacy-frozen"].decode(),
        )

    def test_alias_duplicate_unknown_values_marker_pem_and_malformed_fail_closed(self):
        cases: list[bytes] = [
            BOT_COIN.replace(
                b"server_name coin.362514.ir;",
                b"server_name coin.362514.ir alias.example;",
                1,
            ),
            BOT_COIN + BOT_COIN,
            BOT_COIN.replace(
                b"http://127.0.0.1:8000", b"http://unexpected_upstream", 1
            ),
            BOT_COIN.replace(
                b"# braces in comments",
                b"# production-shadow-generation: stale\n# braces in comments",
            ),
            BOT_COIN + b"\n-----BEGIN PRIVATE KEY-----\n",
            BOT_COIN[:-2],
        ]
        for source in cases:
            with self.subTest(source=source[-50:]):
                with self.assertRaises(NginxGenerationError):
                    render_generation(
                        source,
                        operation_id=OPERATION_ID,
                        vhost="coin.362514.ir",
                        state="shadow-readonly",
                        legacy_upstream="http://127.0.0.1:8000",
                        legacy_static_root=None,
                        shadow_api_port=18001,
                        shadow_static_root=SHADOW_RELEASE_ROOT / "mini_app_dist",
                    )

    def test_unknown_or_missing_static_root_fails_closed(self):
        for source in (
            BOT_MINI.replace(BOT_MINI_ROOT.encode(), b"/unknown/root"),
            BOT_MINI.replace(f"    root {BOT_MINI_ROOT};\n".encode(), b""),
        ):
            with self.assertRaises(NginxGenerationError):
                render_generation(
                    source,
                    operation_id=OPERATION_ID,
                    vhost="mini-app.362514.ir",
                    state="shadow-writable",
                    legacy_upstream="http://127.0.0.1:8000",
                    legacy_static_root=BOT_MINI_ROOT,
                    shadow_api_port=18001,
                    shadow_static_root=SHADOW_RELEASE_ROOT / "mini_app_dist",
                )

    def test_static_aliases_under_root_move_with_release_and_foreign_alias_blocks(self):
        source = BOT_MINI.replace(
            b"    location / {\n",
            (
                f"    location /assets/ {{\n"
                f"        alias {BOT_MINI_ROOT}/assets/;\n"
                f"    }}\n"
                f"    location / {{\n"
            ).encode(),
        )
        rendered = render_generation(
            source,
            operation_id=OPERATION_ID,
            vhost="mini-app.362514.ir",
            state="shadow-readonly",
            legacy_upstream="http://127.0.0.1:8000",
            legacy_static_root=BOT_MINI_ROOT,
            shadow_api_port=18001,
            shadow_static_root=SHADOW_RELEASE_ROOT / "mini_app_dist",
        ).decode()
        self.assertIn(
            f"alias {SHADOW_RELEASE_ROOT}/mini_app_dist/assets/;",
            rendered,
        )
        foreign = source.replace(
            f"{BOT_MINI_ROOT}/assets/".encode(),
            b"/srv/unmodeled/uploads/",
        )
        with self.assertRaisesRegex(NginxGenerationError, "legacy subtree"):
            render_generation(
                foreign,
                operation_id=OPERATION_ID,
                vhost="mini-app.362514.ir",
                state="shadow-readonly",
                legacy_upstream="http://127.0.0.1:8000",
                legacy_static_root=BOT_MINI_ROOT,
                shadow_api_port=18001,
                shadow_static_root=SHADOW_RELEASE_ROOT / "mini_app_dist",
            )


class NginxProducerTests(unittest.TestCase):
    def test_producer_emits_deterministic_private_archives_and_four_aggregates(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ProducerFixture(Path(temporary))
            first_root, first = fixture.produce("first")
            second_root, second = fixture.produce("second")

            fields = (
                "nginx_legacy_normal_generation_sha256",
                "nginx_rollback_generation_sha256",
                "nginx_freeze_generation_sha256",
                "nginx_shadow_readonly_generation_sha256",
                "nginx_shadow_writable_generation_sha256",
            )
            self.assertEqual(first["aggregate_sha256"], second["aggregate_sha256"])
            self.assertEqual(
                first["nginx_legacy_normal_generation_sha256"],
                first["nginx_rollback_generation_sha256"],
            )
            self.assertEqual(
                len({first[field] for field in fields if field != fields[1]}), 4
            )
            self.assertFalse(first["contains_tls_key_or_certificate_body"])
            self.assertFalse(first["production_contacted"])
            for role, expected_count in (("bot_fi", 8), ("webapp_fi", 4)):
                first_archive = first_root / role / "nginx-generations.tar"
                second_archive = second_root / role / "nginx-generations.tar"
                first_manifest = first_root / role / "nginx-generations-manifest.json"
                self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
                self.assertEqual(
                    first_manifest.read_bytes(),
                    (second_root / role / first_manifest.name).read_bytes(),
                )
                self.assertEqual(stat.S_IMODE(first_archive.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(first_manifest.stat().st_mode), 0o600)
                manifest = json.loads(first_manifest.read_text())
                self.assertEqual(
                    manifest["nginx_legacy_normal_generation_sha256"],
                    manifest["nginx_rollback_generation_sha256"],
                )
                with tarfile.open(first_archive, "r:") as archive:
                    members = archive.getmembers()
                    self.assertEqual(len(members), expected_count)
                    self.assertTrue(all(item.isfile() for item in members))
                    self.assertTrue(
                        all(stat.S_IMODE(item.mode) == 0o600 for item in members)
                    )
                    bodies = b"".join(
                        archive.extractfile(item).read() for item in members
                    )
                    self.assertNotIn(b"-----BEGIN CERTIFICATE-----", bodies)
                    self.assertNotIn(b"-----BEGIN PRIVATE KEY-----", bodies)

    def test_invalid_identity_ports_mapping_existing_output_and_unsafe_source_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ProducerFixture(root)
            base = {
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": RELEASE_TREE_SHA,
                "shadow_release_root": SHADOW_RELEASE_ROOT,
                "role_api_ports": {"bot_fi": 18001, "webapp_fi": 18002},
                "sources": fixture.sources(),
                "owner_uid": os.geteuid(),
            }
            bad_cases = [
                {"operation_id": "not-a-uuid"},
                {"release_sha": "A" * 40},
                {"shadow_release_root": Path("/srv/wrong")},
                {"role_api_ports": {"bot_fi": 18001, "webapp_fi": 18001}},
            ]
            for index, override in enumerate(bad_cases):
                with self.subTest(override=override):
                    with self.assertRaises(NginxGenerationError):
                        produce_generations(
                            **{**base, **override},
                            output_root=root / f"bad-{index}",
                        )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(NginxGenerationError):
                produce_generations(**base, output_root=existing)

            symlink = root / "bot-coin-link"
            symlink.symlink_to(fixture.bot_coin)
            sources = list(fixture.sources())
            sources[0] = type(sources[0])(
                **{**sources[0].__dict__, "source_path": symlink}
            )
            with self.assertRaises(NginxGenerationError):
                produce_generations(
                    **{**base, "sources": sources},
                    output_root=root / "symlink-output",
                )

            hardlink = root / "bot-coin-hardlink"
            os.link(fixture.bot_coin, hardlink)
            with self.assertRaises(NginxGenerationError):
                produce_generations(
                    **base,
                    output_root=root / "hardlink-output",
                )


class HostFixture:
    def __init__(self, root: Path, role: str = "bot_fi") -> None:
        self.root = root
        self.producer = ProducerFixture(root)
        self.output, self.aggregate = self.producer.produce()
        self.role = role
        role_row = self.aggregate["roles"][role]
        self.manifest_path = self.output / role / "nginx-generations-manifest.json"
        self.archive_path = self.output / role / "nginx-generations.tar"
        self.manifest_sha256 = role_row["manifest_sha256"]
        self.expected_host = role_row["expected_host"]
        self.system_root = root / "system"
        self.sites_available = self.system_root / "etc/nginx/sites-available"
        self.sites_enabled = self.system_root / "etc/nginx/sites-enabled"
        self.sites_available.mkdir(parents=True)
        self.sites_enabled.mkdir()
        self.nginx_conf = self.system_root / "etc/nginx/nginx.conf"
        top = (
            "user root;\nevents {}\nhttp {\n"
            f"    include {self.sites_enabled}/*;\n"
            "}\n"
        ).encode()
        write_private(self.nginx_conf, top)
        self.nginx_conf.chmod(0o644)
        source_by_vhost = {
            "coin.362514.ir": BOT_COIN,
            "mini-app.362514.ir": BOT_MINI,
            "coin.gold-trade.ir": WEBAPP,
        }
        manifest = json.loads(self.manifest_path.read_text())
        for row in manifest["vhosts"]:
            logical = Path(row["destination"])
            active = self.system_root / logical.relative_to("/")
            write_private(active, source_by_vhost[row["vhost"]])
            active.chmod(0o644)
            link = self.sites_enabled / f"managed-{row['vhost']}"
            link.symlink_to(Path("../sites-available") / active.name)
        self.foreign = self.sites_available / "foreign"
        write_private(
            self.foreign,
            b"server { listen 127.0.0.1:9999; server_name foreign.invalid; }\n",
        )
        self.foreign.chmod(0o644)
        (self.sites_enabled / "foreign").symlink_to("../sites-available/foreign")
        self.operation_base = root / "operation-base"
        self.operation_base.mkdir(mode=0o700)
        self.layout = HostLayout(
            system_root=self.system_root,
            operation_base=self.operation_base,
            nginx_bin=Path("/test/usr/sbin/nginx"),
            nginx_conf=self.nginx_conf,
            sites_available=self.sites_available,
            sites_enabled=self.sites_enabled,
            reload_argv=("/test/usr/bin/systemctl", "reload", "nginx"),
            owner_uid=os.geteuid(),
            identity_addresses=(self.expected_host,),
        )

    @property
    def operation_root(self) -> Path:
        return self.operation_base / OPERATION_ID / self.role.replace("_", "-")

    def call(
        self,
        action: str,
        *,
        generation: str | None = None,
        apply: bool = True,
        confirm: str | None = None,
        runner=None,
    ):
        effective = "legacy-normal" if action == "restore" else generation
        if confirm is None and apply and action in {
            "install",
            "test",
            "activate",
            "restore",
        }:
            confirm = confirmation_phrase(
                action=action,
                operation_id=OPERATION_ID,
                role=self.role,
                generation=effective,
            )
        return execute_host_action(
            manifest_path=self.manifest_path,
            expected_manifest_sha256=self.manifest_sha256,
            archive_path=self.archive_path,
            role=self.role,
            expected_host=self.expected_host,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
            release_tree_sha=RELEASE_TREE_SHA,
            action=action,
            generation=generation,
            apply=apply,
            confirm=confirm,
            layout=self.layout,
            runner=runner or FakeRunner(),
        )

    def active_bytes(self) -> dict[str, bytes]:
        manifest = json.loads(self.manifest_path.read_text())
        return {
            row["vhost"]: (
                self.system_root / Path(row["destination"]).relative_to("/")
            ).read_bytes()
            for row in manifest["vhosts"]
        }


class NginxHostWorkerTests(unittest.TestCase):
    def test_plan_is_default_and_does_not_create_operation_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            runner = FakeRunner()
            result = fixture.call("install", apply=False, runner=runner)

            self.assertEqual(result["status"], "planned")
            self.assertFalse(result["active_configuration_mutated"])
            self.assertFalse(fixture.operation_root.exists())
            self.assertEqual(runner.calls, [])

    def test_exact_confirmation_install_test_activate_readback_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            before = fixture.active_bytes()
            foreign_before = fixture.foreign.read_bytes()
            with self.assertRaises(NginxGenerationError):
                fixture.call("install", confirm="wrong")
            installed = fixture.call("install")
            self.assertEqual(installed["status"], "installed")
            self.assertEqual(
                installed["schema"],
                "production-shadow-nginx-host-action-result-v1",
            )
            self.assertEqual(installed["release_tree_sha"], RELEASE_TREE_SHA)
            self.assertFalse(installed["active_configuration_mutated"])
            self.assertEqual(fixture.call("install")["status"], "already-installed")
            self.assertEqual(fixture.active_bytes(), before)
            self.assertEqual(fixture.foreign.read_bytes(), foreign_before)

            candidate_runner = FakeRunner()
            tested = fixture.call(
                "test",
                generation="legacy-frozen",
                runner=candidate_runner,
            )
            self.assertEqual(tested["status"], "tested")
            self.assertEqual(
                fixture.call("test", generation="legacy-frozen")["status"],
                "already-tested",
            )
            self.assertEqual(len(candidate_runner.calls), 1)
            candidate_top = Path(candidate_runner.calls[0][0][3])
            candidate_text = candidate_top.read_text()
            self.assertIn("candidates/legacy-frozen/sites-enabled.conf", candidate_text)
            includes = candidate_top.parent.joinpath("sites-enabled.conf").read_text()
            self.assertIn(str(fixture.sites_enabled / "foreign"), includes)
            self.assertNotIn(
                str(fixture.sites_enabled / "managed-coin.362514.ir"),
                includes,
            )
            self.assertEqual(fixture.active_bytes(), before)

            activation_runner = FakeRunner()
            activated = fixture.call(
                "activate",
                generation="legacy-frozen",
                runner=activation_runner,
            )
            self.assertEqual(activated["status"], "activated")
            self.assertTrue(activated["active_configuration_mutated"])
            self.assertTrue(activated["service_reloaded"])
            self.assertEqual(len(activation_runner.calls), 2)
            self.assertEqual(
                activation_runner.calls[1][0],
                fixture.layout.reload_argv,
            )
            active = fixture.active_bytes()
            self.assertTrue(
                all(b"production-shadow-generation" in value for value in active.values())
            )
            self.assertTrue(all(b"return 503;" in value for value in active.values()))
            self.assertEqual(fixture.foreign.read_bytes(), foreign_before)
            manifest = json.loads(fixture.manifest_path.read_text())
            self.assertTrue(
                all(
                    stat.S_IMODE(
                        (
                            fixture.system_root
                            / Path(row["destination"]).relative_to("/")
                        ).stat().st_mode
                    )
                    == 0o644
                    for row in manifest["vhosts"]
                )
            )
            self.assertEqual(
                fixture.call(
                    "activate",
                    generation="legacy-frozen",
                )["status"],
                "already-active",
            )

            readback = fixture.call("readback")
            self.assertEqual(readback["state"], "legacy-frozen")
            self.assertFalse(readback["active_configuration_mutated"])

            fixture.call("test", generation="legacy-normal")
            restored = fixture.call("restore")
            self.assertEqual(restored["state"], "legacy-normal")
            self.assertEqual(fixture.active_bytes(), before)
            self.assertEqual(fixture.foreign.read_bytes(), foreign_before)

    def test_candidate_test_failure_makes_no_active_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            before = fixture.active_bytes()
            fixture.call("install")
            runner = FakeRunner([CommandResult(1, b"", b"syntax error")])

            with self.assertRaisesRegex(NginxGenerationError, "candidate Nginx test failed"):
                fixture.call("test", generation="legacy-frozen", runner=runner)
            self.assertEqual(fixture.active_bytes(), before)
            journal = json.loads((fixture.operation_root / "journal.json").read_text())
            self.assertNotIn("legacy-frozen", journal["tested_states"])
            self.assertEqual(journal["events"][-1]["kind"], "test-failed")
            self.assertEqual(journal["events"][-1]["command"]["returncode"], 1)
            self.assertEqual(
                journal["events"][-1]["command"]["stderr_sha256"],
                sha256(b"syntax error"),
            )

    def test_reload_failure_restores_exact_previous_bytes_and_reloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            before = fixture.active_bytes()
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            runner = FakeRunner(
                [
                    CommandResult(0, b"active syntax ok", b""),
                    CommandResult(1, b"", b"reload failed"),
                    CommandResult(0, b"rollback syntax ok", b""),
                    CommandResult(0, b"rollback reload ok", b""),
                ]
            )

            with self.assertRaisesRegex(
                NginxGenerationError,
                "previous generation was restored",
            ):
                fixture.call(
                    "activate",
                    generation="legacy-frozen",
                    runner=runner,
                )
            self.assertEqual(len(runner.calls), 4)
            self.assertEqual(fixture.active_bytes(), before)
            journal = json.loads((fixture.operation_root / "journal.json").read_text())
            self.assertEqual(journal["active_state"], "legacy-normal")
            self.assertIsNone(journal["transaction"])
            self.assertEqual(journal["events"][-1]["kind"], "rolled-back")
            self.assertEqual(
                journal["events"][-1]["failure_evidence"]["returncode"],
                1,
            )

    def test_raw_filesystem_error_after_first_replace_also_rolls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            before = fixture.active_bytes()
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            original = nginx_generation._atomic_replace_destination
            calls = 0

            def injected(path, payload, *, expected_sha256, owner_uid):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected replacement failure")
                return original(
                    path,
                    payload,
                    expected_sha256=expected_sha256,
                    owner_uid=owner_uid,
                )

            with mock.patch.object(
                nginx_generation,
                "_atomic_replace_destination",
                side_effect=injected,
            ):
                with self.assertRaisesRegex(
                    NginxGenerationError,
                    "previous generation was restored",
                ):
                    fixture.call("activate", generation="legacy-frozen")
            self.assertEqual(fixture.active_bytes(), before)

    def test_rollback_validation_failure_is_durable_and_blocks_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            runner = FakeRunner(
                [
                    CommandResult(0, b"target syntax ok", b""),
                    CommandResult(1, b"", b"target reload failed"),
                    CommandResult(1, b"", b"rollback syntax failed"),
                ]
            )
            with self.assertRaisesRegex(
                NginxGenerationError,
                "rollback validation also failed",
            ):
                fixture.call(
                    "activate",
                    generation="legacy-frozen",
                    runner=runner,
                )
            journal = json.loads((fixture.operation_root / "journal.json").read_text())
            self.assertEqual(journal["transaction"]["status"], "rollback-failed")
            self.assertEqual(
                journal["transaction"]["failure_evidence"]["returncode"],
                1,
            )
            self.assertEqual(
                journal["transaction"]["rollback_failure_evidence"]["returncode"],
                1,
            )
            with self.assertRaisesRegex(
                NginxGenerationError,
                "prior Nginx rollback failed",
            ):
                fixture.call("restore")

    def test_inventory_drift_and_destination_drift_block_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            fixture.foreign.write_bytes(fixture.foreign.read_bytes() + b"# drift\n")
            fixture.foreign.chmod(0o600)
            runner = FakeRunner()
            with self.assertRaisesRegex(NginxGenerationError, "inventory drifted"):
                fixture.call(
                    "activate",
                    generation="legacy-frozen",
                    runner=runner,
                )
            self.assertEqual(runner.calls, [])

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            fixture.nginx_conf.write_bytes(
                fixture.nginx_conf.read_bytes() + b"# top-level drift\n"
            )
            fixture.nginx_conf.chmod(0o600)
            with self.assertRaisesRegex(NginxGenerationError, "inventory drifted"):
                fixture.call("activate", generation="legacy-frozen")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            manifest = json.loads(fixture.manifest_path.read_text())
            first = manifest["vhosts"][0]
            path = fixture.system_root / Path(first["destination"]).relative_to("/")
            path.write_bytes(path.read_bytes() + b"# foreign\n")
            path.chmod(0o600)
            with self.assertRaisesRegex(NginxGenerationError, "mixed or foreign"):
                fixture.call("readback")

    def test_transition_order_and_post_writable_restore_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            fixture.call("test", generation="shadow-readonly")
            with self.assertRaisesRegex(NginxGenerationError, "not allowlisted"):
                fixture.call("activate", generation="shadow-readonly")

            fixture.call("test", generation="legacy-frozen")
            fixture.call("activate", generation="legacy-frozen")
            fixture.call("activate", generation="shadow-readonly")
            fixture.call("test", generation="shadow-writable")
            fixture.call("activate", generation="shadow-writable")
            fixture.call("test", generation="legacy-normal")
            with self.assertRaisesRegex(
                NginxGenerationError,
                "only before shadow-writable",
            ):
                fixture.call("restore")

    def test_crash_resume_rolls_partial_transaction_back_before_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            before = fixture.active_bytes()
            fixture.call("install")
            fixture.call("test", generation="legacy-frozen")
            manifest = json.loads(fixture.manifest_path.read_text())
            archive = tarfile.open(fixture.archive_path, "r:")
            first = manifest["vhosts"][0]
            member = (
                f"generations/legacy-frozen/"
                f"{first['destination'].removeprefix('/')}"
            )
            target_payload = archive.extractfile(member).read()
            archive.close()
            active_path = fixture.system_root / Path(first["destination"]).relative_to("/")
            active_path.write_bytes(target_payload)
            active_path.chmod(0o600)
            journal_path = fixture.operation_root / "journal.json"
            journal = json.loads(journal_path.read_text())
            journal["transaction"] = {
                "from_state": "legacy-normal",
                "to_state": "legacy-frozen",
                "status": "applying",
                "inventory_sha256": journal["tested_states"]["legacy-frozen"][
                    "inventory_sha256"
                ],
            }
            journal["state_sha256"] = _journal_hash(journal)
            journal_path.write_bytes(canonical_json_bytes(journal))
            journal_path.chmod(0o600)
            runner = FakeRunner()

            with self.assertRaisesRegex(
                NginxGenerationError,
                "pending transaction",
            ):
                fixture.call("readback", runner=runner)
            self.assertEqual(runner.calls, [])
            result = fixture.call("restore", runner=runner)
            self.assertEqual(result["state"], "legacy-normal")
            self.assertEqual(fixture.active_bytes(), before)
            self.assertEqual(len(runner.calls), 2)

    def test_symlink_destination_enabled_escape_and_manifest_or_archive_tamper_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            manifest = json.loads(fixture.manifest_path.read_text())
            first = manifest["vhosts"][0]
            destination = (
                fixture.system_root / Path(first["destination"]).relative_to("/")
            )
            real = destination.with_suffix(".real")
            destination.rename(real)
            destination.symlink_to(real)
            with self.assertRaises(NginxGenerationError):
                fixture.call("install")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            outside = fixture.root / "outside.conf"
            write_private(outside, b"server { listen 1; }\n")
            enabled = fixture.sites_enabled / "foreign"
            enabled.unlink()
            enabled.symlink_to(outside)
            with self.assertRaisesRegex(NginxGenerationError, "escapes sites-available"):
                fixture.call("install")
                fixture.call("test", generation="legacy-frozen")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            payload = bytearray(fixture.archive_path.read_bytes())
            payload[600] ^= 1
            fixture.archive_path.write_bytes(payload)
            fixture.archive_path.chmod(0o600)
            with self.assertRaisesRegex(NginxGenerationError, "bytes or hash differ"):
                fixture.call("install")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            document = json.loads(fixture.manifest_path.read_text())
            document["expected_host"] = "127.0.0.1"
            fixture.manifest_path.write_bytes(canonical_json_bytes(document))
            fixture.manifest_path.chmod(0o600)
            with self.assertRaisesRegex(NginxGenerationError, "manifest hash differs"):
                fixture.call("install")

    def test_archive_path_escape_and_hardlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            original = json.loads(fixture.manifest_path.read_text())
            member = original["archive"]["members"][0]
            original["archive"]["members"][0] = "../escape"
            buffer = io.BytesIO()
            with tarfile.open(
                fileobj=buffer,
                mode="w",
                format=tarfile.USTAR_FORMAT,
            ) as archive:
                info = tarfile.TarInfo("../escape")
                info.type = tarfile.LNKTYPE
                info.linkname = member
                info.mode = 0o600
                info.uid = info.gid = 0
                info.size = 0
                archive.addfile(info)
            archive_payload = buffer.getvalue()
            original["archive"]["sha256"] = sha256(archive_payload)
            original["archive"]["bytes"] = len(archive_payload)
            manifest_payload = canonical_json_bytes(original)
            fixture.archive_path.write_bytes(archive_payload)
            fixture.archive_path.chmod(0o600)
            fixture.manifest_path.write_bytes(manifest_payload)
            fixture.manifest_path.chmod(0o600)
            fixture.manifest_sha256 = sha256(manifest_payload)
            with self.assertRaises(NginxGenerationError):
                fixture.call("install")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            document = json.loads(fixture.manifest_path.read_text())
            with tarfile.open(fixture.archive_path, "r:") as source:
                members = {
                    item.name: source.extractfile(item).read()
                    for item in source.getmembers()
                }
            buffer = io.BytesIO()
            names = document["archive"]["members"]
            with tarfile.open(
                fileobj=buffer,
                mode="w",
                format=tarfile.USTAR_FORMAT,
            ) as archive:
                for index, name in enumerate(names):
                    info = tarfile.TarInfo(name)
                    info.mode = 0o600
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = 0
                    if index == 0:
                        info.type = tarfile.LNKTYPE
                        info.linkname = names[1]
                        info.size = 0
                        archive.addfile(info)
                    else:
                        info.size = len(members[name])
                        archive.addfile(info, io.BytesIO(members[name]))
            payload = buffer.getvalue()
            document["archive"]["sha256"] = sha256(payload)
            document["archive"]["bytes"] = len(payload)
            manifest_payload = canonical_json_bytes(document)
            fixture.archive_path.write_bytes(payload)
            fixture.archive_path.chmod(0o600)
            fixture.manifest_path.write_bytes(manifest_payload)
            fixture.manifest_path.chmod(0o600)
            fixture.manifest_sha256 = sha256(manifest_payload)
            with self.assertRaisesRegex(NginxGenerationError, "unsafe member"):
                fixture.call("install")

    def test_default_reload_contract_remains_systemctl_reload_only(self):
        self.assertEqual(DEFAULT_RELOAD_ARGV, ("/usr/bin/systemctl", "reload", "nginx"))

    def test_local_host_identity_is_required_before_any_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.layout = HostLayout(
                **{
                    **fixture.layout.__dict__,
                    "identity_addresses": ("127.0.0.1",),
                }
            )
            with self.assertRaisesRegex(
                NginxGenerationError,
                "local host identity differs",
            ):
                fixture.call("install")
            self.assertFalse(fixture.operation_root.exists())

    def test_journal_event_or_transaction_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            journal_path = fixture.operation_root / "journal.json"
            journal = json.loads(journal_path.read_text())
            journal["events"][0]["kind"] = "forged"
            journal["state_sha256"] = _journal_hash(journal)
            journal_path.write_bytes(canonical_json_bytes(journal))
            journal_path.chmod(0o600)
            with self.assertRaisesRegex(NginxGenerationError, "journal is invalid"):
                fixture.call("readback")

        with tempfile.TemporaryDirectory() as temporary:
            fixture = HostFixture(Path(temporary))
            fixture.call("install")
            journal_path = fixture.operation_root / "journal.json"
            journal = json.loads(journal_path.read_text())
            journal["transaction"] = {
                "from_state": "legacy-normal",
                "to_state": "legacy-frozen",
                "status": "unbounded",
                "inventory_sha256": "1" * 64,
            }
            journal["state_sha256"] = _journal_hash(journal)
            journal_path.write_bytes(canonical_json_bytes(journal))
            journal_path.chmod(0o600)
            with self.assertRaisesRegex(
                NginxGenerationError,
                "pending Nginx transaction is invalid",
            ):
                fixture.call("readback")


if __name__ == "__main__":
    unittest.main()
