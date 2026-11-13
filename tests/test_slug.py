"""Unit tests for shared slug normalization."""
import unittest

from core.slug import slugify


class SlugifyTests(unittest.TestCase):
    def test_normalizes_case_and_whitespace(self):
        self.assertEqual(slugify("  Sales Team  "), "sales-team")

    def test_collapses_repeated_separators(self):
        self.assertEqual(slugify("Sales___Team / North"), "sales-team-north")

    def test_strips_leading_and_trailing_separators(self):
        self.assertEqual(slugify("---Sales Team---"), "sales-team")

    def test_empty_or_non_ascii_only_input_returns_empty_slug(self):
        self.assertEqual(slugify("   "), "")
        self.assertEqual(slugify("東京"), "")

    def test_casefolds_before_normalization(self):
        self.assertEqual(slugify("STRAẞE"), "stra-e")


if __name__ == "__main__":
    unittest.main()
