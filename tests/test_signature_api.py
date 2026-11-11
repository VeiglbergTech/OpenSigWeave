"""Runtime tests for administrator signature-catalog API handlers."""
import asyncio
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("OIDC_DISCOVERY_URL", "https://idp.example/application/o/opensigweave/.well-known/openid-configuration")
os.environ.setdefault("OIDC_REDIRECT_URI", "https://app.example.org/auth")
os.environ.setdefault("OIDC_CLIENT_ID", "opensigweave-test")
os.environ.setdefault("OIDC_CLIENT_SECRET", "opensigweave-test-client-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response

import main
from core.modes import SignatureMode
from database import Base
from signature_storage import SignatureFileStore
from identity.attributes import IdentityAttributes


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

    def test_domain_policy_disables_existing_personal_overrides(self):
        domain = main.DomainDB(
            domain_name="example.org",
            is_active=True,
            allow_overrides=False,
            template_html="<p>Domain {{ first_name }}</p>",
        )
        self.db.add(domain)
        self.db.add(main.UserOverrideDB(
            user_email="user@example.org",
            html_content="<p>Personal override</p>",
            is_disabled=False,
        ))
        self.db.commit()

        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode=None,
                db=self.db,
            ))

        self.assertIn("Domain Alice", result["html"])
        self.assertNotIn("Personal override", result["html"])

    def test_personal_override_email_matching_is_case_insensitive(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=True,
            template_html="<p>Domain default</p>",
        ))
        self.db.commit()
        uppercase_request = SimpleNamespace(
            session={"user": {"groups": ["signature-users"], "email": "User@EXAMPLE.ORG"}}
        )
        main.save_my_signature(
            main.OverrideUpdate(html_content="<p>Personal {{ first_name }}</p>"),
            uppercase_request,
            self.db,
        )
        stored = self.db.query(main.UserOverrideDB).one()
        self.assertEqual(stored.user_email, "user@example.org")

        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode=None,
                db=self.db,
            ))
        self.assertIn("Personal Alice", result["html"])

    def test_personal_override_save_respects_domain_policy(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=False,
        ))
        self.db.commit()
        with self.assertRaises(main.HTTPException) as raised:
            main.save_my_signature(
                main.OverrideUpdate(html_content="<p>Bypass</p>"),
                self.user_request,
                self.db,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_user_cannot_delete_administrator_disabled_override(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=True,
        ))
        self.db.add(main.UserOverrideDB(
            user_email="user@example.org",
            html_content="<p>Managed</p>",
            is_disabled=True,
        ))
        self.db.commit()
        with self.assertRaises(main.HTTPException) as raised:
            main.delete_my_signature(self.user_request, self.db)
        self.assertEqual(raised.exception.status_code, 403)
        override = self.db.query(main.UserOverrideDB).filter_by(
            user_email="user@example.org"
        ).one()
        self.assertTrue(override.is_disabled)



    def test_literal_email_lookup_does_not_treat_sql_like_wildcards_as_patterns(self):
        self.db.add_all([
            main.UserOverrideDB(
                user_email="userXname@example.org",
                html_content="<p>Wrong account</p>",
            ),
            main.UserOverrideDB(
                user_email="user_name@example.org",
                html_content="<p>Exact account</p>",
            ),
        ])
        self.db.commit()
        request = SimpleNamespace(
            session={"user": {"groups": ["signature-users"], "email": "user_name@example.org"}}
        )
        result = main.get_my_signature(request, self.db)
        self.assertEqual(result["html_content"], "<p>Exact account</p>")


    def test_request_body_limit_rejects_oversized_payloads(self):
        client = TestClient(main.app)
        response = client.post(
            "/api/my-signature",
            content=b"x" * (1024 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)


    def test_untrusted_host_header_is_rejected(self):
        client = TestClient(main.app, base_url="https://evil.example")
        response = client.get("/")
        self.assertEqual(response.status_code, 400)

    def test_canonical_host_is_accepted(self):
        client = TestClient(main.app, base_url="https://app.example.org")
        response = client.get("/")
        self.assertEqual(response.status_code, 307)

    def test_authenticated_non_member_cannot_use_personal_signature_api(self):
        outsider = SimpleNamespace(
            session={"user": {"groups": ["employees"], "email": "outsider@example.org"}}
        )
        for operation in (
            lambda: main.get_my_signature(outsider, self.db),
            lambda: main.save_my_signature(main.OverrideUpdate(html_content="<p>Denied</p>"), outsider, self.db),
            lambda: main.delete_my_signature(outsider, self.db),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(main.HTTPException) as raised:
                    operation()
                self.assertEqual(raised.exception.status_code, 403)


    def test_personal_override_delete_respects_domain_policy(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=False,
        ))
        self.db.add(main.UserOverrideDB(
            user_email="user@example.org",
            html_content="<p>Existing</p>",
            is_disabled=False,
        ))
        self.db.commit()
        with self.assertRaises(main.HTTPException) as raised:
            main.delete_my_signature(self.user_request, self.db)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self.db.query(main.UserOverrideDB).count(), 1)

    def test_personal_override_html_is_sanitized_before_database_storage(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=True,
        ))
        self.db.commit()
        main.save_my_signature(
            main.OverrideUpdate(html_content='<p onclick="alert(1)">Safe<script>bad()</script></p>'),
            self.user_request,
            self.db,
        )
        stored = self.db.query(main.UserOverrideDB).one().html_content
        self.assertIn("Safe", stored)
        self.assertNotIn("onclick", stored)
        self.assertNotIn("<script", stored)

    def test_domain_template_is_sanitized_before_storage(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.update_domain(
            domain.id,
            main.DomainUpdate(
                is_active=True, allow_overrides=True, inject_on_replies=False,
                trim_whitespace=True, strip_device_signatures=False,
                template_html='<div onmouseover="bad()">Domain<script>bad()</script></div>',
            ),
            self.admin_request,
            self.db,
        )
        self.db.refresh(domain)
        self.assertIn("Domain", domain.template_html)
        self.assertNotIn("onmouseover", domain.template_html)
        self.assertNotIn("<script", domain.template_html)

    def test_non_admin_cannot_mutate_signature_catalog(self):
        payload = main.SignatureCreateRequest(
            name="Denied", mode="AUTO", html_content="<p>Denied</p>"
        )
        with self.assertRaises(main.HTTPException) as raised:
            main.create_signature_endpoint(payload, self.user_request, self.db)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
