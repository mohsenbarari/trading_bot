"""Wiring replacements for Codex model and pinned CLI migration."""

from __future__ import annotations

WIRING_REPLACEMENTS: tuple[tuple[str, str, str], ...] = (
    (
        "scripts/two_site_codex_autopilot.py",
        'MODEL = "gpt-6-sol"',
        'MODEL = "gpt-6.1-sol"',
    ),
    (
        "scripts/two_site_codex_autopilot.py",
        'EXPECTED_CODEX_CLI_VERSION = "codex-cli 0.156.1"',
        'EXPECTED_CODEX_CLI_VERSION = "codex-cli 0.159.2"',
    ),
    (
        "scripts/two_site_codex_autopilot.py",
        'EXPECTED_CODEX_CLI_SHA256 = "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f"',
        'EXPECTED_CODEX_CLI_SHA256 = "1748767b230ebfc3d4ab7e4e254920d0c0ad9691fd8c11f190e7d44511a4a92e"',
    ),
    (
        "scripts/two_site_codex_autopilot.py",
        '    "codex-code-mode-host": "45ba654b0e145406f3316be4729b886980ce811aa2e3b6ed4e033dd41ab524f1",',
        '    "codex-code-mode-host": "5b2c075ac2380fa04d76d7313fbc044d29c8d0a0d0b9138415acd4610211ca03",',
    ),
    (
        "scripts/two_site_codex_autopilot.py",
        '    "codex-package.json": "6cf97f0c8999775e81c038429a31f28c406e077ecdbece52a9700e244bd2458f",',
        '    "codex-package.json": "6e812d1eecc3ffa0b339df730a53137d8ec1d1e2617d09c23878bd25e1a56845",',
    ),
    (
        "scripts/two_site_codex_autopilot.py",
        'args.codex = args.root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"',
        'args.codex = args.root.resolve() / ".local/tools/codex-0.159.2-1748767b230ebfc3/codex"',
    ),
    (
        "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
        "Use gpt-6-sol/xhigh.",
        "Use gpt-6.1-sol/xhigh.",
    ),
    (
        "docs/refactor/two-server/PROPOSAL_AUTOMATION.md",
        "مدل جاری Supervisor: `gpt-6-sol` با `xhigh`.",
        "مدل جاری Supervisor: `gpt-6.1-sol` با `xhigh`.",
    ),
    (
        "docs/refactor/two-server/PROPOSAL_AUTOMATION.md",
        (
            "Codex CLI نسخهٔ 0.156.1 با SHA ثبت‌شده در Supervisor، از مسیر مستقل این integration:\n"
            "`.local/tools/codex-0.156.1-0b2e9301d6100ddd/codex`."
        ),
        (
            "Codex CLI نسخهٔ 0.159.2 با SHA ثبت‌شده در Supervisor، از مسیر مستقل این integration:\n"
            "`.local/tools/codex-0.159.2-1748767b230ebfc3/codex`."
        ),
    ),
    (
        "tests/test_two_site_autopilot_stable_cli.py",
        'self.assertEqual(autopilot.EXPECTED_CODEX_CLI_VERSION, "codex-cli 0.156.1")',
        'self.assertEqual(autopilot.EXPECTED_CODEX_CLI_VERSION, "codex-cli 0.159.2")',
    ),
    (
        "tests/test_two_site_autopilot_stable_cli.py",
        (
            "        self.assertEqual(\n"
            "            autopilot.EXPECTED_CODEX_CLI_SHA256,\n"
            '            "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f",\n'
            "        )"
        ),
        (
            "        self.assertEqual(\n"
            "            autopilot.EXPECTED_CODEX_CLI_SHA256,\n"
            '            "1748767b230ebfc3d4ab7e4e254920d0c0ad9691fd8c11f190e7d44511a4a92e",\n'
            "        )"
        ),
    ),
    (
        "tests/test_two_site_autopilot_stable_cli.py",
        'self.assertEqual(autopilot.MODEL, "gpt-6-sol")',
        'self.assertEqual(autopilot.MODEL, "gpt-6.1-sol")',
    ),
    (
        "tests/test_two_site_autopilot_stable_cli.py",
        'derived = root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"',
        'derived = root.resolve() / ".local/tools/codex-0.159.2-1748767b230ebfc3/codex"',
    ),
    (
        "tests/test_two_site_autopilot_stable_cli.py",
        'missing = root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"',
        'missing = root.resolve() / ".local/tools/codex-0.159.2-1748767b230ebfc3/codex"',
    ),
    (
        "tests/test_two_site_zero_echo_recipe.py",
        'assert "Use gpt-6-sol/xhigh" in prompt_text',
        'assert "Use gpt-6.1-sol/xhigh" in prompt_text',
    ),
    (
        "tests/test_two_site_zero_echo_recipe.py",
        (
            "    # Current mandatory safety/workflow clauses measure 16621 bytes; leaves <379 bytes headroom.\n"
            "    assert prompt_bytes < 17000"
        ),
        (
            "    # Current mandatory safety/workflow clauses measure 17262 bytes; leaves <738 bytes headroom.\n"
            "    assert prompt_bytes < 18000"
        ),
    ),
    (
        "tests/test_two_site_zero_echo_recipe.py",
        'assert ap.MODEL == "gpt-6-sol"',
        'assert ap.MODEL == "gpt-6.1-sol"',
    ),
)
