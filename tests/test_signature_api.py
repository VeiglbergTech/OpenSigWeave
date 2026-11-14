"""Runtime tests for administrator signature-catalog API handlers."""
import asyncio
import os
import tempfile
import unittest

from fastapi import HTTPException
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
from models import IdentityCacheDB


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

    def test_admin_component_export_requires_admin_and_returns_private_zip(self):
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Exported signature",
                mode="AUTO",
                html_content="<p>Export payload</p>",
            ),
            self.admin_request,
            self.db,
        )
        response = main.export_admin_components(
            self.admin_request, "signatures,domains", self.db
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "application/zip")
        self.assertTrue(response.body.startswith(b"PK"))
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        with self.assertRaises(HTTPException) as denied:
            main.export_admin_components(self.user_request, "signatures", self.db)
        self.assertEqual(denied.exception.status_code, 403)

    def test_signature_api_supports_multiple_domain_assignments(self):
        first = main.DomainDB(domain_name="first.example", is_active=True)
        second = main.DomainDB(domain_name="second.example", is_active=True)
        self.db.add_all([first, second])
        self.db.commit()
        created = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Shared signature",
                domain_ids=[first.id, second.id],
                mode="AUTO",
                html_content="<p>Shared</p>",
            ),
            self.admin_request,
            self.db,
        )
        listed = main.list_signatures(self.admin_request, self.db)
        item = next(signature for signature in listed if signature["id"] == created["id"])
        self.assertEqual(item["domain_ids"], [first.id, second.id])
        self.assertEqual(item["scope"], "DOMAIN")

        second_signature = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Second shared signature",
                domain_ids=[first.id],
                mode="PROFESSIONAL",
                html_content="<p>Second</p>",
            ),
            self.admin_request,
            self.db,
        )
        first_domain_signatures = [entry for entry in main.list_signatures(self.admin_request, self.db)
                                   if first.id in entry["domain_ids"]]
        self.assertEqual({entry["id"] for entry in first_domain_signatures}, {created["id"], second_signature["id"]})

        updated = main.update_signature_endpoint(
            created["id"],
            main.SignatureUpdateRequest(
                name="Shared signature",
                domain_ids=[second.id],
                is_active=True,
            ),
            self.admin_request,
            self.db,
        )
        self.assertEqual(updated["domain_ids"], [second.id])

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

    def test_mode_selection_reads_name_based_signature_version(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Professional",
                domain_id=None,
                mode="PROFESSIONAL",
                html_content="<p>Selected variant signature</p>",
            ),
            self.admin_request,
            self.db,
        )
        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode="PROFESSIONAL",
                db=self.db,
            ))
        self.assertIn("Selected variant signature", result["html"])

    def test_signature_text_marker_is_used_when_mode_header_is_invalid(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Professional",
                domain_id=None,
                mode="PROFESSIONAL",
                html_content="<p>Named marker signature</p>",
            ),
            self.admin_request,
            self.db,
        )
        code = __import__("hashlib").sha256(b"professional").hexdigest()[:4]
        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode="INVALID-MODE",
                x_signature_name="pROfessional",
                x_signature_code=code.upper(),
                db=self.db,
            ))
        self.assertIn("Named marker signature", result["html"])

    def test_signature_text_marker_uses_unicode_casefold_matching(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Straße",
                domain_id=None,
                mode="PROFESSIONAL",
                html_content="<p>Unicode marker signature</p>",
            ),
            self.admin_request,
            self.db,
        )
        code = __import__("hashlib").sha256("strasse".encode("utf-8")).hexdigest()[:4]
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
                x_signature_name="STRASSE",
                x_signature_code=code,
                db=self.db,
            ))
        self.assertIn("Unicode marker signature", result["html"])

    def test_signature_text_marker_rejects_wrong_four_character_code(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Professional",
                mode="PROFESSIONAL",
                html_content="<p>Must not be selected</p>",
            ),
            self.admin_request,
            self.db,
        )
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
                x_signature_name="Professional",
                x_signature_code="0000",
                db=self.db,
            ))
        self.assertEqual(result["html"], "")

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
        client = TestClient(main.app, base_url="https://app.example.org")
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
        client = TestClient(main.app, base_url="https://app.example.org", follow_redirects=False)
        response = client.get("/")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["strict-transport-security"], "max-age=31536000")
        self.assertEqual(response.headers["x-permitted-cross-domain-policies"], "none")

    def test_oidc_email_verified_claim_must_be_boolean_true(self):
        base_user = {
            "sub": "user-123",
            "email": "user@example.org",
            "groups": ["signature-users"],
            "name": "Test User",
        }
        for claim in ("false", 1, False, None):
            with self.subTest(email_verified=claim):
                request = SimpleNamespace(session={"stale": "session"})
                token = {"userinfo": {**base_user, "email_verified": claim}}
                with patch.object(main.oauth.oidc, "authorize_access_token", new=AsyncMock(return_value=token)):
                    response = asyncio.run(main.auth(request))
                self.assertEqual(response.status_code, 303)
                self.assertIn("authentication_failed", response.headers["location"])
                self.assertEqual(request.session, {})

    def test_oidc_accepts_boolean_true_email_verified_claim(self):
        request = SimpleNamespace(session={"stale": "session"})
        token = {"userinfo": {
            "sub": "user-123",
            "email": "user@example.org",
            "email_verified": True,
            "groups": ["signature-users"],
            "name": "Test User",
        }}
        with patch.object(main.oauth.oidc, "authorize_access_token", new=AsyncMock(return_value=token)):
            response = asyncio.run(main.auth(request))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(request.session["user"]["email_verified"], True)

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

    def test_personal_override_preserves_source_and_stores_sanitized_delivery_html(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True, allow_overrides=True,
        ))
        self.db.commit()
        source = '<!--[if mso]><v:rect>Outlook</v:rect><![endif]--><p onclick="alert(1)">Safe<script>bad()</script></p>'
        main.save_my_signature(
            main.OverrideUpdate(html_content=source),
            self.user_request,
            self.db,
        )
        stored = self.db.query(main.UserOverrideDB).one()
        self.assertEqual(stored.html_content_source, source)
        self.assertIn("Safe", stored.html_content)
        self.assertNotIn("<!--[if mso]", stored.html_content)
        self.assertNotIn("onclick", stored.html_content)
        self.assertNotIn("<script", stored.html_content)
        self.assertEqual(main.get_my_signature(self.user_request, self.db)["html_content"], source)

    def test_domain_template_preserves_source_and_stores_sanitized_delivery_html(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        source = '<!--[if mso]><v:rect>Outlook</v:rect><![endif]--><div onmouseover="bad()">Domain<script>bad()</script></div>'
        main.update_domain(
            domain.id,
            main.DomainUpdate(
                is_active=True, allow_overrides=True, inject_on_replies=False,
                trim_whitespace=True, strip_device_signatures=False,
                template_html=source,
            ),
            self.admin_request,
            self.db,
        )
        self.db.refresh(domain)
        self.assertEqual(domain.template_html_source, source)
        self.assertIn("Domain", domain.template_html)
        self.assertNotIn("<!--[if mso]", domain.template_html)
        self.assertNotIn("onmouseover", domain.template_html)
        self.assertNotIn("<script", domain.template_html)

    def test_non_admin_cannot_mutate_signature_catalog(self):
        payload = main.SignatureCreateRequest(
            name="Denied", mode="AUTO", html_content="<p>Denied</p>"
        )
        with self.assertRaises(main.HTTPException) as raised:
            main.create_signature_endpoint(payload, self.user_request, self.db)
        self.assertEqual(raised.exception.status_code, 403)

    def test_identity_attributes_are_cached_and_used_when_authentik_fails(self):
        fresh = IdentityAttributes(
            first_name="Alice", last_name="Example", title="Engineer",
            phone="+49 123", groups=frozenset({"signature-users"}),
            claims={"email": "user@example.org"},
            attributes={"department": "IT"},
        )
        with patch.object(main.attribute_provider, "get_attributes", new=AsyncMock(return_value=fresh)):
            first = asyncio.run(main.get_identity_attributes(self.db, "User@Example.org"))
        self.assertEqual(first, fresh)
        stored = self.db.get(main.IdentityCacheDB, "user@example.org")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.first_name, "Alice")

        with patch.object(
            main.attribute_provider, "get_attributes",
            new=AsyncMock(side_effect=main.IdentityProviderUnavailable("offline")),
        ):
            fallback = asyncio.run(main.get_identity_attributes(self.db, "user@example.org"))
        self.assertEqual(fallback, fresh)

    def test_identity_without_prior_cache_returns_empty_attributes_on_provider_failure(self):
        with patch.object(
            main.attribute_provider, "get_attributes",
            new=AsyncMock(side_effect=main.IdentityProviderUnavailable("offline")),
        ):
            result = asyncio.run(main.get_identity_attributes(self.db, "new@example.org"))
        self.assertEqual(result, IdentityAttributes())

    def test_successful_empty_identity_refresh_replaces_cached_attributes(self):
        previous = IdentityCacheDB(
            email="user@example.org", first_name="Old", last_name="Profile",
            title="Old title", phone="123", groups=["old-group"],
            claims={"old": True}, attributes={"department": "Old"},
        )
        self.db.add(previous)
        self.db.commit()
        with patch.object(
            main.attribute_provider, "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes()),
        ):
            refreshed = asyncio.run(main.get_identity_attributes(self.db, "user@example.org"))
        self.assertEqual(refreshed, IdentityAttributes())
        stored = self.db.get(main.IdentityCacheDB, "user@example.org")
        self.assertEqual(stored.first_name, "")
        self.assertEqual(stored.groups, [])
        self.assertEqual(stored.claims, {})
        self.assertEqual(stored.attributes, {})

    def test_invalid_mode_header_falls_back_to_automatic_selection(self):
        self.db.add(main.DomainDB(
            domain_name="example.org", is_active=True,
            template_html="<p>Automatic fallback</p>",
        ))
        self.db.commit()
        with patch.object(
            main.attribute_provider, "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode="NOT-A-VALID-MODE",
                db=self.db,
            ))
        self.assertIn("Automatic fallback", result["html"])


    def test_group_placeholder_resolves_once_and_reuses_same_signature(self):
        domain = main.DomainDB(domain_name="tags.example", is_active=True)
        self.db.add(domain)
        self.db.commit()
        child = main.create_signature_endpoint(
            main.SignatureCreateRequest(name="Reusable Footer", mode="INTERNAL", html_content="<b>Reusable footer</b>"),
            self.admin_request, self.db,
        )
        group = main.create_signature_group(
            main.SignatureGroupCreateRequest(name="Footer Group"), self.admin_request, self.db,
        )
        main.add_signature_group_member(
            group["id"], main.SignatureGroupMemberRequest(signature_id=child["id"]), self.admin_request, self.db,
        )
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Outer Template", mode="AUTO",
                html_content="<div>Top {{ group:footer-group }} Bottom {{ group:footer-group }}</div>",
            ),
            self.admin_request, self.db,
        )
        with patch.object(
            main, "get_identity_attributes",
            new=AsyncMock(return_value=IdentityAttributes(principal_id="idp-immutable:alice-42")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "alice@tags.example", Response(), x_engine_key=main.ENGINE_API_KEY, db=self.db,
            ))
        self.assertEqual(result["html"].count("Reusable footer"), 2)
        states = self.db.query(main.GroupSelectionStateDB).all()
        self.assertEqual(len(states), 1)

    def test_rspamd_engine_selects_signature_from_group_rule(self):
        domain = main.DomainDB(domain_name="group.example", is_active=True)
        self.db.add(domain)
        self.db.commit()
        signature = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Group Engine Signature",
                mode="AUTO",
                html_content="<p>Group engine selected</p>",
            ),
            self.admin_request,
            self.db,
        )
        group = main.create_signature_group(
            main.SignatureGroupCreateRequest(name="Engine Group"),
            self.admin_request,
            self.db,
        )
        main.add_signature_group_member(
            group["id"],
            main.SignatureGroupMemberRequest(signature_id=signature["id"], weight=1),
            self.admin_request,
            self.db,
        )
        main.create_signature_rule(
            main.SignatureRuleCreateRequest(name="Use engine group", action_mode="GROUP", group_id=group["id"]),
            self.admin_request,
            self.db,
        )
        with patch.object(
            main,
            "get_identity_attributes",
            new=AsyncMock(return_value=IdentityAttributes(principal_id="idp-immutable:user-42")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "alice@group.example",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                db=self.db,
            ))
        self.assertIn("Group engine selected", result["html"])

    def test_admin_can_create_group_add_members_and_target_group_from_rule(self):
        signature = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Group member",
                mode="AUTO",
                html_content="<p>Group member</p>",
            ),
            self.admin_request,
            self.db,
        )
        group = main.create_signature_group(
            main.SignatureGroupCreateRequest(name="Corporate Team", domain_ids=[]),
            self.admin_request,
            self.db,
        )
        main.add_signature_group_member(
            group["id"],
            main.SignatureGroupMemberRequest(signature_id=signature["id"], weight=4, is_enabled=True),
            self.admin_request,
            self.db,
        )
        listed_groups = main.list_signature_groups(self.admin_request, self.db)
        listed = next(item for item in listed_groups if item["id"] == group["id"])
        self.assertEqual(listed["slug"], "corporate-team")
        self.assertEqual(listed["members"], [{"signature_id": signature["id"], "weight": 4, "is_enabled": True}])

        rule = main.create_signature_rule(
            main.SignatureRuleCreateRequest(
                name="Corporate group rule",
                action_mode="GROUP",
                group_id=group["id"],
            ),
            self.admin_request,
            self.db,
        )
        listed_rules = main.list_signature_rules(self.admin_request, self.db)
        stored_rule = next(item for item in listed_rules if item["id"] == rule["id"])
        self.assertEqual(stored_rule["action_mode"], "GROUP")
        self.assertEqual(stored_rule["group_id"], group["id"])

        with self.assertRaises(Exception):
            main.delete_signature_group(group["id"], self.admin_request, self.db)


    def test_admin_group_preview_renders_without_consuming_rotation_state(self):
        domain = main.DomainDB(domain_name="preview.example", is_active=True)
        self.db.add(domain)
        self.db.commit()
        signature = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Preview Member",
                mode="AUTO",
                html_content="<p>Hello {{ first_name }} {{ last_name }}</p>",
            ),
            self.admin_request,
            self.db,
        )
        group = main.create_signature_group(
            main.SignatureGroupCreateRequest(name="Preview Group"),
            self.admin_request,
            self.db,
        )
        main.add_signature_group_member(
            group["id"],
            main.SignatureGroupMemberRequest(signature_id=signature["id"]),
            self.admin_request,
            self.db,
        )

        first = main.preview_signature_group(
            group["id"], domain.id, self.admin_request, self.db
        )
        second = main.preview_signature_group(
            group["id"], domain.id, self.admin_request, self.db
        )
        self.assertEqual(first["signature_id"], signature["id"])
        self.assertEqual(first["html"], "<p>Hello Alex Example</p>")
        self.assertEqual(second["signature_id"], first["signature_id"])
        self.assertFalse(first["rotation_state_changed"])
        self.assertEqual(self.db.query(main.GroupSelectionStateDB).count(), 0)

    def test_non_admin_cannot_preview_signature_group(self):
        with self.assertRaises(main.HTTPException) as raised:
            main.preview_signature_group(
                "missing-group", 1, self.user_request, self.db
            )
        self.assertEqual(raised.exception.status_code, 403)


    def test_admin_override_preserves_source_and_returns_it_to_editor(self):
        self.db.add(main.DomainDB(domain_name="example.org", is_active=True))
        self.db.commit()
        source = '<!--[if mso]><v:rect>Outlook</v:rect><![endif]--><p onclick="bad()">Managed<script>bad()</script></p>'
        main.save_admin_override(
            main.AdminOverrideSave(
                user_email="managed@example.org",
                html_content=source,
                is_disabled=False,
            ),
            self.admin_request,
            self.db,
        )
        stored = self.db.query(main.UserOverrideDB).filter_by(
            user_email="managed@example.org"
        ).one()
        self.assertEqual(stored.html_content_source, source)
        self.assertNotIn("<!--[if mso]", stored.html_content)
        listed = main.get_domain_overrides("example.org", self.admin_request, self.db)
        self.assertEqual(listed[0]["html_content"], source)

    def test_raw_signature_version_is_sanitized_before_mail_rendering(self):
        self.db.add(main.DomainDB(domain_name="example.org", is_active=True))
        self.db.commit()
        source = '<!--[if mso]><v:rect>Outlook</v:rect><![endif]--><p onclick="bad()">Safe<script>bad()</script></p>'
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Source-preserved signature",
                mode="PROFESSIONAL",
                html_content=source,
            ),
            self.admin_request,
            self.db,
        )
        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_rspamd_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_mode="PROFESSIONAL",
                db=self.db,
            ))
        self.assertIn("Safe", result["html"])
        self.assertNotIn("<!--[if mso]", result["html"])
        self.assertNotIn("<v:rect", result["html"])
        self.assertNotIn("onclick", result["html"])
        self.assertNotIn("<script", result["html"])



