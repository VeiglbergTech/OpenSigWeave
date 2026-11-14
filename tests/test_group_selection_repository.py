import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.group_selection_repository import get_group_preview_signature, select_and_record_group_signature
from database import Base
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureGroupDB,
    SignatureGroupDomainDB, SignatureGroupMemberDB,
)


class GroupSelectionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()
        self.domain = DomainDB(domain_name="example.org", is_active=True)
        self.group = SignatureGroupDB(id=uuid.uuid4().hex, name="Corporate", slug="corporate", is_active=True)
        self.one = SignatureDB(id=uuid.uuid4().hex, name="One", scope="GLOBAL", mode="AUTO", is_active=True)
        self.two = SignatureDB(id=uuid.uuid4().hex, name="Two", scope="GLOBAL", mode="AUTO", is_active=True)
        self.session.add_all([self.domain, self.group, self.one, self.two])
        self.session.flush()
        self.session.add_all([
            SignatureGroupMemberDB(group_id=self.group.id, signature_id=self.one.id, weight=1, is_enabled=True),
            SignatureGroupMemberDB(group_id=self.group.id, signature_id=self.two.id, weight=3, is_enabled=True),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_weighted_selection_does_not_repeat_for_stable_principal(self):
        with patch("core.variant_selector.secrets.randbelow", return_value=0):
            expected_first = min([self.one.id, self.two.id])
            expected_second = max([self.one.id, self.two.id])
            first = select_and_record_group_signature(self.session, "idp-a:authentik:42", self.group.id, self.domain.id)
            self.assertEqual(first.id, expected_first)
            self.session.commit()
            second = select_and_record_group_signature(self.session, "idp-a:authentik:42", self.group.id, self.domain.id)
            self.assertEqual(second.id, expected_second)
            self.session.commit()
        state = self.session.execute(select(GroupSelectionStateDB)).scalar_one()
        self.assertEqual(state.last_signature_id, expected_second)

    def test_email_is_not_accepted_as_stable_principal(self):
        with self.assertRaises(ValueError):
            select_and_record_group_signature(self.session, "alice@example.org", self.group.id, self.domain.id)

    def test_group_domain_scope_is_enforced(self):
        other = DomainDB(domain_name="other.example", is_active=True)
        self.session.add(other)
        self.session.flush()
        self.session.add(SignatureGroupDomainDB(group_id=self.group.id, domain_id=other.id))
        self.session.commit()
        self.assertIsNone(select_and_record_group_signature(self.session, "idp-a:user:42", self.group.id, self.domain.id))

    def test_disabled_member_is_never_selected(self):
        self.session.query(SignatureGroupMemberDB).filter_by(
            group_id=self.group.id, signature_id=self.one.id
        ).update({"is_enabled": False})
        self.session.commit()
        selected = select_and_record_group_signature(self.session, "idp-a:user:99", self.group.id, self.domain.id)
        self.assertEqual(selected.id, self.two.id)


    def test_preview_is_deterministic_and_does_not_change_rotation_state(self):
        first = get_group_preview_signature(self.session, self.group.id, self.domain.id)
        second = get_group_preview_signature(self.session, self.group.id, self.domain.id)
        self.assertIsNotNone(first)
        self.assertEqual(first.id, min(self.one.id, self.two.id))
        self.assertEqual(second.id, first.id)
        self.assertEqual(self.session.query(GroupSelectionStateDB).count(), 0)

    def test_preview_respects_disabled_members_and_domain_scope(self):
        self.session.query(SignatureGroupMemberDB).filter_by(
            group_id=self.group.id, signature_id=self.one.id
        ).update({"is_enabled": False})
        self.session.commit()
        preview = get_group_preview_signature(self.session, self.group.id, self.domain.id)
        self.assertEqual(preview.id, self.two.id)

        other = DomainDB(domain_name="other.example", is_active=True)
        self.session.add(other)
        self.session.flush()
        self.session.add(SignatureGroupDomainDB(group_id=self.group.id, domain_id=other.id))
        self.session.commit()
        self.assertIsNone(get_group_preview_signature(self.session, self.group.id, self.domain.id))


if __name__ == "__main__":
    unittest.main()
