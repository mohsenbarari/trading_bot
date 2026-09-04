"""Disposable SQLite fixtures for the version-pinned CLI binding adapter."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.two_site_antigravity_session import binding, fields, one

ROOT = Path(__file__).resolve().parents[1]
PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    return bytes(result) + bytes([number])


def message(*pairs):
    result = b""
    for number, value in pairs:
        if isinstance(value, str):
            value = value.encode()
        if isinstance(value, int):
            result += varint(number << 3) + varint(value)
        else:
            result += varint(number << 3 | 2) + varint(len(value)) + value
    return result


def fixture(directory, role="two-site-coordinator", *, omit_zero=False,
            wrong_prompt=False, extra_tool=False, split_config=False):
    root = Path(directory) / "repo"
    home = Path(directory) / "cli"
    agent = root / ".agents" / "agents" / role / "agent.md"
    agent.parent.mkdir(parents=True)
    definition = (ROOT / ".agents" / "agents" / role / "agent.md").read_text()
    agent.write_text(definition)
    parts = definition.split("---", 2)
    meta = yaml.safe_load(parts[1])
    tools = meta["tools"]
    body = parts[2][len("\n\n# System Prompt\n\n"):-1]
    if wrong_prompt:
        body += "\nUnreviewed extra instruction."
    enabled = tools + (["unreviewed_tool"] if extra_tool else [])
    prompt = message((1, message((1, "System Prompt"), (2, body))))
    container = message((2, prompt), *[(3, message((1, tool), (2, 1))) for tool in enabled])
    config = message((5, "sandbox"), (17, message((16, agent.as_uri()))), (18, message((1, container))))
    main = role == "two-site-coordinator"
    flags = [(12, int(not main)), (13, int(main))]
    if omit_zero:
        flags = [(key, value) for key, value in flags if value]
    script = message((1, role), (7, config), (8, meta["description"]), *flags, (15, 1))
    session = PARENT if main else CHILD
    pairs = [(4, script), (6, PARENT), (10, config + (message((99, "drift")) if split_config else b""))]
    if not main:
        label = "Stage Worker" if role.endswith("worker") else "Stage Verifier"
        pairs += [(5, PARENT), (8, message((1, role), (2, label))), (17, 1)]
    database = home / "conversations" / f"{session}.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE trajectory_meta(cascade_id TEXT,trajectory_type INT,source INT)")
        db.execute("INSERT INTO trajectory_meta VALUES(?,4,17)", (session,))
        db.execute("CREATE TABLE trajectory_metadata_blob(id TEXT,data BLOB)")
        db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (message(*pairs),))
        db.execute("CREATE TABLE steps(status INT,error_details BLOB)")
        db.executemany("INSERT INTO steps VALUES(3,?)", [(b"",), (b"",)])
        db.execute("CREATE TABLE executor_metadata(data BLOB)")
        db.execute("INSERT INTO executor_metadata VALUES(?)", (
            message((10, message((1, message((28, "gemini-3.8-flash-high")))))),))
    return root, home, database, session, set(tools), None if main else PARENT


class BindingTests(unittest.TestCase):
    def check_fixture(self, item, role="two-site-coordinator"):
        root, home, _database, session, tools, parent = item
        return binding(session, role, root, tools, parent_id=parent, cli_home=home)

    def test_exact_three_roles_and_readonly_database(self):
        for role in ("two-site-coordinator", "two-site-stage-worker", "two-site-stage-verifier"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory:
                item = fixture(directory, role)
                before = item[2].read_bytes()
                self.assertEqual(role, self.check_fixture(item, role)["name"])
                self.assertEqual(before, item[2].read_bytes())

    def test_missing_zero_flag_and_config_or_prompt_drift_block(self):
        for option in ("omit_zero", "wrong_prompt", "extra_tool", "split_config"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as directory:
                item = fixture(directory, **{option: True})
                with self.assertRaises(ValueError):
                    self.check_fixture(item)

    def test_duplicate_identity_and_failed_or_unfinished_steps_block(self):
        for sql in (
            "INSERT INTO trajectory_meta SELECT * FROM trajectory_meta",
            "UPDATE trajectory_meta SET source=0",
            "INSERT INTO trajectory_metadata_blob SELECT * FROM trajectory_metadata_blob",
            "UPDATE steps SET status=1",
            "UPDATE steps SET error_details=x'01'",
            "DELETE FROM executor_metadata",
        ):
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as directory:
                item = fixture(directory)
                with sqlite3.connect(item[2]) as db:
                    db.execute(sql)
                with self.assertRaises(ValueError):
                    self.check_fixture(item)

    def test_reparenting_and_wrong_uuid_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root, home, _database, session, tools, _parent = fixture(directory, "two-site-stage-worker")
            with self.assertRaises(ValueError):
                binding(session, "two-site-stage-worker", root, tools, parent_id=CHILD, cli_home=home)
            with self.assertRaises(ValueError):
                binding("../credential", "two-site-stage-worker", root, tools, cli_home=home)

    def test_source_definition_change_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            item = fixture(directory)
            agent = item[0] / ".agents/agents/two-site-coordinator/agent.md"
            agent.write_text(agent.read_text().replace("Primary orchestrator", "Unexpected orchestrator"))
            with self.assertRaises(ValueError):
                self.check_fixture(item)

    def test_decoder_rejects_truncation_groups_and_ambiguous_fields(self):
        for blob in (b"\x80", b"\x00", b"\x0a\x05x", b"\x0b", b"\x80" * 11):
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                fields(blob)
        with self.assertRaises(ValueError):
            one(message((1, "a"), (1, "b")), 1)


if __name__ == "__main__":
    unittest.main()