if __name__ == "__main__":
    unittest.main()


class AuthorizationEndpointTests(unittest.TestCase):
    """Verify authorization at real route-handler boundaries, not only helpers."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.user_request = SimpleNamespace(session={
            "user": {"email": "user@example.org", "groups": ["signature-users"]}
        })
        self.admin_request = SimpleNamespace(session={
            "user": {"email": "admin@example.org", "groups": ["signature-admins"]}
        })
        self.anonymous_request = SimpleNamespace(session={})

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_signature_catalog_and_group_endpoints_reject_regular_users(self):
        for handler, args in (
            (main.list_signatures, (self.user_request, self.db)),
            (main.list_signature_groups, (self.user_request, self.db)),
            (main.list_signature_rules, (self.user_request, self.db)),
            (main.read_domains, (self.user_request, self.db)),
            (main.list_identity_providers, (self.user_request, self.db)),
        ):
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(HTTPException) as denied:
                    handler(*args)
                self.assertEqual(denied.exception.status_code, 403)

    def test_signature_catalog_and_group_endpoints_reject_anonymous_sessions(self):
        for handler, args in (
            (main.list_signatures, (self.anonymous_request, self.db)),
            (main.list_signature_groups, (self.anonymous_request, self.db)),
            (main.list_signature_rules, (self.anonymous_request, self.db)),
            (main.read_domains, (self.anonymous_request, self.db)),
            (main.list_identity_providers, (self.anonymous_request, self.db)),
        ):
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(HTTPException) as denied:
                    handler(*args)
                self.assertEqual(denied.exception.status_code, 403)

    def test_personal_signature_endpoint_requires_valid_signature_group(self):
        with self.assertRaises(HTTPException) as anonymous:
            main.get_my_signature(self.anonymous_request, self.db)
        self.assertEqual(anonymous.exception.status_code, 401)

        with self.assertRaises(HTTPException) as unrelated:
            main.get_my_signature(SimpleNamespace(session={
                "user": {"email": "outsider@example.org", "groups": ["unrelated"]}
            }), self.db)
        self.assertEqual(unrelated.exception.status_code, 403)

    def test_authorized_signature_user_can_access_personal_signature_endpoint(self):
        result = main.get_my_signature(self.user_request, self.db)
        self.assertEqual(result, "")

    def test_signature_administrator_can_access_admin_read_endpoints(self):
        self.assertEqual(main.list_signatures(self.admin_request, self.db), [])
        self.assertEqual(main.list_signature_groups(self.admin_request, self.db), [])
        self.assertEqual(main.list_signature_rules(self.admin_request, self.db), [])
        self.assertEqual(main.read_domains(self.admin_request, self.db), [])
        self.assertEqual(main.list_identity_providers(self.admin_request, self.db), [])

