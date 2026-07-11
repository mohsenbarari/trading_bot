"""Versioned canonical identity contract shared by Python and PostgreSQL."""

from __future__ import annotations

import re


REGISTRATION_IDENTITY_CONTRACT = "registration_identity_v1"

# Keep this set immutable for v1 so Python and PostgreSQL do not inherit
# different whitespace behavior from runtimes, collations, or locales.
CANONICAL_EDGE_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d"
    "\u001c\u001d\u001e\u001f\u0020"
    "\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
CANONICAL_EDGE_WHITESPACE_SQL = (
    r"U&'\0009\000A\000B\000C\000D"
    r"\001C\001D\001E\001F\0020"
    r"\0085\00A0\1680"
    r"\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A"
    r"\2028\2029\202F\205F\3000'"
)

CANONICAL_DIGIT_SOURCE = (
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
)
CANONICAL_DIGIT_TARGET = "01234567890123456789"
CANONICAL_DIGIT_SOURCE_SQL = (
    r"U&'\06F0\06F1\06F2\06F3\06F4\06F5\06F6\06F7\06F8\06F9"
    r"\0660\0661\0662\0663\0664\0665\0666\0667\0668\0669'"
)
CANONICAL_DIGIT_TARGET_SQL = "'01234567890123456789'"

ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"

_DIGIT_TRANSLATION = str.maketrans(CANONICAL_DIGIT_SOURCE, CANONICAL_DIGIT_TARGET)
_ASCII_LOWER_TRANSLATION = str.maketrans(ASCII_UPPER, ASCII_LOWER)
_SQL_IDENTIFIER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$"
)


def strip_canonical_identity_whitespace(value: object) -> str:
    return str(value or "").strip(CANONICAL_EDGE_WHITESPACE)


def normalize_persian_numerals(value: str) -> str:
    if not value:
        return value
    return str(value).translate(_DIGIT_TRANSLATION)


def normalize_account_name(value: object) -> str:
    normalized = normalize_persian_numerals(
        strip_canonical_identity_whitespace(value)
    )
    # Product account names use English/Persian letters. ASCII-only folding is
    # deterministic across Python and every PostgreSQL collation.
    return normalized.translate(_ASCII_LOWER_TRANSLATION)


def normalize_mobile_number(value: object) -> str:
    return normalize_persian_numerals(strip_canonical_identity_whitespace(value))


def _checked_sql_identifier(column_name: str) -> str:
    if not _SQL_IDENTIFIER.fullmatch(column_name):
        raise ValueError("canonical identity SQL requires a simple or qualified column identifier")
    return column_name


def canonical_account_name_sql(column_name: str) -> str:
    column = _checked_sql_identifier(column_name)
    return (
        f"translate(translate(btrim({column}, {CANONICAL_EDGE_WHITESPACE_SQL}), "
        f"{CANONICAL_DIGIT_SOURCE_SQL}, {CANONICAL_DIGIT_TARGET_SQL}), "
        f"'{ASCII_UPPER}', '{ASCII_LOWER}')"
    )


def canonical_mobile_number_sql(column_name: str) -> str:
    column = _checked_sql_identifier(column_name)
    return (
        f"translate(btrim({column}, {CANONICAL_EDGE_WHITESPACE_SQL}), "
        f"{CANONICAL_DIGIT_SOURCE_SQL}, {CANONICAL_DIGIT_TARGET_SQL})"
    )


NORMALIZED_ACCOUNT_NAME_SQL = canonical_account_name_sql("account_name")
NORMALIZED_MOBILE_NUMBER_SQL = canonical_mobile_number_sql("mobile_number")
