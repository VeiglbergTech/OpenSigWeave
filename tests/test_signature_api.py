"""Runtime tests for administrator signature-catalog API handlers."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("OIDC_DISCOVERY_URL", "https://idp.example/application/o/opensigweave/.well-known/openid-configuration")
os.environ.setdefault("OIDC_CLIENT_ID", "opensigweave-test")
os.environ.setdefault("OIDC_CLIENT_SECRET", "opensigweave-test-client-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from core.modes import SignatureMode
from database import Base
from signature_storage import SignatureFileStore


class SignatureCatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_store = main.signature_file_store
        main.signature_file_store = SignatureFileStore(self.tempdir.name)
        self.admin_request = SimpleNamespace(
            session={"user": {"groups": ["signature-admins"], "email": "admin@example.org"}}
        )
        self.user_request = SimpleNamespace(
            session={"user": {"groups": ["signature-users"], "email": "user@example.org"}}
        )

    def tearDown(self):
        main.signature_file_store = self.previous_store
        self.db.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_admin_can_create_update_and_manage_signature_variants(self):
        payload = main.SignatureCreateRequest(
            name="Corporate",
            domain_id=None,
            mode="PROFESSIONAL",
            html_content="<p>Hello {{ first_name }}</p>",
            weight=2,
            is_active=False,
        )
        created = main.create_signature_endpoint(payload, self.admin_request, self.db)
        signature = self.db.get(main.SignatureDB, created["id"])
        self.assertIsNotNone(signature)
        self.assertFalse(signature.is_active)

        updated = main.update_signature_endpoint(
            created["id"],
            main.SignatureUpdateRequest(name="Corporate Active", domain_id=None, is_active=True),
            self.admin_request,
            self.db,
        )
        self.assertEqual(updated["name"], "Corporate Active")
        self.assertTrue(updated["is_active"])

        variants = main.list_signature_variants(created["id"], self.admin_request, self.db)
        self.assertEqual(len(variants), 1)
        changed = main.update_signature_variant_endpoint(
            variants[0]["id"],
            main.SignatureVariantUpdateRequest(weight=5, is_enabled=False),
            self.admin_request,
            self.db,
        )
        self.assertEqual(changed["weight"], 5)
        self.assertFalse(changed["is_enabled"])

    def test_admin_can_add_and_list_versions(self):
        created = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Versioned",
                mode="AUTO",
                html_content="<p>Version 1</p>",
            ),
            self.admin_request,
            self.db,
        )
        version = main.create_signature_version_endpoint(
            created["id"],
            main.SignatureVersionCreateRequest(html_content="<p>Version 2</p>"),
            self.admin_request,
            self.db,
        )
        versions = main.list_signature_versions(created["id"], self.admin_request, self.db)
        self.assertEqual(version["version_number"], 2)
        self.assertEqual([item["version_number"] for item in versions], [2, 1])

    def test_non_admin_cannot_mutate_signature_catalog(self):
        payload = main.SignatureCreateRequest(
            name="Denied", mode="AUTO", html_content="<p>Denied</p>"
        )
        with self.assertRaises(main.HTTPException) as raised:
            main.create_signature_endpoint(payload, self.user_request, self.db)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
