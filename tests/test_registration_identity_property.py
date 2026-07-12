from __future__ import annotations

import string
import unittest

from hypothesis import given, strategies as st

from core.registration_identity import (
    CANONICAL_EDGE_WHITESPACE,
    normalize_account_name,
    normalize_mobile_number,
)


PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


class RegistrationIdentityPropertyTests(unittest.TestCase):
    @given(st.text(max_size=80))
    def test_normalization_is_idempotent(self, value: str):
        self.assertEqual(
            normalize_account_name(normalize_account_name(value)),
            normalize_account_name(value),
        )
        self.assertEqual(
            normalize_mobile_number(normalize_mobile_number(value)),
            normalize_mobile_number(value),
        )

    @given(
        st.text(alphabet=string.digits, min_size=1, max_size=24),
        st.sampled_from((PERSIAN_DIGITS, ARABIC_DIGITS)),
        st.text(alphabet=CANONICAL_EDGE_WHITESPACE, max_size=4),
        st.text(alphabet=CANONICAL_EDGE_WHITESPACE, max_size=4),
    )
    def test_mobile_digit_scripts_and_canonical_edge_space_converge(
        self,
        ascii_digits: str,
        digit_script: str,
        left: str,
        right: str,
    ):
        translated = ascii_digits.translate(str.maketrans(string.digits, digit_script))
        self.assertEqual(normalize_mobile_number(left + translated + right), ascii_digits)

    @given(st.text(alphabet=string.ascii_letters + string.digits, max_size=50))
    def test_account_ascii_case_converges_without_changing_digits(self, value: str):
        self.assertEqual(normalize_account_name(value.upper()), value.lower())


if __name__ == "__main__":
    unittest.main()
