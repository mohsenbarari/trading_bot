"""Standalone unit tests for pure materialize_replacements helper."""
from __future__ import annotations

import unittest

from scripts import two_site_proposal_runner as runner


class TestMaterializeReplacements(unittest.TestCase):
    """Exhaustive tests for two_site_proposal_runner.materialize_replacements."""

    def test_single_replacement(self) -> None:
        """Verify single replacement at various positions."""
        # Middle of text
        original = "The quick brown fox jumps over the lazy dog."
        replacements = [{"old": "brown fox", "new": "red panda"}]
        expected = "The quick red panda jumps over the lazy dog."
        self.assertEqual(
            runner.materialize_replacements(original, replacements),
            expected,
        )

        # At start of text
        self.assertEqual(
            runner.materialize_replacements("START middle end", [{"old": "START", "new": "BEGIN"}]),
            "BEGIN middle end",
        )

        # At end of text
        self.assertEqual(
            runner.materialize_replacements("start middle END", [{"old": "END", "new": "FINISH"}]),
            "start middle FINISH",
        )

        # Entire text
        self.assertEqual(
            runner.materialize_replacements("sole", [{"old": "sole", "new": "multiple"}]),
            "multiple",
        )

    def test_deletion(self) -> None:
        """Verify replacement where new is empty string acts as deletion."""
        original = "keep delete_me keep"
        replacements = [{"old": " delete_me", "new": ""}]
        self.assertEqual(
            runner.materialize_replacements(original, replacements),
            "keep keep",
        )

        # Deletion at start
        self.assertEqual(
            runner.materialize_replacements("PREFIX body", [{"old": "PREFIX ", "new": ""}]),
            "body",
        )

        # Deletion at end
        self.assertEqual(
            runner.materialize_replacements("body SUFFIX", [{"old": " SUFFIX", "new": ""}]),
            "body",
        )

    def test_disjoint_replacements_forward_and_reverse_order(self) -> None:
        """Verify disjoint replacements yield identical output regardless of input order."""
        original = "part_one separator part_two separator part_three"
        rep_one = {"old": "part_one", "new": "FIRST"}
        rep_two = {"old": "part_two", "new": "SECOND"}
        rep_three = {"old": "part_three", "new": "THIRD"}
        expected = "FIRST separator SECOND separator THIRD"

        forward_two = [rep_one, rep_three]
        reverse_two = [rep_three, rep_one]
        out_fwd = runner.materialize_replacements(original, forward_two)
        out_rev = runner.materialize_replacements(original, reverse_two)
        self.assertEqual(out_fwd, "FIRST separator part_two separator THIRD")
        self.assertEqual(out_rev, "FIRST separator part_two separator THIRD")
        self.assertEqual(out_fwd, out_rev)

        all_permutations = [
            [rep_one, rep_two, rep_three],
            [rep_three, rep_two, rep_one],
            [rep_two, rep_one, rep_three],
            [rep_three, rep_one, rep_two],
        ]
        for perm in all_permutations:
            with self.subTest(perm=perm):
                self.assertEqual(
                    runner.materialize_replacements(original, perm),
                    expected,
                )

    def test_persian_zwnj_and_prefix_suffix_preservation(self) -> None:
        """Verify exact preservation of Persian zero-width non-joiners and prefix/suffix bytes."""
        prefix = "پیش\u200cنویس نامه\u200cها برای بررسی: "
        old_target = "نسخه\u200cی قدیمی سامانه"
        new_target = "نسخه\u200cی جدید و به\u200cروزرسانی\u200cشده"
        suffix = " طبق دستورالعمل\u200cهای سازمانی و کتاب\u200cها.\n"

        original = f"{prefix}{old_target}{suffix}"
        expected = f"{prefix}{new_target}{suffix}"

        self.assertIn("\u200c", prefix)
        self.assertIn("\u200c", suffix)

        result = runner.materialize_replacements(
            original, [{"old": old_target, "new": new_target}]
        )
        self.assertEqual(result, expected)
        self.assertEqual(result.encode("utf-8"), expected.encode("utf-8"))
        self.assertEqual(result[:len(prefix)], prefix)
        self.assertEqual(result[len(result) - len(suffix):], suffix)
        self.assertEqual(result[:len(prefix)].encode("utf-8"), prefix.encode("utf-8"))
        self.assertEqual(
            result[len(result) - len(suffix):].encode("utf-8"),
            suffix.encode("utf-8"),
        )
        self.assertEqual(result[:len(prefix)].count("\u200c"), prefix.count("\u200c"))
        self.assertEqual(
            result[len(result) - len(suffix):].count("\u200c"),
            suffix.count("\u200c"),
        )

    def test_malformed_replacements_table(self) -> None:
        """Table-test all malformed replacement error conditions."""
        original = "alpha bravo charlie delta echo"

        over_max_replacements = [
            {"old": f"k_{i}", "new": f"v_{i}"}
            for i in range(runner.MAX_REPLACEMENTS + 1)
        ]

        test_cases = [
            ("non_list_string", original, "not a list", "replacements must be a list"),
            ("non_list_dict", original, {"old": "alpha", "new": "a"}, "replacements must be a list"),
            ("non_list_int", original, 12345, "replacements must be a list"),
            ("non_list_none", original, None, "replacements must be a list"),
            ("non_list_tuple", original, ({"old": "alpha", "new": "a"},), "replacements must be a list"),
            ("zero_items", original, [], f"replacements count must be between 1 and {runner.MAX_REPLACEMENTS}"),
            ("more_than_max", original, over_max_replacements, f"replacements count must be between 1 and {runner.MAX_REPLACEMENTS}"),
            ("non_dict_string_item", original, ["invalid item"], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("non_dict_int_item", original, [42], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("non_dict_list_item", original, [["old", "new"]], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("non_dict_none_item", original, [None], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("missing_old_key", original, [{"new": "a"}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("missing_new_key", original, [{"old": "alpha"}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("empty_keys_dict", original, [{}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("extra_key", original, [{"old": "alpha", "new": "a", "extra": "x"}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("wrong_keys", original, [{"from": "alpha", "to": "a"}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("case_sensitive_keys", original, [{"Old": "alpha", "New": "a"}], "replacement item must be a dict with exactly 'old' and 'new'"),
            ("empty_string_old", original, [{"old": "", "new": "a"}], "replacement old must be a nonempty string"),
            ("none_old", original, [{"old": None, "new": "a"}], "replacement old must be a nonempty string"),
            ("int_old", original, [{"old": 100, "new": "a"}], "replacement old must be a nonempty string"),
            ("bytes_old", original, [{"old": b"alpha", "new": "a"}], "replacement old must be a nonempty string"),
            ("none_new", original, [{"old": "alpha", "new": None}], "replacement new must be a string"),
            ("int_new", original, [{"old": "alpha", "new": 100}], "replacement new must be a string"),
            ("list_new", original, [{"old": "alpha", "new": ["a"]}], "replacement new must be a string"),
            ("bytes_new", original, [{"old": "alpha", "new": b"a"}], "replacement new must be a string"),
            ("unchanged_old_new", original, [{"old": "alpha", "new": "alpha"}], "replacement old and new must differ"),
            ("absent_old", original, [{"old": "foxtrot", "new": "f"}], "replacement target not found in original: 'foxtrot'"),
            ("duplicated_occurrence_of_old", "repeat word and repeat word", [{"old": "repeat word", "new": "single"}], "replacement target occurs more than once in original: 'repeat word'"),
            ("overlapping_spans_forward", "abcdefgh", [{"old": "abcd", "new": "1"}, {"old": "cdef", "new": "2"}], "replacement spans overlap"),
            ("overlapping_spans_reverse", "abcdefgh", [{"old": "cdef", "new": "2"}, {"old": "abcd", "new": "1"}], "replacement spans overlap"),
            ("contained_overlapping_span", "abcdefgh", [{"old": "abcdef", "new": "1"}, {"old": "bcd", "new": "2"}], "replacement spans overlap"),
            ("identical_span_repeated", "abcdefgh", [{"old": "cdef", "new": "1"}, {"old": "cdef", "new": "2"}], "replacement spans overlap"),
            ("cascading_only_old_forward", "hello cat", [{"old": "cat", "new": "dog"}, {"old": "dog", "new": "bird"}], "replacement target not found in original: 'dog'"),
            ("cascading_only_old_reverse", "hello cat", [{"old": "dog", "new": "bird"}, {"old": "cat", "new": "dog"}], "replacement target not found in original: 'dog'"),
        ]

        for label, src, reps, expected_fragment in test_cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(runner.ProposalError, expected_fragment):
                    runner.materialize_replacements(src, reps)

    def test_malformed_original(self) -> None:
        """Verify original must be a string."""
        for bad_original in [None, 123, b"not a string", ["text"], {"text": "val"}]:
            with self.subTest(bad_original=type(bad_original).__name__):
                with self.assertRaisesRegex(runner.ProposalError, "original text must be a string"):
                    runner.materialize_replacements(bad_original, [{"old": "a", "new": "b"}])

    def test_aggregate_utf8_byte_boundary(self) -> None:
        """Verify aggregate UTF-8 byte boundary: accepts exactly MAX_REPLACEMENT_BYTES, rejects +1."""
        old_ascii = "X"
        len_old_bytes = len(old_ascii.encode("utf-8"))
        exact_new_ascii = "Y" * (runner.MAX_REPLACEMENT_BYTES - len_old_bytes)
        over_new_ascii = exact_new_ascii + "Z"

        original = f"before {old_ascii} after"
        self.assertLess(len(original.encode("utf-8")), runner.MAX_CONTEXT)

        # Exact boundary accepts
        accepted_res = runner.materialize_replacements(
            original,
            [{"old": old_ascii, "new": exact_new_ascii}],
        )
        self.assertEqual(accepted_res, f"before {exact_new_ascii} after")

        # One byte over rejects
        with self.assertRaisesRegex(runner.ProposalError, "aggregate replacement UTF-8 bytes exceed limit"):
            runner.materialize_replacements(
                original,
                [{"old": old_ascii, "new": over_new_ascii}],
            )

        # Multi-replacement boundary
        chunk_one_size = 20000
        item_one = {"old": "A", "new": "B" * chunk_one_size}
        bytes_one = len("A".encode("utf-8")) + len(("B" * chunk_one_size).encode("utf-8"))

        remaining_exact = runner.MAX_REPLACEMENT_BYTES - bytes_one
        old_two = "C"
        new_two_exact = "D" * (remaining_exact - len(old_two.encode("utf-8")))
        item_two_exact = {"old": old_two, "new": new_two_exact}

        multi_original = "start A middle C end"
        self.assertLess(len(multi_original.encode("utf-8")), runner.MAX_CONTEXT)

        # Exact boundary across two replacements accepts
        res_multi = runner.materialize_replacements(
            multi_original,
            [item_one, item_two_exact],
        )
        self.assertEqual(
            res_multi,
            f"start {'B' * chunk_one_size} middle {new_two_exact} end",
        )

        # One byte over across two replacements rejects
        item_two_over = {"old": old_two, "new": new_two_exact + "D"}
        with self.assertRaisesRegex(runner.ProposalError, "aggregate replacement UTF-8 bytes exceed limit"):
            runner.materialize_replacements(
                multi_original,
                [item_one, item_two_over],
            )

        # Multi-byte UTF-8 boundary verification
        sh_char = "ش"
        self.assertEqual(len(sh_char.encode("utf-8")), 2)
        exact_new_persian = sh_char * 32767
        self.assertEqual(len(exact_new_persian.encode("utf-8")), 65534)
        persian_orig = f"متن {sh_char} پایان"
        self.assertLess(len(persian_orig.encode("utf-8")), runner.MAX_CONTEXT)

        accepted_persian = runner.materialize_replacements(
            persian_orig,
            [{"old": sh_char, "new": exact_new_persian}],
        )
        self.assertEqual(accepted_persian, f"متن {exact_new_persian} پایان")

        over_new_persian = exact_new_persian + "X"
        with self.assertRaisesRegex(runner.ProposalError, "aggregate replacement UTF-8 bytes exceed limit"):
            runner.materialize_replacements(
                persian_orig,
                [{"old": sh_char, "new": over_new_persian}],
            )


if __name__ == "__main__":
    unittest.main()
