"""Strict structural reader for the three admitted glibc -t merge producers.

No test outcome is accepted or relabelled here.  Callers retain their own
minimum-test, FAIL/XPASS, CVE, build-return-code and qualification policies.
"""
from collections import Counter
from hashlib import sha256
import re

MAX_BYTES = 16 * 1024**2
MAX_ROWS = 131072
BANNER = "\t\t=== glibc tests ==="
# Original Linux merge argv, not build dependency order or a caller allowlist.
SECTIONS = tuple((
    ". argp assert catgets conform csu ctype debug dirent dlfcn elf gmon "
    "gnulib hesiod iconv iconvdata inet intl io libio locale localedata login "
    "malloc manual math mathvec misc nis nptl nptl_db nscd nss po posix "
    "resolv resource rt setjmp signal socket stdio-common stdlib string "
    "sunrpc support sysvipc termios time timezone wcsmbs wctype"
).split())
_ROW = re.compile(r"(PASS|FAIL|XFAIL|XPASS|UNSUPPORTED): ([A-Za-z0-9_./+@:-]+)")


def _need(ok, code):
    if ok is not True:
        raise RuntimeError(code)


def parse_merge_ledger(raw):
    """Return ordered untouched status/name rows from one complete vendor sum.

    The admitted scripts emit one banner, then exactly one empty separator and
    Running header per original section.  A section may legitimately be empty.
    There is no footer.  ERROR/UNRESOLVED or any other line cannot disappear.
    """
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "vendor_ledger_bound")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise RuntimeError("vendor_ledger_encoding") from None
    _need(text.endswith("\n") and "\r" not in text, "vendor_ledger_framing")
    lines = text.split("\n")
    lines.pop()  # Exactly the final LF emitted by the original echo/cat producer.
    _need(lines and lines[0] == BANNER, "vendor_ledger_framing")
    offset = 1
    rows = []
    row_sections = []
    names = set()
    section_counts = []
    for section in SECTIONS:
        _need(
            offset + 1 < len(lines)
            and lines[offset] == ""
            and lines[offset + 1] == "Running " + section + " ...",
            "vendor_ledger_section",
        )
        offset += 2
        section_start = len(rows)
        while offset < len(lines) and lines[offset] != "":
            match = _ROW.fullmatch(lines[offset])
            _need(match is not None, "vendor_ledger_shape")
            state, name = match.groups()
            _need(name not in names, "vendor_ledger_duplicate")
            _need(len(rows) < MAX_ROWS, "vendor_ledger_row_bound")
            names.add(name)
            rows.append((state, name))
            row_sections.append(section)
            offset += 1
        section_counts.append((section, len(rows) - section_start))
    _need(offset == len(lines), "vendor_ledger_trailing")
    _need(len(rows) > 0, "vendor_ledger_empty")
    return {
        "schema": "strict_original_glibc_merge_ledger/1",
        "rows": tuple(rows),
        "row_sections": tuple(row_sections),
        "counts": dict(sorted(Counter(state for state, _ in rows).items())),
        "tests": len(rows),
        "sections": SECTIONS,
        "section_counts": tuple(section_counts),
        "raw": {"bytes": len(raw), "sha256": sha256(raw).hexdigest()},
    }
