import unittest
from unittest.mock import patch

from core.modes import SignatureMode, resolve_mode
from core.variant_selector import SignatureVariant, select_variant


class SignatureModeTests(unittest.TestCase):
    def test_default_mode_is_auto(self):
        self.assertEqual(resolve_mode(None), SignatureMode.AUTO)

    def test_explicit_mode_is_case_insensitive(self):
        self.assertEqual(resolve_mode("professional"), SignatureMode.PROFESSIONAL)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_mode("administrator")


class VariantSelectorTests(unittest.TestCase):
    def setUp(self):
        self.variants = [
            SignatureVariant("one", "<p>One</p>"),
            SignatureVariant("two", "<p>Two</p>"),
            SignatureVariant("three", "<p>Three</p>"),
        ]

    def test_previous_variant_is_excluded_when_alternatives_exist(self):
        with patch("core.variant_selector.secrets.randbelow", return_value=0) as choose:
            selected = select_variant(self.variants, previous_variant_id="one")
        self.assertEqual(selected.id, "two")
        self.assertEqual(choose.call_args.args[0], 2)

    def test_only_variant_can_repeat_when_no_alternative_exists(self):
        only = [self.variants[0]]
        with patch("core.variant_selector.secrets.randbelow", return_value=0):
            selected = select_variant(only, previous_variant_id="one")
        self.assertEqual(selected.id, "one")

    def test_disabled_variants_are_never_selected(self):
        candidates = [
            SignatureVariant("disabled", "x", enabled=False),
            self.variants[1],
        ]
        with patch("core.variant_selector.secrets.randbelow", return_value=0):
            selected = select_variant(candidates)
        self.assertEqual(selected.id, "two")

    def test_variant_weights_affect_random_selection(self):
        weighted = [
            SignatureVariant("light", "L", weight=1),
            SignatureVariant("heavy", "H", weight=3),
        ]
        with patch("core.variant_selector.secrets.randbelow", return_value=2):
            selected = select_variant(weighted)
        self.assertEqual(selected.id, "heavy")

    def test_no_enabled_variants_returns_none(self):
        self.assertIsNone(select_variant([SignatureVariant("off", "", enabled=False)]))


if __name__ == "__main__":
    unittest.main()
