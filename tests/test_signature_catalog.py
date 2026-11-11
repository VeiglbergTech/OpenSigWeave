import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.modes import SignatureMode
from database import Base
from models import DomainDB, SignatureDB, SignatureVariantDB, SignatureVersionDB
from signature_catalog import (
    SignatureNotFoundError,
    add_signature_variant,
    add_signature_version,
    create_signature,
    update_signature,
    update_signature_variant,
)
from signature_storage import SignatureFileStore


class SignatureCatalogTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SignatureFileStore(self.tempdir.name)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_create_signature_persists_metadata_variant_and_html_file(self):
        signature, version, variant = create_signature(
            self.session,
            self.store,
            name="Default signature",
            domain_id=None,
            mode=SignatureMode.AUTO,
            html_content="<p>Hello {{ first_name }}</p>",
            weight=2,
            created_by="admin@example.org",
        )

        self.assertEqual(signature.scope, "GLOBAL")
        self.assertEqual(signature.mode, "AUTO")
        self.assertEqual(version.version_number, 1)
        self.assertEqual(variant.pool_name, "AUTO")
        self.assertEqual(variant.weight, 2)
        self.assertEqual(
            self.store.read(signature.name, uuid.UUID(hex=version.id), version.sha256),
            "<p>Hello {{ first_name }}</p>",
        )

    def test_create_signature_rejects_blank_name(self):
        with self.assertRaises(ValueError):
            create_signature(
                self.session, self.store, name="   ", domain_id=None,
                mode=SignatureMode.AUTO, html_content="<p>Signature</p>",
            )

    def test_create_signature_can_start_inactive(self):
        signature, _, _ = create_signature(
            self.session, self.store, name="Inactive", domain_id=None,
            mode=SignatureMode.AUTO, html_content="<p>Inactive</p>",
            is_active=False,
        )
        self.assertFalse(signature.is_active)

    def test_new_version_increments_version_number(self):
        signature, first, _ = create_signature(
            self.session,
            self.store,
            name="Versioned",
            domain_id=None,
            mode=SignatureMode.PROFESSIONAL,
            html_content="<p>Version 1</p>",
        )
        second = add_signature_version(
            self.session,
            self.store,
            signature.id,
            "<p>Version 2</p>",
        )
        self.assertEqual(first.version_number, 1)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(
            self.store.read(signature.name, uuid.UUID(hex=second.id), second.sha256),
            "<p>Version 2</p>",
        )

    def test_add_variant_requires_matching_mode(self):
        signature, _, _ = create_signature(
            self.session,
            self.store,
            name="Professional",
            domain_id=None,
            mode=SignatureMode.PROFESSIONAL,
            html_content="<p>Professional</p>",
        )
        with self.assertRaises(ValueError):
            add_signature_variant(
                self.session,
                signature.id,
                pool_name=SignatureMode.AUTO,
            )

    def test_missing_signature_raises_not_found(self):
        with self.assertRaises(SignatureNotFoundError):
            add_signature_version(
                self.session,
                self.store,
                uuid.uuid4().hex,
                "<p>Not found</p>",
            )

    def test_update_signature_metadata_and_deactivate(self):
        signature, _, _ = create_signature(
            self.session, self.store, name="Original", domain_id=None,
            mode=SignatureMode.AUTO, html_content="<p>Signature</p>",
        )
        domain = DomainDB(domain_name="example.org")
        self.session.add(domain)
        self.session.commit()

        updated = update_signature(
            self.session, self.store, signature.id, name="Renamed", domain_id=domain.id,
            is_active=False,
        )
        self.assertEqual(updated.name, "Renamed")
        self.assertEqual(updated.scope, "DOMAIN")
        self.assertEqual(updated.domain_id, domain.id)
        self.assertFalse(updated.is_active)

    def test_update_variant_weight_and_enabled_state(self):
        _, _, variant = create_signature(
            self.session, self.store, name="Variant", domain_id=None,
            mode=SignatureMode.INTERNAL, html_content="<p>Internal</p>",
        )
        updated = update_signature_variant(
            self.session, variant.id, weight=7, is_enabled=False,
        )
        self.assertEqual(updated.weight, 7)
        self.assertFalse(updated.is_enabled)

    def test_update_signature_rejects_unknown_domain(self):
        signature, _, _ = create_signature(
            self.session, self.store, name="Original", domain_id=None,
            mode=SignatureMode.AUTO, html_content="<p>Signature</p>",
        )
        with self.assertRaises(ValueError):
            update_signature(
                self.session, signature.id, name="Original", domain_id=999,
                is_active=True,
            )

    def test_failed_database_commit_removes_written_file(self):
        with patch.object(self.session, "commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaises(RuntimeError):
                create_signature(
                    self.session,
                    self.store,
                    name="Rollback",
                    domain_id=None,
                    mode=SignatureMode.AUTO,
                    html_content="<p>Rollback</p>",
                )
        self.assertEqual(list(Path(self.tempdir.name).glob("*.html")), [])
        self.assertEqual(self.session.execute(select(SignatureDB)).scalars().all(), [])

    def test_signature_html_is_sanitized_before_storage(self):
        signature, version, _ = create_signature(
            self.session,
            self.store,
            name="Untrusted template",
            domain_id=None,
            mode=SignatureMode.AUTO,
            html_content=(
                '<p style="color: red; position: fixed" onclick="alert(1)">'
                '{{ first_name }}'
                '<img src="https://example.org/logo.png" onerror="alert(1)">'
                '<a href="javascript:alert(1)">link</a>'
                '<script>alert(1)</script>'
                '</p>'
            ),
        )
        stored = self.store.read(signature.name, uuid.UUID(hex=version.id), version.sha256)
        self.assertIn("{{ first_name }}", stored)
        self.assertIn('src="https://example.org/logo.png"', stored)
        self.assertNotIn("onclick", stored)
        self.assertNotIn("onerror", stored)
        self.assertNotIn("javascript:", stored)
        self.assertNotIn("<script", stored)
        self.assertNotIn("position", stored)

    def test_new_signature_versions_are_sanitized_too(self):
        signature, _, _ = create_signature(
            self.session, self.store, name="Versioned", domain_id=None,
            mode=SignatureMode.PROFESSIONAL, html_content="<p>Version 1</p>",
        )
        version = add_signature_version(
            self.session, self.store, signature.id,
            '<div onmouseover="alert(1)">Version 2<script>bad()</script></div>',
        )
        stored = self.store.read(uuid.UUID(hex=version.id), version.sha256)
        self.assertIn("Version 2", stored)
        self.assertNotIn("onmouseover", stored)
        self.assertNotIn("<script", stored)


if __name__ == "__main__":
    unittest.main()
