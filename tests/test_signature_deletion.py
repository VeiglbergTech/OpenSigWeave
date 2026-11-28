"""Regression tests for safe catalog signature deletion and reference cleanup."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureDomainDB,
    SignatureGroupDB, SignatureGroupMemberDB, SignatureRuleDB,
    SignatureScheduleDB, SignatureVersionDB, UserOverrideDB,
)
from signature_catalog import SignatureNotFoundError, create_signature
from signature_deletion import delete_signature_with_cleanup, inspect_signature_deletion
from signature_storage import SignatureFileStore, SignatureStorageError


class SignatureDeletionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.temp = tempfile.TemporaryDirectory()
        self.store = SignatureFileStore(self.temp.name)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def signature(self, name="Disposable"):
        return create_signature(
            self.db, self.store, name=name,
            html_content="<p>" + name + "</p>",
        )[0]

    def group(self, name, *signatures):
        group = SignatureGroupDB(
            id=uuid.uuid4().hex, name=name, slug=name.lower().replace(" ", "-"),
            is_active=True,
        )
        self.db.add(group)
        self.db.flush()
        for signature in signatures:
            self.db.add(SignatureGroupMemberDB(
                group_id=group.id, signature_id=signature.id,
                weight=2, is_enabled=True,
            ))
        self.db.commit()
        return group

    def rule(self, name, signature=None, group=None):
        rule = SignatureRuleDB(
            id=uuid.uuid4().hex, name=name, priority=1,
            action_mode="GROUP" if group else "SIGNATURE",
            signature_id=signature.id if signature else None,
            group_id=group.id if group else None,
            is_enabled=True,
        )
        self.db.add(rule)
        self.db.commit()
        return rule

    def test_unused_signature_is_removed_from_db_and_disk(self):
        signature = self.signature()
        folder = Path(self.temp.name) / "signatur" / signature.name
        self.assertTrue(folder.exists())
        self.assertEqual(inspect_signature_deletion(self.db, signature.id)["version_count"], 1)
        deleted = delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertEqual(deleted["status"], "deleted")
        self.assertFalse(deleted["files_cleanup_pending"])
        self.assertFalse(folder.exists())
        self.assertEqual(self.db.query(SignatureDB).count(), 0)
        self.assertEqual(self.db.query(SignatureVersionDB).count(), 0)

    def test_one_group_is_kept_but_disabled_if_last_member_deleted(self):
        signature = self.signature()
        group = self.group("One member", signature)
        self.db.add(GroupSelectionStateDB(
            principal_id="principal", group_id=group.id,
            last_signature_id=signature.id,
        ))
        self.db.commit()
        impact = inspect_signature_deletion(self.db, signature.id)
        self.assertTrue(impact["groups"][0]["will_be_empty"])
        self.assertTrue(impact["groups"][0]["will_disable"])
        delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertIsNotNone(self.db.get(SignatureGroupDB, group.id))
        self.assertFalse(self.db.get(SignatureGroupDB, group.id).is_active)
        self.assertEqual(self.db.query(SignatureGroupMemberDB).count(), 0)
        self.assertIsNone(self.db.get(GroupSelectionStateDB, ("principal", group.id)).last_signature_id)

    def test_multiple_groups_keep_unrelated_members_and_rotation(self):
        removed = self.signature()
        retained = self.signature("Retained")
        first = self.group("First", removed, retained)
        second = self.group("Second", removed)
        related_rule = self.rule("Group rule", group=second)
        unaffected_rule = self.rule("Retained rule", signature=retained)
        impact = inspect_signature_deletion(self.db, removed.id)
        self.assertEqual(len(impact["groups"]), 2)
        delete_signature_with_cleanup(self.db, self.store, removed.id)
        self.assertTrue(self.db.get(SignatureGroupDB, first.id).is_active)
        self.assertFalse(self.db.get(SignatureGroupDB, second.id).is_active)
        self.assertEqual(
            [member.signature_id for member in self.db.query(SignatureGroupMemberDB).all()],
            [retained.id],
        )
        self.assertFalse(self.db.get(SignatureRuleDB, related_rule.id).is_enabled)
        self.assertTrue(self.db.get(SignatureRuleDB, unaffected_rule.id).is_enabled)
        self.assertEqual(self.db.get(SignatureRuleDB, unaffected_rule.id).signature_id, retained.id)

    def test_direct_rules_are_preserved_disabled_without_target(self):
        signature = self.signature()
        rule = self.rule("Direct rule", signature=signature)
        impact = inspect_signature_deletion(self.db, signature.id)
        self.assertEqual([row["name"] for row in impact["rules"]], ["Direct rule"])
        delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertIsNone(self.db.get(SignatureRuleDB, rule.id).signature_id)
        self.assertFalse(self.db.get(SignatureRuleDB, rule.id).is_enabled)
        self.assertEqual(self.db.get(SignatureRuleDB, rule.id).name, "Direct rule")

    def test_personal_override_exact_marker_is_removed_but_other_html_remains(self):
        signature = self.signature()
        from hashlib import sha256
        code = sha256(signature.name.casefold().encode()).hexdigest()[:4]
        marker = f"<---[{signature.name}]--[{code}]--->"
        different = "<---[Unrelated]--[1234]--->"
        self.db.add(UserOverrideDB(
            user_email="editor@example.org",
            html_content="<p>Hello</p>" + marker + different,
            html_content_source="<p>Hello</p>" + marker + different,
            is_disabled=False,
        ))
        self.db.commit()
        self.assertEqual(inspect_signature_deletion(self.db, signature.id)["personal_override_count"], 1)
        delete_signature_with_cleanup(self.db, self.store, signature.id)
        override = self.db.query(UserOverrideDB).one()
        self.assertNotIn(marker, override.html_content)
        self.assertNotIn(marker, override.html_content_source)
        self.assertIn(different, override.html_content)
        self.assertIn("<p>Hello</p>", override.html_content_source)

    def test_legacy_direct_schedules_must_be_removed_due_to_target_constraint(self):
        signature = self.signature()
        now = datetime.now(timezone.utc)
        schedule = SignatureScheduleDB(
            id=uuid.uuid4().hex, name="Old schedule", target_type="SIGNATURE",
            signature_id=signature.id, starts_at=now,
            ends_at=now + timedelta(days=1), is_enabled=True,
        )
        self.db.add(schedule)
        self.db.commit()
        self.assertEqual(inspect_signature_deletion(self.db, signature.id)["schedules"][0]["effect"], "removed")
        delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertIsNone(self.db.get(SignatureScheduleDB, schedule.id))

    def test_empty_group_schedule_is_disabled_but_preserved(self):
        signature = self.signature()
        group = self.group("Scheduled", signature)
        now = datetime.now(timezone.utc)
        schedule = SignatureScheduleDB(
            id=uuid.uuid4().hex, name="Group schedule", target_type="GROUP",
            group_id=group.id, starts_at=now,
            ends_at=now + timedelta(days=1), is_enabled=True,
        )
        self.db.add(schedule)
        self.db.commit()
        self.assertEqual(inspect_signature_deletion(self.db, signature.id)["schedules"][0]["effect"], "disabled")
        delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertFalse(self.db.get(SignatureScheduleDB, schedule.id).is_enabled)
        self.assertEqual(self.db.get(SignatureScheduleDB, schedule.id).group_id, group.id)

    def test_required_domain_warning_is_reported_before_deletion(self):
        domain = DomainDB(domain_name="required.example.org", is_active=True, signature_required=True)
        self.db.add(domain)
        self.db.commit()
        signature = self.signature()
        self.rule("Mandatory", signature=signature)
        impact = inspect_signature_deletion(self.db, signature.id)
        self.assertEqual(impact["mandatory_domains"][0]["name"], domain.domain_name)

    def test_failed_database_commit_restores_original_html_and_references(self):
        signature = self.signature()
        group = self.group("Restored", signature)
        folder = Path(self.temp.name) / "signatur" / signature.name
        with patch.object(self.db, "commit", side_effect=RuntimeError("test transaction failure")):
            with self.assertRaises(RuntimeError):
                delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertTrue(folder.is_dir())
        self.assertTrue((folder / "signature.html").is_file())
        self.assertIsNotNone(self.db.get(SignatureDB, signature.id))
        self.assertEqual(self.db.query(SignatureGroupMemberDB).count(), 1)
        self.assertTrue(self.db.get(SignatureGroupDB, group.id).is_active)

    def test_stage_failure_does_not_change_database(self):
        signature = self.signature()
        with patch.object(self.store, "stage_signature_delete",
                          side_effect=SignatureStorageError("simulated failure")):
            with self.assertRaises(SignatureStorageError):
                delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertIsNotNone(self.db.get(SignatureDB, signature.id))
        self.assertEqual(self.db.query(SignatureVersionDB).count(), 1)

    def test_postcommit_cleanup_failure_is_reported_and_retryable(self):
        signature = self.signature()
        with patch.object(self.store, "purge_staged_signature",
                          side_effect=SignatureStorageError("simulated failure")):
            result = delete_signature_with_cleanup(self.db, self.store, signature.id)
        self.assertTrue(result["files_cleanup_pending"])
        self.assertIsNone(self.db.get(SignatureDB, signature.id))
        quarantined = list(self.store.signatures_root.glob(".deleted-*"))
        self.assertEqual(len(quarantined), 1)
        self.store.purge_staged_signature(quarantined[0].name)
        self.assertFalse(quarantined[0].exists())

    def test_missing_signature_is_not_deleted(self):
        with self.assertRaises(SignatureNotFoundError):
            delete_signature_with_cleanup(self.db, self.store, "0" * 32)


if __name__ == "__main__":
    unittest.main()
