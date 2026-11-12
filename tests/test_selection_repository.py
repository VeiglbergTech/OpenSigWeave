import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.selection_repository import select_and_record_variant
from database import Base
from models import DomainDB, SelectionStateDB, SignatureDB, SignatureDomainDB, SignatureVariantDB


class SelectionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()
        signature = SignatureDB(
            id=uuid.uuid4().hex,
            name="Default pool",
            scope="DOMAIN",
            mode="AUTO",
            is_active=True,
        )
        self.one = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="AUTO",
            weight=1,
            is_enabled=True,
        )
        self.two = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="AUTO",
            weight=1,
            is_enabled=True,
        )
        self.session.add_all([signature, self.one, self.two])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_sender_does_not_repeat_previous_variant(self):
        expected_first, expected_second = sorted([self.one, self.two], key=lambda item: item.id)
        with patch("core.variant_selector.secrets.randbelow", return_value=0):
            first = select_and_record_variant(
                self.session, "Alice@Example.org", "auto"
            )
            self.assertEqual(first.id, expected_first.id)
            self.session.commit()

            second = select_and_record_variant(
                self.session, "alice@example.org", "AUTO"
            )
            self.assertEqual(second.id, expected_second.id)
            self.session.commit()

        state = self.session.execute(
            select(SelectionStateDB).where(
                SelectionStateDB.sender_email == "alice@example.org",
                SelectionStateDB.pool_name == "AUTO",
            )
        ).scalar_one()
        self.assertEqual(state.last_variant_id, expected_second.id)

    def test_selection_state_is_scoped_per_sender(self):
        expected_first = min([self.one, self.two], key=lambda item: item.id)
        with patch("core.variant_selector.secrets.randbelow", return_value=0):
            selected = select_and_record_variant(
                self.session, "bob@example.org", "AUTO"
            )
        self.assertEqual(selected.id, expected_first.id)
        self.session.commit()
        states = self.session.execute(select(SelectionStateDB)).scalars().all()
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].sender_email, "bob@example.org")

    def test_empty_candidate_pool_returns_none(self):
        self.assertIsNone(select_and_record_variant(self.session, "alice@example.org", "INTERNAL"))
        self.assertEqual(self.session.execute(select(SelectionStateDB)).scalars().all(), [])

    def test_signature_can_be_assigned_to_multiple_domains(self):
        domains = [
            DomainDB(domain_name="one.example", is_active=True),
            DomainDB(domain_name="two.example", is_active=True),
            DomainDB(domain_name="three.example", is_active=True),
        ]
        self.session.add_all(domains)
        self.session.flush()
        self.session.add_all([
            SignatureDomainDB(signature_id=self.one.signature_id, domain_id=domains[0].id),
            SignatureDomainDB(signature_id=self.one.signature_id, domain_id=domains[1].id),
        ])
        self.session.commit()

        selected_one = select_and_record_variant(
            self.session, "one@example.org", "AUTO", domain_id=domains[0].id
        )
        self.assertEqual(selected_one.id, self.one.id)
        self.session.commit()

        selected_two = select_and_record_variant(
            self.session, "two@example.org", "AUTO", domain_id=domains[1].id
        )
        self.assertEqual(selected_two.signature_id, self.one.signature_id)
        self.session.commit()

        selected_three = select_and_record_variant(
            self.session, "three@example.org", "AUTO", domain_id=domains[2].id
        )
        self.assertIsNone(selected_three)

    def test_rule_target_limits_selection_to_signature(self):
        targeted_signature = SignatureDB(
            id=uuid.uuid4().hex,
            name="Targeted professional",
            scope="DOMAIN",
            mode="AUTO",
            is_active=True,
        )
        targeted_variant = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=targeted_signature.id,
            pool_name="AUTO",
            weight=1,
            is_enabled=True,
        )
        self.session.add_all([targeted_signature, targeted_variant])
        self.session.commit()

        selected = select_and_record_variant(
            self.session,
            "rules@example.org",
            "AUTO",
            signature_id=targeted_signature.id,
        )
        self.assertEqual(selected.id, targeted_variant.id)
        self.session.rollback()


if __name__ == "__main__":
    unittest.main()
