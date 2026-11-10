import unittest

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
            claims={"department": "Sales"},
            attributes={"title": "Account Manager"},
        )

    def test_higher_priority_rule_wins(self):
        default = SignatureRule("default", 1, (), "AUTO")
        sales = SignatureRule(
            "sales",
            20,
            (RuleCondition("group_contains", "sales"),),
            "PROFESSIONAL",
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
            "PROFESSIONAL",
        )
        mismatch = SignatureRule(
            "sender-engineering",
            100,
            (RuleCondition("claim_equals", "Engineering", key="department"),),
            "INTERNAL",
        )
        self.assertEqual(evaluate_rules([mismatch, matching], self.context), matching)

    def test_disabled_rules_are_ignored(self):
        disabled = SignatureRule("disabled", 100, (), "NONE", enabled=False)
        default = SignatureRule("default", 1, (), "AUTO")
        self.assertEqual(evaluate_rules([disabled, default], self.context), default)

    def test_equal_priorities_use_stable_rule_id_order(self):
        second = SignatureRule("z-rule", 10, (), "INTERNAL")
        first = SignatureRule("a-rule", 10, (), "PROFESSIONAL")
        self.assertEqual(evaluate_rules([second, first], self.context), first)

    def test_unknown_condition_is_rejected(self):
        invalid = SignatureRule(
            "invalid",
            1,
            (RuleCondition("arbitrary_python", "x"),),
            "AUTO",
        )
        with self.assertRaises(ValueError):
            evaluate_rules([invalid], self.context)


if __name__ == "__main__":
    unittest.main()
