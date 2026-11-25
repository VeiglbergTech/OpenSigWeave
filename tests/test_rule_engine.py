import unittest
from datetime import datetime, timezone

from core.rules import (
    RuleCondition,
    RuleContext,
    SignatureRule,
    evaluate_rules,
)


class SignatureRuleTests(unittest.TestCase):
    def setUp(self):
        self.context = RuleContext(
            sender="Alice@Example.org",
            domain="example.org",
            groups=frozenset({"Sales", "Employees"}),
            claims={"department": "Sales", "is_active": True},
            attributes={"title": "Account Manager", "departments": ["Sales", "Operations"]},
        )

    def test_higher_priority_rule_wins(self):
        default = SignatureRule("default", 1, (), "SIGNATURE")
        sales = SignatureRule(
            "sales",
            20,
            (RuleCondition("group_contains", "sales"),),
            "GROUP",
        )
        self.assertEqual(evaluate_rules([default, sales], self.context), sales)

    def test_conditions_are_combined_with_and(self):
        matching = SignatureRule(
            "sender-sales",
            10,
            (
                RuleCondition("sender_equals", "alice@example.org"),
                RuleCondition("claim_equals", "Sales", key="department"),
            ),
            "GROUP",
        )
        mismatch = SignatureRule(
            "sender-engineering",
            100,
            (RuleCondition("claim_equals", "Engineering", key="department"),),
            "NONE",
        )
        self.assertEqual(evaluate_rules([mismatch, matching], self.context), matching)

    def test_disabled_rules_are_ignored(self):
        disabled = SignatureRule("disabled", 100, (), "NONE", enabled=False)
        default = SignatureRule("default", 1, (), "SIGNATURE")
        self.assertEqual(evaluate_rules([disabled, default], self.context), default)

    def test_equal_priorities_use_stable_rule_id_order(self):
        second = SignatureRule("z-rule", 10, (), "NONE")
        first = SignatureRule("a-rule", 10, (), "GROUP")
        self.assertEqual(evaluate_rules([second, first], self.context), first)


    def test_list_valued_attributes_match_any_value(self):
        rule = SignatureRule(
            "department-list",
            10,
            (RuleCondition("attribute_equals", "Sales", key="departments"),),
            "GROUP",
        )
        self.assertEqual(evaluate_rules([rule], self.context), rule)


    def test_oidc_claim_condition_matches_case_insensitively(self):
        rule = SignatureRule(
            "active-user",
            10,
            (RuleCondition("claim_equals", "true", key="is_active"),),
            "GROUP",
        )
        self.assertEqual(evaluate_rules([rule], self.context), rule)

    def test_sender_and_domain_not_equals_are_case_insensitive(self):
        for kind, equal_value, different_value in (
            ("sender_not_equals", "ALICE@example.org", "bob@example.org"),
            ("domain_not_equals", "EXAMPLE.ORG", "example.net"),
        ):
            with self.subTest(kind=kind):
                equal_rule = SignatureRule("equal", 10, (RuleCondition(kind, equal_value),), "NONE")
                other_rule = SignatureRule("other", 10, (RuleCondition(kind, different_value),), "NONE")
                self.assertIsNone(evaluate_rules([equal_rule], self.context))
                self.assertEqual(evaluate_rules([other_rule], self.context), other_rule)

    def test_claim_and_attribute_not_equals_handle_lists_and_missing_values(self):
        cases = (
            ("claim_not_equals", "department", "sales", "engineering"),
            ("attribute_not_equals", "departments", "OPERATIONS", "engineering"),
            ("attribute_not_equals", "title", "account manager", "Developer"),
        )
        for kind, key, equal_value, different_value in cases:
            with self.subTest(kind=kind, key=key):
                equal_rule = SignatureRule("equal", 10, (RuleCondition(kind, equal_value, key=key),), "NONE")
                different_rule = SignatureRule("different", 10, (RuleCondition(kind, different_value, key=key),), "NONE")
                missing_rule = SignatureRule("missing", 10, (RuleCondition(kind, different_value, key="absent"),), "NONE")
                self.assertIsNone(evaluate_rules([equal_rule], self.context))
                self.assertEqual(evaluate_rules([different_rule], self.context), different_rule)
                self.assertIsNone(evaluate_rules([missing_rule], self.context))

        for missing in (None, [], [None], "", ["", None], ["  "]):
            with self.subTest(missing=missing):
                context = RuleContext(sender="a@test.org", domain="test.org", attributes={"departments": missing})
                rule = SignatureRule("empty", 10, (RuleCondition("attribute_not_equals", "sales", key="departments"),), "NONE")
                self.assertIsNone(evaluate_rules([rule], context))

    def test_negative_condition_preserves_and_and_priority(self):
        fallback = SignatureRule("fallback", 1, (), "SIGNATURE")
        conditional = SignatureRule("conditional", 20, (
            RuleCondition("domain_equals", "example.org"),
            RuleCondition("sender_not_equals", "bob@example.org"),
        ), "GROUP")
        self.assertEqual(evaluate_rules([fallback, conditional], self.context), conditional)
        failed = SignatureRule("failed", 30, (
            RuleCondition("domain_equals", "example.org"),
            RuleCondition("sender_not_equals", "alice@example.org"),
        ), "NONE")
        self.assertEqual(evaluate_rules([fallback, failed], self.context), fallback)


    def test_active_timed_rule_overrides_higher_priority_always_rule(self):
        default = SignatureRule("default", 100, (), "SIGNATURE")
        timed = SignatureRule("christmas", -100, (
            RuleCondition("time_window", "2026-12-27T00:00:00+00:00", key="2026-12-01T00:00:00+00:00"),
            RuleCondition("domain_equals", "example.org"),
        ), "GROUP")
        during = RuleContext(sender="alice@example.org", domain="example.org",
                             now=datetime(2026, 12, 24, tzinfo=timezone.utc))
        before = RuleContext(sender="alice@example.org", domain="example.org",
                             now=datetime(2026, 11, 24, tzinfo=timezone.utc))
        after = RuleContext(sender="alice@example.org", domain="example.org",
                            now=datetime(2026, 12, 27, tzinfo=timezone.utc))
        self.assertEqual(evaluate_rules([default, timed], during), timed)
        self.assertEqual(evaluate_rules([default, timed], before), default)
        self.assertEqual(evaluate_rules([default, timed], after), default)

    def test_invalid_time_window_never_matches(self):
        fallback = SignatureRule("fallback", 1, (), "NONE")
        invalid = SignatureRule("bad-window", 100, (
            RuleCondition("time_window", "not-a-time", key="broken"),
        ), "SIGNATURE")
        self.assertEqual(evaluate_rules([invalid, fallback], self.context), fallback)

    def test_unknown_condition_is_rejected(self):
        invalid = SignatureRule(
            "invalid",
            1,
            (RuleCondition("arbitrary_python", "x"),),
            "SIGNATURE",
        )
        with self.assertRaises(ValueError):
            evaluate_rules([invalid], self.context)


if __name__ == "__main__":
    unittest.main()
