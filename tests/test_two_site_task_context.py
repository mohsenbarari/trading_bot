"""Unit tests for preparation_notes context helper."""
import copy
import json
import pytest
from scripts.two_site_task_context import preparation_notes


def make_task(goal="", reqs=None, excerpts=None, **kwargs):
    t = {"task_id": "test-task", "goal": goal, "requirements": reqs or []}
    if excerpts is not None:
        t["excerpts"] = excerpts
    t.update(kwargs)
    return t


@pytest.mark.parametrize("lit_code,expected_sub", [
    ('x = "معامله\u200cای"', "\\u200c"),
    ('x = "معامله\\u200cای"', "\\u200c"),
    ('x = "معامله\\U0000200cای"', "\\u200c"),
    ('x = "معامله\\N{ZERO WIDTH NON-JOINER}ای"', "\\u200c"),
])
def test_zwnj_escape_forms(lit_code, expected_sub):
    task = make_task()
    sources = {"scripts/app.py": {"content": lit_code}}
    notes = preparation_notes(task, sources)
    assert len(notes["literal_hints"]) == 1
    h = notes["literal_hints"][0]
    assert set(h.keys()) == {"path", "line", "json_literal", "is_fragment"}
    assert expected_sub in h["json_literal"]
    assert h["is_fragment"] is False
    assert h["line"] == 1


@pytest.mark.parametrize("code,expect_hints", [
    ('x = "معاملهای"\ny = "دکمههای"', 0),
    ('f = f"معامله{var}ای"', 0),
    ('f = f"معامله\\u200c{var}ای"', 1),
])
def test_joined_text_mismatch(code, expect_hints):
    task = make_task()
    sources = {"scripts/app.py": {"content": code}}
    notes = preparation_notes(task, sources)
    assert len(notes["literal_hints"]) == expect_hints


@pytest.mark.parametrize("char_code,expected_esc", [
    ('x = "join\\u200dchar"', "\\u200d"),
    ('x = "bidi\\u200eleft"', "\\u200e"),
    ('x = "bidi\\u200fright"', "\\u200f"),
    ('x = "arabic\\u061cmark"', "\\u061c"),
])
def test_zwj_and_bidi(char_code, expected_esc):
    task = make_task()
    sources = {"scripts/app.py": {"content": char_code}}
    notes = preparation_notes(task, sources)
    assert len(notes["literal_hints"]) == 1
    assert expected_esc in notes["literal_hints"][0]["json_literal"]


def test_canonical_nested_range_original_line():
    code = "# comment\nx = 1\ny = 'دکمه\\u200cای'\n"
    excerpts = [{
        "path": "scripts/app.py",
        "ranges": [{"start": 50, "end": 55, "text": code}],
    }]
    t1 = make_task(excerpts=excerpts)
    n1 = preparation_notes(t1, {})
    assert len(n1["literal_hints"]) == 1
    assert n1["literal_hints"][0]["line"] == 52

    goal_marker = "Goal text\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" + json.dumps({"excerpts": excerpts})
    t2 = make_task(goal=goal_marker)
    n2 = preparation_notes(t2, {})
    assert len(n2["literal_hints"]) == 1
    assert n2["literal_hints"][0]["line"] == 52


def test_fstring_fragment_label():
    code = 'a = "معامله\\u200cای"\nb = f"دکمه\\u200c{val}"\n'
    notes = preparation_notes(make_task(), {"scripts/app.py": {"content": code}})
    assert len(notes["literal_hints"]) == 2
    assert notes["literal_hints"][0]["is_fragment"] is False
    assert notes["literal_hints"][1]["is_fragment"] is True


@pytest.mark.parametrize("path,should_hint", [
    ("scripts/service.py", True),
    ("tests/test_service.py", False),
    ("tests/unit/test_foo.py", False),
    ("scripts/service_test.py", False),
])
def test_source_test_exclusion(path, should_hint):
    code = 'val = "معامله\\u200cای"\n'
    notes = preparation_notes(make_task(), {path: {"content": code}})
    assert (len(notes["literal_hints"]) == 1) == should_hint


def test_blocker_ids_in_all_locations_and_missing_warning():
    id1 = "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
    id2 = "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN"
    id3 = "DR-001"
    id4 = "DR-002"

    t = make_task(
        goal=f"Fix {id1}",
        reqs=[f"Must address {id2}"],
        excerpts=[{"path": "scripts/app.py", "ranges": [{"start": 1, "end": 2, "text": f"# {id4}"}]}],
    )
    s = {"scripts/app.py": {"content": f"# Ref: {id3}"}}
    notes = preparation_notes(t, s)
    assert notes["exact_blocker_ids"] == [id1, id2, id3, id4]
    assert "MISSING_EXACT_BLOCKER_ID" not in notes["warnings"]

    t_missing = make_task(goal="Task requires exact blocker remediation")
    notes_missing = preparation_notes(t_missing, {})
    assert notes_missing["exact_blocker_ids"] == []
    assert "MISSING_EXACT_BLOCKER_ID" in notes_missing["warnings"]


