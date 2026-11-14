import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.rule_repository import load_signature_rules
from core.rules import RuleContext, evaluate_rules
from database import Base
from models import SignatureDB, SignatureRuleConditionDB, SignatureRuleDB


class RuleRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()
        self.signature = SignatureDB(
            id="signature-professional",
            name="Professional",
            scope="GLOBAL",
            mode="PROFESSIONAL",
            is_active=True,
        )
        self.session.add(self.signature)
        self.session.flush()

        self.session.add_all([
            SignatureRuleDB(
                id="z-default",
                name="Fallback",
                priority=1,
                action_mode="AUTO",
                is_enabled=True,
            ),
            SignatureRuleDB(
                id="a-sales",
                name="Sales team",
                priority=20,
                action_mode="PROFESSIONAL",
                signature_id=self.signature.id,
                is_enabled=True,
            ),
            SignatureRuleDB(
                id="disabled-rule",
                name="Disabled",
                priority=100,
                action_mode="INTERNAL",
                is_enabled=False,
            ),
        ])
        self.session.flush()
        self.session.add_all([
            SignatureRuleConditionDB(
                rule_id="a-sales",
                kind="group_contains",
                key="",
                value="sales",
            ),
            SignatureRuleConditionDB(
                rule_id="a-sales",
                kind="attribute_equals",
                key="department",
                value="Sales",
            ),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_loads_persisted_conditions_and_action_target(self):
        rules = load_signature_rules(self.session)
        context = RuleContext(
            sender="alice@example.org",
            domain="example.org",
            groups=frozenset({"Sales"}),
            attributes={"department": "Sales"},
        )
        selected = evaluate_rules(rules, context)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, "a-sales")
        self.assertEqual(selected.action, "PROFESSIONAL")
        self.assertEqual(selected.signature_id, self.signature.id)
        self.assertEqual(len(selected.conditions), 2)

    def test_disabled_high_priority_rule_does_not_win(self):
        rules = load_signature_rules(self.session)
        context = RuleContext(sender="alice@example.org", domain="example.org")
        selected = evaluate_rules(rules, context)
        self.assertEqual(selected.id, "z-default")

    def test_empty_database_returns_empty_tuple(self):
        self.session.query(SignatureRuleConditionDB).delete()
        self.session.query(SignatureRuleDB).delete()
        self.session.commit()
        self.assertEqual(load_signature_rules(self.session), ())


if __name__ == "__main__":
    unittest.main()
