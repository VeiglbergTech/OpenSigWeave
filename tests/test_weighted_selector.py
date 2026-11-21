import unittest
from unittest.mock import patch

from core.weighted_selector import WeightedCandidate, select_weighted_no_repeat


class WeightedSelectorTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            WeightedCandidate("one", "<p>One</p>"),
            WeightedCandidate("two", "<p>Two</p>"),
            WeightedCandidate("three", "<p>Three</p>"),
        ]

    def test_previous_candidate_is_excluded_when_alternatives_exist(self):
        with patch("core.weighted_selector.secrets.randbelow", return_value=0) as choose:
            selected = select_weighted_no_repeat(self.candidates, previous_id="one")
        self.assertEqual(selected.id, "two")
        self.assertEqual(choose.call_args.args[0], 2)

    def test_only_candidate_can_repeat_when_no_alternative_exists(self):
        with patch("core.weighted_selector.secrets.randbelow", return_value=0):
            selected = select_weighted_no_repeat([self.candidates[0]], previous_id="one")
        self.assertEqual(selected.id, "one")

    def test_disabled_candidates_are_never_selected(self):
        candidates = [
            WeightedCandidate("disabled", "x", enabled=False),
            self.candidates[1],
        ]
        with patch("core.weighted_selector.secrets.randbelow", return_value=0):
            selected = select_weighted_no_repeat(candidates)
        self.assertEqual(selected.id, "two")

    def test_weights_affect_random_selection(self):
        weighted = [
            WeightedCandidate("light", "L", weight=1),
            WeightedCandidate("heavy", "H", weight=3),
        ]
        with patch("core.weighted_selector.secrets.randbelow", return_value=2):
            selected = select_weighted_no_repeat(weighted)
        self.assertEqual(selected.id, "heavy")

    def test_no_enabled_candidates_returns_none(self):
        self.assertIsNone(
            select_weighted_no_repeat([WeightedCandidate("off", "", enabled=False)])
        )


if __name__ == "__main__":
    unittest.main()