def test_malformed_syntax_overlong_warnings():
    n_syntax = preparation_notes(make_task(), {"scripts/bad.py": {"content": "def (:"}})
    assert "SOURCE_BLOCK_OMITTED" in n_syntax["warnings"]

    overlong = "# " + ("A" * 160005) + "\n"
    n_over = preparation_notes(make_task(), {"scripts/big.py": {"content": overlong}})
    assert "SOURCE_BLOCK_OMITTED" in n_over["warnings"]


def test_immutability_determinism_and_cap():
    lines = [f'v{i} = "معامله\\u200c{i}"' for i in range(25)]
    content = "\n".join(lines)
    sources = {"scripts/app.py": {"content": content}}
    task = make_task(goal="Check P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE")

    task_copy = copy.deepcopy(task)
    sources_copy = copy.deepcopy(sources)

    n1 = preparation_notes(task, sources)
    n2 = preparation_notes(task, sources)

    assert task == task_copy
    assert sources == sources_copy
    assert n1 == n2

    assert n1["schema_version"] == 1
    assert len(n1["literal_hints"]) == 16
    assert n1["omitted_literal_count"] == 9
    assert len(json.dumps(n1).encode("utf-8")) <= 8192

    for h in n1["literal_hints"]:
        assert set(h.keys()) == {"path", "line", "json_literal", "is_fragment"}



def test_many_blocker_ids_output_cap():
    goal = " ".join("DR-" + str(i) for i in range(2000))
    notes = preparation_notes({"goal": goal}, {})
    assert len(notes["exact_blocker_ids"]) <= 32
    assert "BLOCKER_IDS_OMITTED" in notes["warnings"]
    assert len(json.dumps(notes).encode("utf-8")) <= 8192
    assert notes["schema_version"] == 1


def test_large_literal_set_and_bookkeeping_cap():
    zwnj = chr(0x200c)
    nl = chr(10)
    content = nl.join(
        f"v_{i} = " + json.dumps("معامله" + zwnj + str(i), ensure_ascii=True)
        for i in range(600)
    )
    notes = preparation_notes(make_task(), {"scripts/big_literals.py": {"content": content}})
    assert "SOURCE_BLOCK_OMITTED" in notes["warnings"]
    assert len(notes["literal_hints"]) <= 16
    assert len(json.dumps(notes).encode("utf-8")) <= 8192
    assert notes["schema_version"] == 1


def test_deep_ast_recursion_handling():
    code = "x = " + " + ".join(["a"] * 1200) + chr(10)
    notes = preparation_notes(make_task(), {"scripts/deep.py": {"content": code}})
    assert notes["schema_version"] == 1
    assert len(json.dumps(notes).encode("utf-8")) <= 8192
    lit = json.dumps("معامله" + chr(0x200c) + "ای", ensure_ascii=True)
    code_cf = "x = " + " + ".join([lit] * 1200) + chr(10)
    n_cf = preparation_notes(make_task(), {"scripts/deep_cf.py": {"content": code_cf}})
    assert n_cf["schema_version"] == 1 and len(n_cf["literal_hints"]) == 1
    assert len(json.dumps(n_cf).encode("utf-8")) <= 8192


def test_named_sqlalchemy_models_emit_required_type_and_enum_storage_hints():
    model = '''
import enum
from sqlalchemy import Column, DateTime, Enum, Integer, String
class State(str, enum.Enum):
    READY = "ready"
class Ledger:
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(Enum(State), nullable=False, default=State.READY)
    value_state = Column(Enum(State, values_callable=lambda cls: [x.value for x in cls]), nullable=False)
'''
    task = make_task(
        goal="Persist a Ledger database row in sqlite.",
        reqs=["Construct Ledger with a valid expires_at and inspect its state."],
    )
    notes = preparation_notes(task, {"models/ledger.py": {"content": model}})
    hints = notes["dependency_context"]["model_contract_hints"]
    by_field = {item["field"]: item for item in hints if item["model"] == "Ledger"}

    assert by_field["name"]["required_without_declared_default"] is True
    assert by_field["expires_at"]["column_type"] == "DateTime(timezone=True)"
    assert by_field["expires_at"]["required_without_declared_default"] is True
    assert by_field["state"]["enum_database_representation"] == "member_name"
    assert by_field["value_state"]["enum_database_representation"] == "value"
    assert by_field["value_state"]["required_without_declared_default"] is True
