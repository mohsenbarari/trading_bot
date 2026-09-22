"""Hermetic unit tests for two_site_source_excerpts module."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import two_site_proposal_runner as runner
from scripts import two_site_source_excerpts as excerpts


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.two_site_semantic_task_builder.load_active_assignment_scope",
        lambda *args, **kwargs: "stage_a",
    )
    try:
        import two_site_semantic_task_builder
        monkeypatch.setattr(
            two_site_semantic_task_builder,
            "load_active_assignment_scope",
            lambda *args, **kwargs: "stage_a",
        )
    except (ImportError, AttributeError):
        pass

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

    git("init")
    git("config", "user.email", "offline@example.invalid")
    git("config", "user.name", "Offline Fixture")
    (tmp_path / ".gitignore").write_text(".local/\n")
    (tmp_path / "code.py").write_text(
        "import os\n"
        "LIMIT = 2\n"
        "@staticmethod\n"
        "def f(x):\n"
        "    return x + LIMIT\n\n"
        "def unrelated():\n"
        "    return 1\n"
    )
    git("add", "--", ".gitignore", "code.py")
    git("commit", "-m", "initial offline fixture")
    return tmp_path, git


def test_split_physical_lines_delimiters_and_unicode_breaks():
    assert excerpts._split_physical_lines("") == []

    sample = "line1\r\nline2\rline3\nline4"
    lines = excerpts._split_physical_lines(sample)
    assert lines == ["line1\r\n", "line2\r", "line3\n", "line4"]
    assert "".join(lines) == sample

    assert excerpts._split_physical_lines("a\nb\n") == ["a\n", "b\n"]
    assert excerpts._split_physical_lines("a\nb") == ["a\n", "b"]

    unicode_sample = "literal = 'part1\u2028part2\u2029part3'\nfinal = 1\r\n"
    u_lines = excerpts._split_physical_lines(unicode_sample)
    assert len(u_lines) == 2
    assert u_lines[0] == "literal = 'part1\u2028part2\u2029part3'\n"
    assert u_lines[1] == "final = 1\r\n"
    assert "".join(u_lines) == unicode_sample


def test_split_physical_lines_deterministic_linear_work():
    class CountedStr(str):
        def __init__(self, value: str):
            super().__init__()
            self.find_calls = 0
            self.scanned_chars = 0

        def find(self, sub, start=0, end=None):
            self.find_calls += 1
            length = len(self)
            actual_end = length if end is None else min(end, length)
            actual_start = max(0, start)
            if actual_end > actual_start:
                self.scanned_chars += (actual_end - actual_start)
            return super().find(sub, start, end)

    raw_lf = "a\n" * 1000
    counted_lf = CountedStr(raw_lf)

    pos = 0
    while pos < len(counted_lf):
        lf = counted_lf.find("\n", pos)
        counted_lf.find("\r", pos)
        pos = lf + 1

    old_absent_scanned = counted_lf.scanned_chars
    assert old_absent_scanned > 8 * len(raw_lf)

    new_counted = CountedStr(raw_lf)
    result = excerpts._split_physical_lines(new_counted)
    assert len(result) == 1000
    assert "".join(result) == raw_lf
    assert new_counted.scanned_chars <= 8 * len(raw_lf)
    assert new_counted.scanned_chars == 0

    raw_cr = "b\r" * 1000
    pos = 0
    old_cr = CountedStr(raw_cr)
    while pos < len(old_cr):
        cr = old_cr.find("\r", pos)
        old_cr.find("\n", pos)
        pos = cr + 1
    assert old_cr.scanned_chars > 8 * len(raw_cr)

    new_cr_counted = CountedStr(raw_cr)
    cr_result = excerpts._split_physical_lines(new_cr_counted)
    assert len(cr_result) == 1000
    assert "".join(cr_result) == raw_cr
    assert new_cr_counted.scanned_chars <= 8 * len(raw_cr)
    assert new_cr_counted.scanned_chars == 0


def test_extract_bundle_real_unicode_source_over_160k(repo):
    root, git = repo
    large_path = root / "large_unicode.py"

    header = (
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n\n"
        "PERSIAN_TEXT = 'سلام\u200cدنیا و \u200dترکیب'\n"
        "BIDI_LITERAL = 'abc\u200e\u200fdef'\n"
        "PARAGRAPH_SEP = 'hello\u2028world\u2029'\n\n"
        "@staticmethod\n"
        "def decorated_fn(x: int) -> int:\n"
        "    # Persian comment: \u200cکار می\u200cکند\n"
        "    return x + 42\n\n"
    )
    padding_comment = "# " + ("x" * 78 + "\n")
    content = header + (padding_comment * 2100)
    data = content.encode("utf-8")
    assert len(data) > 160000

    large_path.write_bytes(data)
    git("add", "--", "large_unicode.py")
    git("commit", "-m", "add large unicode source")

    records, report = excerpts.extract_bundle(
        root,
        [{"path": "large_unicode.py", "symbols": ["PERSIAN_TEXT", "BIDI_LITERAL", "PARAGRAPH_SEP", "decorated_fn"]}],
    )

    assert report == {
        "source_count": 1,
        "source_bytes": len(data),
        "max_source_bytes": 2000000,
        "max_total_bytes": 8000000,
    }
    assert len(records) == 1
    rec = records[0]
    assert rec["path"] == "large_unicode.py"
    assert rec["sha256"] == runner.sha(data)
    assert rec["symbols"] == ["PERSIAN_TEXT", "BIDI_LITERAL", "PARAGRAPH_SEP", "decorated_fn"]

    combined_text = "".join(r["text"] for r in rec["ranges"])
    assert "import os" in combined_text
    assert "import sys" in combined_text
    assert "@staticmethod" in combined_text
    assert "def decorated_fn" in combined_text
    assert "سلام\u200cدنیا" in combined_text
    assert "\u2028world\u2029" in combined_text


def test_extract_bundle_2m_and_2m1_limits(repo):
    root, git = repo

    prefix_2m = b"x = 1\n"
    pad_2m = b"# " + b"a" * 1999991 + b"\n"
    data_2m = prefix_2m + pad_2m
    assert len(data_2m) == 2000000
    (root / "valid_2m.py").write_bytes(data_2m)

    pad_2m1 = b"# " + b"a" * 1999992 + b"\n"
    data_2m1 = prefix_2m + pad_2m1
    assert len(data_2m1) == 2000001
    (root / "overflow_2m1.py").write_bytes(data_2m1)

    git("add", "--", "valid_2m.py", "overflow_2m1.py")
    git("commit", "-m", "add 2m boundary files")

    records, report = excerpts.extract_bundle(root, [{"path": "valid_2m.py", "symbols": ["x"]}])
    assert report["source_count"] == 1
    assert report["source_bytes"] == 2000000

    with pytest.raises(runner.ProposalError, match="exceeds the safe context budget"):
        excerpts.extract_bundle(root, [{"path": "overflow_2m1.py", "symbols": ["x"]}])


def test_extract_bundle_8m_total_and_abort_before_fifth_reader(repo, monkeypatch):
    root, git = repo

    names = ["f1.py", "f2.py", "f3.py", "f4.py"]
    for i, name in enumerate(names, 1):
        content = f"v{i} = {i}\n".encode("utf-8")
        pad = b"# " + (b"k" * (2000000 - len(content) - 3)) + b"\n"
        data = content + pad
        assert len(data) == 2000000
        (root / name).write_bytes(data)

    (root / "f5.py").write_bytes(b"v5 = 5\n")
    git("add", "--", *names, "f5.py")
    git("commit", "-m", "add 4x2M files plus 5th file")

    calls: list[tuple[str, int]] = []
    orig_bounded = runner.bounded_file_bytes

    def spy_bounded(r, p, limit, label, failure_callback=None):
        calls.append((p.name, limit))
        return orig_bounded(r, p, limit, label, failure_callback)

    monkeypatch.setattr(runner, "bounded_file_bytes", spy_bounded)

    reqs_4 = [{"path": name, "symbols": [f"v{i}"]} for i, name in enumerate(names, 1)]
    records, report = excerpts.extract_bundle(root, reqs_4)
    assert report["source_count"] == 4
    assert report["source_bytes"] == 8000000
    assert len(calls) == 4
    assert all(limit == 2000000 for _, limit in calls)

    calls.clear()
    reqs_5 = reqs_4 + [{"path": "f5.py", "symbols": ["v5"]}]
    with pytest.raises(runner.ProposalError, match="invocation context budget exceeded"):
        excerpts.extract_bundle(root, reqs_5)

    assert len(calls) == 4
    assert [name for name, _ in calls] == names


def test_extract_bundle_positive_remaining_limit_argument(repo, monkeypatch):
    root, git = repo

    names = ["f1.py", "f2.py", "f3.py"]
    for i, name in enumerate(names, 1):
        content = f"v{i} = {i}\n".encode("utf-8")
        pad = b"# " + (b"k" * (2000000 - len(content) - 3)) + b"\n"
        data = content + pad
        assert len(data) == 2000000
        (root / name).write_bytes(data)

    (root / "part_15m.py").write_bytes(b"p = 1\n# " + b"b" * (1500000 - 9) + b"\n")
    (root / "overflow_500k1.py").write_bytes(b"q = 1\n# " + b"c" * (500001 - 9) + b"\n")
    git("add", "--", *names, "part_15m.py", "overflow_500k1.py")
    git("commit", "-m", "add 3x2M, 1.5M and 500k1 files")

    calls: list[tuple[str, int]] = []
    orig_bounded = runner.bounded_file_bytes

    def spy_bounded(r, p, limit, label, failure_callback=None):
        calls.append((p.name, limit))
        return orig_bounded(r, p, limit, label, failure_callback)

    monkeypatch.setattr(runner, "bounded_file_bytes", spy_bounded)

    reqs = [
        {"path": "f1.py", "symbols": ["v1"]},
        {"path": "f2.py", "symbols": ["v2"]},
        {"path": "f3.py", "symbols": ["v3"]},
        {"path": "part_15m.py", "symbols": ["p"]},
        {"path": "overflow_500k1.py", "symbols": ["q"]},
    ]

    with pytest.raises(runner.ProposalError, match="exceeds the safe context budget"):
        excerpts.extract_bundle(root, reqs)

    assert len(calls) == 5
    assert calls[4] == ("overflow_500k1.py", 500000)


def test_extract_bundle_empty_and_validations(repo):
    root, _ = repo

    records, report = excerpts.extract_bundle(root, [])
    assert records == []
    assert report == {
        "source_count": 0,
        "source_bytes": 0,
        "max_source_bytes": 2000000,
        "max_total_bytes": 8000000,
    }

    with pytest.raises(runner.ProposalError, match="must be a list"):
        excerpts.extract_bundle(root, "not a list")  # type: ignore
    with pytest.raises(runner.ProposalError, match="cannot exceed 32"):
        excerpts.extract_bundle(root, [{"path": f"p{i}.py", "symbols": ["x"]} for i in range(33)])

    with pytest.raises(runner.ProposalError, match="exactly 'path' and 'symbols'"):
        excerpts.extract_bundle(root, [{"path": "code.py"}])  # type: ignore
    with pytest.raises(runner.ProposalError, match="must be a string"):
        excerpts.extract_bundle(root, [{"path": 123, "symbols": ["f"]}])  # type: ignore

    with pytest.raises(runner.ProposalError, match="duplicate"):
        excerpts.extract_bundle(root, [
            {"path": "code.py", "symbols": ["f"]},
            {"path": "code.py", "symbols": ["LIMIT"]},
        ])

    with pytest.raises(runner.ProposalError, match="unique Python top-level symbol"):
        excerpts.extract_bundle(root, [{"path": "code.py", "symbols": []}])
    with pytest.raises(runner.ProposalError, match="unique Python top-level symbol"):
        excerpts.extract_bundle(root, [{"path": "code.py", "symbols": ["not an id!"]}])
    with pytest.raises(runner.ProposalError, match="unique Python top-level symbol"):
        excerpts.extract_bundle(root, [{"path": "code.py", "symbols": ["f", "f"]}])

    with pytest.raises(runner.ProposalError, match="missing top-level excerpt symbols"):
        excerpts.extract_bundle(root, [{"path": "code.py", "symbols": ["nonexistent"]}])

    (root / "untracked.py").write_text("u = 1\n")
    with pytest.raises(runner.ProposalError, match="untracked"):
        excerpts.extract_bundle(root, [{"path": "untracked.py", "symbols": ["u"]}])

    (root / "notes.md").write_text("# Notes\n")
    with pytest.raises(runner.ProposalError, match=r"\.py file"):
        excerpts.extract_bundle(root, [{"path": "notes.md", "symbols": ["Notes"]}])

    with pytest.raises(runner.ProposalError):
        excerpts.extract_bundle(root, [{"path": "../code.py", "symbols": ["f"]}])


def test_extract_bundle_symlink_hardlink_credential(repo):
    root, git = repo

    (root / "sym.py").symlink_to(root / "code.py")
    with pytest.raises(runner.ProposalError):
        excerpts.extract_bundle(root, [{"path": "sym.py", "symbols": ["f"]}])
    (root / "sym.py").unlink()

    os.link(root / "code.py", root / "hard.py")
    git("add", "--", "hard.py")
    git("commit", "-m", "add hardlink")
    with pytest.raises(runner.ProposalError, match="hardlink"):
        excerpts.extract_bundle(root, [{"path": "hard.py", "symbols": ["f"]}])

    (root / "secret.py").write_text("API_KEY = 'sk-" + "a" * 25 + "'\n")
    git("add", "--", "secret.py")
    git("commit", "-m", "add secret")
    with pytest.raises(runner.ProposalError, match="credential-like"):
        excerpts.extract_bundle(root, [{"path": "secret.py", "symbols": ["API_KEY"]}])


def test_fresh_isolated_import_proposal_error():
    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"

    code_pkg = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "import scripts.two_site_source_excerpts as mod_pkg\n"
        "import scripts.two_site_proposal_runner as runner_pkg\n"
        "assert mod_pkg.ProposalError is runner_pkg.ProposalError\n"
    )
    res_pkg = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code_pkg],
        capture_output=True,
        text=True,
    )
    assert res_pkg.returncode == 0, f"STDOUT: {res_pkg.stdout}\nSTDERR: {res_pkg.stderr}"

    code_std = (
        "import sys\n"
        f"sys.path.insert(0, {str(scripts_dir)!r})\n"
        "import two_site_source_excerpts as mod_std\n"
        "import two_site_proposal_runner as runner_std\n"
        "assert mod_std.ProposalError is runner_std.ProposalError\n"
    )
    res_std = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code_std],
        capture_output=True,
        text=True,
    )
    assert res_std.returncode == 0, f"STDOUT: {res_std.stdout}\nSTDERR: {res_std.stderr}"
