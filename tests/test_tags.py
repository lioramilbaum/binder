import unittest

from binder.tags import normalize_tags


class NormalizeTagsTests(unittest.TestCase):
    """Tests for tag normalization."""

    def test_whitespace_collapse(self):
        result = normalize_tags(["  hello   world  "])
        self.assertEqual(result, ["hello world"])

    def test_empties_dropped(self):
        result = normalize_tags(["tag1", "  ", "", "tag2"])
        self.assertEqual(result, ["tag1", "tag2"])

    def test_duplicates_removed_case_insensitively(self):
        result = normalize_tags(["Budget", "budget", "BUDGET"])
        self.assertEqual(result, ["Budget"])

    def test_duplicates_keep_first_spelling(self):
        result = normalize_tags(["Budget", "  budget  ", "бюджет"])
        self.assertEqual(result, ["Budget", "бюджет"])

    def test_order_preserved(self):
        result = normalize_tags(["z", "a", "m"])
        self.assertEqual(result, ["z", "a", "m"])

    def test_hebrew_unchanged(self):
        result = normalize_tags(["תקציב", "דרישה"])
        self.assertEqual(result, ["תקציב", "דרישה"])

    def test_hebrew_case_insensitive_duplicate_removal(self):
        result = normalize_tags(["תקציב", "תקציב"])
        self.assertEqual(result, ["תקציב"])


if __name__ == "__main__":
    unittest.main()
