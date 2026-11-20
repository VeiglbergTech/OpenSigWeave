"""Runtime tests for administrator signature-catalog API handlers."""
import asyncio
import base64
import json
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

from auth_config import hash_local_admin_password

os.environ["APP_ALLOWED_HOSTS"] = "signature-test.example.org,app.example.org"
os.environ["LOCAL_ADMIN_EMAIL"] = "admin@example.org"
os.environ["LOCAL_ADMIN_PASSWORD_HASH"] = hash_local_admin_password("correct horse battery staple")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from itsdangerous import TimestampSigner

import main
from core.engine_contract import SignatureEngineResult
from database import Base
from signature_storage import SignatureFileStore
from identity.attributes import IdentityAttributes
from models import IdentityCacheDB


class SignatureCatalogApiTests(unittest.TestCase):
    def test_stalwart_hook_rejects_missing_or_invalid_bearer_token(self):
        request = SimpleNamespace(json=AsyncMock(return_value={}))
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}):
            with self.assertRaises(HTTPException) as missing:
                asyncio.run(main.stalwart_mta_hook(request, None, self.db))
            self.assertEqual(missing.exception.status_code, 401)
            with self.assertRaises(HTTPException) as invalid:
                asyncio.run(main.stalwart_mta_hook(
                    request, "Bearer wrong-token", self.db
                ))
            self.assertEqual(invalid.exception.status_code, 401)

    def test_stalwart_hook_accepts_unauthenticated_inbound_without_injection(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": ""}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {"headers": [], "contents": "Hello"},
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}):
            response = asyncio.run(main.stalwart_mta_hook(
                request, "Bearer expected-token", self.db
            ))
        self.assertEqual(response, {"action": "accept"})

    def test_stalwart_hook_uses_authenticated_envelope_sender_and_shared_engine(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "user@example.org"}},
            "envelope": {"from": {"address": "alias@example.org"}},
            "message": {
                "headers": [
                    ["From", " forged@example.net\r\n"],
                    ["x-opensigweave-mode", "retired-selector"],
                    ["X-OpenSigWeave-Signature", "Sales"],
                    ["X-OpenSigWeave-Signature-Code", "a1b2"],
                ],
                "contents": "Hello",
            },
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with (
            patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}),
            patch.object(main, "resolve_signature_engine", new=AsyncMock(return_value=SignatureEngineResult("", False, True, False))) as engine,
        ):
            response = asyncio.run(main.stalwart_mta_hook(
                request, "bEaReR expected-token", self.db
            ))
        self.assertEqual(response["action"], "accept")
        # No body rewrite means no hook modifications: a header-only response
        # could overwrite a body change made by an earlier Stalwart Milter.
        self.assertEqual(response, {"action": "accept"})
        self.assertIs(engine.await_args.args[0], self.db)
        self.assertFalse(engine.await_args.kwargs["commit_selection"])
        engine_request = engine.await_args.args[1]
        self.assertEqual(engine_request.sender_email, "alias@example.org")
        self.assertEqual(engine_request.signature_name, "Sales")
        self.assertEqual(engine_request.signature_code, "a1b2")

    def test_stalwart_hook_commits_rotation_only_after_body_replacement(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "user@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [
                    ["From", " sender@example.org\r\n"],
                    ["Content-Type", " text/plain; charset=utf-8\r\n"],
                ],
                "contents": "Hello",
            },
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with (
            patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}),
            patch.object(
                main, "resolve_signature_engine",
                new=AsyncMock(return_value=SignatureEngineResult(
                    "<p>Signature</p>", False, True, False
                )),
            ) as engine,
            patch.object(self.db, "commit", wraps=self.db.commit) as commit,
            patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback,
        ):
            response = asyncio.run(main.stalwart_mta_hook(
                request, "Bearer expected-token", self.db
            ))

        self.assertIn(
            "replaceContents",
            [item["type"] for item in response["modifications"]],
        )
        self.assertFalse(engine.await_args.kwargs["commit_selection"])
        commit.assert_called_once()
        rollback.assert_not_called()

    def test_stalwart_hook_rolls_back_rotation_when_signed_message_is_not_modified(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "user@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [
                    ["From", " sender@example.org\r\n"],
                    ["Content-Type", ' multipart/signed; boundary="b1"; protocol="application/pgp-signature"\r\n'],
                ],
                "contents": (
                    "--b1\r\nContent-Type: text/plain\r\n\r\nHello\r\n"
                    "--b1--\r\n"
                ),
            },
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with (
            patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}),
            patch.object(
                main, "resolve_signature_engine",
                new=AsyncMock(return_value=SignatureEngineResult(
                    "<p>Signature</p>", False, True, False
                )),
            ) as engine,
            patch.object(self.db, "commit", wraps=self.db.commit) as commit,
            patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback,
        ):
            response = asyncio.run(main.stalwart_mta_hook(
                request, "Bearer expected-token", self.db
            ))

        self.assertEqual(response, {"action": "accept"})
        self.assertFalse(engine.await_args.kwargs["commit_selection"])
        commit.assert_not_called()
        rollback.assert_called_once()

    def test_stalwart_hook_rolls_back_rotation_when_engine_rendering_fails(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "user@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [
                    ["From", " sender@example.org\\r\\n"],
                    ["Content-Type", " text/plain; charset=utf-8\\r\\n"],
                ],
                "contents": "Hello",
            },
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with (
            patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}),
            patch.object(
                main, "resolve_signature_engine",
                new=AsyncMock(side_effect=main.SignatureEngineError(
                    "INVALID_TEMPLATE", "Invalid signature template"
                )),
            ),
            patch.object(self.db, "commit", wraps=self.db.commit) as commit,
            patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.stalwart_mta_hook(
                    request, "Bearer expected-token", self.db
                ))

        self.assertEqual(raised.exception.status_code, 422)
        commit.assert_not_called()
        rollback.assert_called_once()

    def test_stalwart_hook_rolls_back_rotation_when_mime_headers_are_invalid(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "user@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [
                    ["From", " sender@example.org\rInjected: yes\r\n"],
                    ["Content-Type", " text/plain; charset=utf-8\r\n"],
                ],
                "contents": "Hello",
            },
        }
        request = SimpleNamespace(json=AsyncMock(return_value=payload))
        with (
            patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "expected-token"}),
            patch.object(
                main, "resolve_signature_engine",
                new=AsyncMock(return_value=SignatureEngineResult(
                    "<p>Signature</p>", False, True, False
                )),
            ),
            patch.object(self.db, "commit", wraps=self.db.commit) as commit,
            patch.object(self.db, "rollback", wraps=self.db.rollback) as rollback,
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.stalwart_mta_hook(
                    request, "Bearer expected-token", self.db
                ))

        self.assertEqual(raised.exception.status_code, 400)
        commit.assert_not_called()
        rollback.assert_called_once()

    def test_engine_api_exposes_versioned_adapter_neutral_route_and_legacy_rspamd_alias(self):
        routes = {
            route.path: route.methods
            for route in main.app.routes
            if getattr(route, "methods", None)
        }
        self.assertEqual(routes.get("/api/v1/engine/signatures/{email}"), {"GET"})
        self.assertEqual(routes.get("/api/signature/{email}"), {"GET"})
        versioned = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/v1/engine/signatures/{email}")
        legacy = next(route for route in main.app.routes if getattr(route, "path", None) == "/api/signature/{email}")
        self.assertIs(versioned.endpoint, legacy.endpoint)

    def test_engine_route_requires_key_and_sets_private_no_store_cache(self):
        response = Response()
        with patch.object(main, "ENGINE_API_KEY", "engine-secret"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.get_engine_signature(
                    "sender@example.org", response, None, None, None, self.db
                ))
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(response.headers.get("cache-control"), "no-store, private")

    def test_engine_route_normalizes_request_and_returns_shared_result_payload(self):
        response = Response()
        result = SignatureEngineResult(
            "<p>Rendered</p>", True, False, True
        )
        with (
            patch.object(main, "ENGINE_API_KEY", "engine-secret"),
            patch.object(
                main, "resolve_signature_engine", new=AsyncMock(return_value=result)
            ) as engine,
        ):
            payload = asyncio.run(main.get_engine_signature(
                " User@Example.ORG ", response, "engine-secret",
                "Sales", "a1b2", self.db
            ))

        self.assertEqual(payload, result.to_payload())
        self.assertEqual(response.headers.get("cache-control"), "no-store, private")
        engine_request = engine.await_args.args[1]
        self.assertEqual(engine_request.sender_email, "user@example.org")
        self.assertEqual(engine_request.signature_name, "Sales")
        self.assertEqual(engine_request.signature_code, "a1b2")

    def test_engine_route_maps_service_errors_to_stable_http_statuses(self):
        cases = (
            ("INVALID_TEMPLATE", "Invalid signature template", 422),
            ("INVALID_STORED_RULE", "A stored signature rule has an invalid action", 500),
            ("SIGNATURE_ASSET_UNAVAILABLE", "Signature asset unavailable", 503),
        )
        for code, safe_message, expected_status in cases:
            with self.subTest(code=code):
                response = Response()
                with (
                    patch.object(main, "ENGINE_API_KEY", "engine-secret"),
                    patch.object(
                        main, "resolve_signature_engine",
                        new=AsyncMock(side_effect=main.SignatureEngineError(code, safe_message)),
                    ),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        asyncio.run(main.get_engine_signature(
                            "sender@example.org", response, "engine-secret",
                            None, None, self.db
                        ))
                self.assertEqual(raised.exception.status_code, expected_status)
                self.assertEqual(raised.exception.detail, safe_message)
                self.assertEqual(response.headers.get("cache-control"), "no-store, private")

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

    def test_domain_profile_provider_assignment_is_independent_of_login_provider(self):
        domain = main.DomainDB(domain_name="profile-provider.example", is_active=True)
        provider = main.IdentityProviderDB(
            id="profile-provider-test",
            display_name="Profile provider test",
            provider_type="ldap",
            is_enabled=True,
        )
        self.db.add_all([domain, provider])
        self.db.commit()
        self.db.refresh(domain)

        assigned = main.assign_domain_identity_provider(
            domain.id,
            {"identity_provider_id": provider.id},
            self.admin_request,
            self.db,
        )
        self.assertEqual(assigned["identity_provider_id"], provider.id)

        listed = next(
            item for item in main.read_domains(self.admin_request, self.db)
            if item["id"] == domain.id
        )
        self.assertEqual(listed["identity_provider_id"], provider.id)
        self.assertIsNone(listed["authentication_provider_id"])

        # Updating unrelated domain settings must not clear either provider field.
        main.update_domain(
            domain.id,
            main.DomainUpdate(
                is_active=True,
                allow_overrides=True,
                inject_on_replies=False,
                trim_whitespace=True,
                strip_device_signatures=False,
                template_html="<p>Profile provider test</p>",
            ),
            self.admin_request,
            self.db,
        )
        listed_after_update = next(
            item for item in main.read_domains(self.admin_request, self.db)
            if item["id"] == domain.id
        )
        self.assertEqual(listed_after_update["identity_provider_id"], provider.id)
        self.assertIsNone(listed_after_update["authentication_provider_id"])

    def test_signature_api_supports_multiple_domain_assignments(self):
        first = main.DomainDB(domain_name="first.example", is_active=True)
        second = main.DomainDB(domain_name="second.example", is_active=True)
        self.db.add_all([first, second])
        self.db.commit()
        created = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Shared signature",
                domain_ids=[first.id, second.id],
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

    def test_admin_can_create_and_update_signature_without_legacy_variant_api(self):
        payload = main.SignatureCreateRequest(
            name="Corporate",
            domain_id=None,
            html_content="<p>Hello {{ first_name }}</p>",
            is_active=False,
        )
        created = main.create_signature_endpoint(payload, self.admin_request, self.db)
        signature = self.db.get(main.SignatureDB, created["id"])
        self.assertIsNotNone(signature)
        self.assertEqual(signature.mode, "UNUSED")
        self.assertFalse(signature.is_active)
        self.assertNotIn("variant_id", created)

        updated = main.update_signature_endpoint(
            created["id"],
            main.SignatureUpdateRequest(name="Corporate Active", domain_id=None, is_active=True),
            self.admin_request,
            self.db,
        )
        self.assertEqual(updated["name"], "Corporate Active")
        self.assertTrue(updated["is_active"])

    def test_admin_can_add_list_and_preview_versions_without_changing_current(self):
        created = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Versioned",
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
        self.assertEqual([item["is_current"] for item in versions], [True, False])

        previewed = main.get_signature_version(
            created["id"],
            created["version_id"],
            self.admin_request,
            self.db,
        )
        self.assertEqual(previewed["html_content"], "<p>Version 1</p>")
        self.assertFalse(previewed["is_current"])
        after_preview = main.list_signature_versions(created["id"], self.admin_request, self.db)
        self.assertEqual([item["version_number"] for item in after_preview], [2, 1])
        self.assertEqual([item["is_current"] for item in after_preview], [True, False])

    def test_admin_restore_creates_new_current_version_and_preserves_history(self):
        created = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Restorable API",
                html_content="<p>Version 1</p>",
            ),
            self.admin_request,
            self.db,
        )
        second = main.create_signature_version_endpoint(
            created["id"],
            main.SignatureVersionCreateRequest(html_content="<p>Version 2</p>"),
            self.admin_request,
            self.db,
        )

        restored = main.restore_signature_version_endpoint(
            created["id"],
            created["version_id"],
            self.admin_request,
            self.db,
        )
        self.assertEqual(restored["version_number"], 3)
        self.assertEqual(restored["restored_from_version_id"], created["version_id"])
        self.assertEqual(restored["restored_from_version_number"], 1)

        versions = main.list_signature_versions(created["id"], self.admin_request, self.db)
        self.assertEqual([item["version_number"] for item in versions], [3, 2, 1])
        self.assertEqual([item["is_current"] for item in versions], [True, False, False])

        first_snapshot = main.get_signature_version(
            created["id"], created["version_id"], self.admin_request, self.db
        )
        second_snapshot = main.get_signature_version(
            created["id"], second["id"], self.admin_request, self.db
        )
        restored_snapshot = main.get_signature_version(
            created["id"], restored["id"], self.admin_request, self.db
        )
        self.assertEqual(first_snapshot["html_content"], "<p>Version 1</p>")
        self.assertEqual(second_snapshot["html_content"], "<p>Version 2</p>")
        self.assertEqual(restored_snapshot["html_content"], "<p>Version 1</p>")
        self.assertFalse(first_snapshot["is_current"])
        self.assertFalse(second_snapshot["is_current"])
        self.assertTrue(restored_snapshot["is_current"])

        with self.assertRaises(HTTPException) as current_restore:
            main.restore_signature_version_endpoint(
                created["id"], restored["id"], self.admin_request, self.db
            )
        self.assertEqual(current_restore.exception.status_code, 409)

    def test_restore_rejects_version_owned_by_another_signature(self):
        first = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Restore source",
                html_content="<p>Source</p>",
            ),
            self.admin_request,
            self.db,
        )
        second = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Restore target",
                html_content="<p>Target</p>",
            ),
            self.admin_request,
            self.db,
        )
        with self.assertRaises(HTTPException) as denied:
            main.restore_signature_version_endpoint(
                second["id"], first["version_id"], self.admin_request, self.db
            )
        self.assertEqual(denied.exception.status_code, 404)
        target_versions = main.list_signature_versions(second["id"], self.admin_request, self.db)
        self.assertEqual([item["version_number"] for item in target_versions], [1])

    def test_signature_text_marker_selects_named_signature(self):
        domain = main.DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        self.db.commit()
        main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Business",
                domain_id=None,
                html_content="<p>Named marker signature</p>",
            ),
            self.admin_request,
            self.db,
        )
        code = __import__("hashlib").sha256(b"business").hexdigest()[:4]
        with patch.object(
            main.attribute_provider,
            "get_attributes",
            new=AsyncMock(return_value=IdentityAttributes(first_name="Alice")),
        ):
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_name="bUSiness",
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
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
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
                name="Business",
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
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_name="Business",
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
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
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
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
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
                with self.assertRaises(ValueError):
                    main._valid_oidc_identity({**base_user, "email_verified": claim})

    def test_oidc_accepts_boolean_true_email_verified_claim(self):
        user = {
            "sub": "user-123",
            "email": "user@example.org",
            "email_verified": True,
            "groups": ["signature-users"],
            "name": "Test User",
        }
        email, groups = main._valid_oidc_identity(user)
        self.assertEqual(email, "user@example.org")
        self.assertEqual(groups, ["signature-users"])

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
            name="Denied", html_content="<p>Denied</p>"
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

    def test_group_placeholder_resolves_once_and_reuses_same_signature(self):
        domain = main.DomainDB(domain_name="tags.example", is_active=True)
        self.db.add(domain)
        self.db.commit()
        child = main.create_signature_endpoint(
            main.SignatureCreateRequest(name="Reusable Footer", html_content="<b>Reusable footer</b>"),
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
                name="Outer Template",
                html_content="<div>Top {{ group:footer-group }} Bottom {{ group:footer-group }}</div>",
            ),
            self.admin_request, self.db,
        )
        outer_code = __import__("hashlib").sha256(b"outer template").hexdigest()[:4]
        with patch.object(
            main, "get_identity_attributes",
            new=AsyncMock(return_value=IdentityAttributes(principal_id="idp-immutable:alice-42")),
        ):
            result = asyncio.run(main.get_engine_signature(
                "alice@tags.example",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_name="Outer Template",
                x_signature_code=outer_code,
                db=self.db,
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
                html_content='<p>Group engine selected</p><img src="https://example.test/pixel.png" onerror="alert(1)"><script>alert(2)</script>',
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
            result = asyncio.run(main.get_engine_signature(
                "alice@group.example",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                db=self.db,
            ))
        self.assertIn("Group engine selected", result["html"])
        self.assertNotIn("<script", result["html"])
        self.assertNotIn("onerror", result["html"])

    def test_admin_can_create_group_add_members_and_target_group_from_rule(self):
        signature = main.create_signature_endpoint(
            main.SignatureCreateRequest(
                name="Group member",
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
            result = asyncio.run(main.get_engine_signature(
                "user@example.org",
                Response(),
                x_engine_key=main.ENGINE_API_KEY,
                x_signature_db=self.db,
            ))
        self.assertIn("Safe", result["html"])
        self.assertNotIn("<!--[if mso]", result["html"])
        self.assertNotIn("<v:rect", result["html"])
        self.assertNotIn("onclick", result["html"])
        self.assertNotIn("<script", result["html"])



class AuthorizationHttpRouteTests(unittest.TestCase):
    """Exercise authorization through ASGI routing and signed browser sessions."""

    COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "__Host-opensigweave-session")

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.previous_override = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app, base_url="https://app.example.org")

    def tearDown(self):
        if self.previous_override is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = self.previous_override
        self.engine.dispose()

    def authenticate(self, user, csrf_token="csrf-test-token"):
        payload = base64.b64encode(
            json.dumps(
                {"user": user, "csrf_token": csrf_token},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signed = TimestampSigner(main.session_secret).sign(payload).decode("utf-8")
        self.client.cookies.set(self.COOKIE_NAME, signed)
        return csrf_token

    def test_stalwart_http_route_rejects_missing_hook_token(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "sender@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {"headers": [], "contents": "Hello"},
        }
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            response = self.client.post("/api/v1/adapters/stalwart/mta-hook", json=payload)
        self.assertEqual(response.status_code, 401)

    def test_stalwart_http_route_rejects_invalid_json(self):
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            response = self.client.post(
                "/api/v1/adapters/stalwart/mta-hook",
                content=b"{not-json",
                headers={"Authorization": "Bearer route-test-token", "Content-Type": "application/json"},
            )
        self.assertEqual(response.status_code, 400)

    def test_stalwart_http_route_rejects_non_data_stage(self):
        payload = {"context": {"stage": "mail", "sasl": {"login": "sender@example.org"}}}
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            response = self.client.post(
                "/api/v1/adapters/stalwart/mta-hook",
                json=payload,
                headers={"Authorization": "Bearer route-test-token"},
            )
        self.assertEqual(response.status_code, 400)

    def test_stalwart_http_route_rejects_missing_envelope_sender(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "sender@example.org"}},
            "envelope": {"from": {}},
            "message": {"headers": [], "contents": "Hello"},
        }
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            response = self.client.post(
                "/api/v1/adapters/stalwart/mta-hook",
                json=payload,
                headers={"Authorization": "Bearer route-test-token"},
            )
        self.assertEqual(response.status_code, 400)

    def test_stalwart_http_route_rejects_malformed_message_headers(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "sender@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {"headers": [["Content-Type", " text/html\\r\\n"], ["Invalid"]], "contents": "<p>Hello</p>"},
        }
        signature = {"html": "<p>Signature</p>", "inject_on_replies": False}
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            with patch.object(main, "resolve_signature_engine", new=AsyncMock(return_value=SignatureEngineResult(signature["html"], False, True, False))):
                response = self.client.post(
                    "/api/v1/adapters/stalwart/mta-hook",
                    json=payload,
                    headers={"Authorization": "Bearer route-test-token"},
                )
        self.assertEqual(response.status_code, 400)

    def test_stalwart_http_route_accepts_unauthenticated_inbound_without_injection(self):
        payload = {
            "context": {"stage": "data", "sasl": {}},
            "envelope": {"from": {"address": "external@example.net"}},
            "message": {"headers": [], "contents": "Hello"},
        }
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            with patch.object(main, "resolve_signature_engine", new=AsyncMock()) as engine:
                response = self.client.post(
                    "/api/v1/adapters/stalwart/mta-hook",
                    json=payload,
                    headers={"Authorization": "Bearer route-test-token"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"action": "accept"})
        engine.assert_not_awaited()

    def test_stalwart_http_route_injects_using_authenticated_envelope_sender(self):
        payload = {
            "context": {"stage": "data", "sasl": {"login": "sender@example.org"}},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [
                    ["From", " sender@example.org\r\n"],
                    ["Content-Type", " text/html; charset=utf-8\r\n"],
                ],
                "contents": "<html><body>Hello</body></html>",
            },
        }
        signature = {"html": "<p>HTTP route signature</p>", "inject_on_replies": False}
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "route-test-token"}):
            with patch.object(
                main, "resolve_signature_engine", new=AsyncMock(return_value=SignatureEngineResult(signature["html"], False, True, False))
            ) as engine:
                response = self.client.post(
                    "/api/v1/adapters/stalwart/mta-hook",
                    json=payload,
                    headers={"Authorization": "Bearer route-test-token"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "HTTP route signature",
            next(
                item["value"] for item in response.json()["modifications"]
                if item["type"] == "replaceContents"
            ),
        )
        engine.assert_awaited_once()
        self.assertEqual(engine.await_args.args[1].sender_email, "sender@example.org")

    def test_anonymous_http_requests_cannot_read_admin_or_personal_apis(self):
        for path in (
            "/api/admin/signatures",
            "/api/admin/signature-groups",
            "/api/admin/rules",
            "/api/admin/identity-providers",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)

        personal = self.client.get("/api/my-signature")
        self.assertEqual(personal.status_code, 401)

    def test_regular_user_http_requests_are_denied_admin_apis_but_can_read_personal_signature(self):
        self.authenticate({"email": "user@example.org", "groups": ["signature-users"]})
        for path in (
            "/api/admin/signatures",
            "/api/admin/signature-groups",
            "/api/admin/rules",
            "/api/admin/identity-providers",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)

        personal = self.client.get("/api/my-signature")
        self.assertEqual(personal.status_code, 200)
        self.assertEqual(personal.json(), {"html_content": ""})

    def test_admin_mutation_requires_session_bound_csrf_token(self):
        self.authenticate({"email": "admin@example.org", "groups": ["signature-admins"]})
        payload = {"name": "HTTP CSRF Group", "domain_ids": [], "is_active": True}

        denied = self.client.post("/api/admin/signature-groups", json=payload)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"detail": "CSRF validation failed"})

        allowed = self.client.post(
            "/api/admin/signature-groups",
            json=payload,
            headers={"X-CSRF-Token": "csrf-test-token"},
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertIsNotNone(allowed.json().get("id"))

    def test_administrator_http_session_can_read_admin_apis(self):
        self.authenticate({"email": "admin@example.org", "groups": ["signature-admins"]})
        for path in (
            "/api/admin/signatures",
            "/api/admin/signature-groups",
            "/api/admin/rules",
            "/api/admin/identity-providers",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)

    def test_admin_import_preview_requires_admin_and_session_csrf(self):
        self.authenticate({"email": "user@example.org", "groups": ["signature-users"]})
        headers = {"Content-Type": "application/zip", "X-CSRF-Token": "csrf-test-token"}
        denied = self.client.post("/api/admin/imports/preview", content=b"not a zip", headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {"detail": "Admins only"})

        self.authenticate({"email": "admin@example.org", "groups": ["signature-admins"]})
        missing_csrf = self.client.post(
            "/api/admin/imports/preview", content=b"not a zip",
            headers={"Content-Type": "application/zip"},
        )
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf.json(), {"detail": "CSRF validation failed"})

        invalid_archive = self.client.post(
            "/api/admin/imports/preview", content=b"not a zip", headers=headers,
        )
        self.assertEqual(invalid_archive.status_code, 422)


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
        self.assertEqual(result, {"html_content": ""})

    def test_signature_version_restore_endpoint_rejects_non_admin_sessions(self):
        for request in (self.user_request, self.anonymous_request):
            with self.subTest(request=request):
                with self.assertRaises(HTTPException) as denied:
                    main.restore_signature_version_endpoint(
                        "missing-signature",
                        "missing-version",
                        request,
                        self.db,
                    )
                self.assertEqual(denied.exception.status_code, 403)

    def test_signature_administrator_can_access_admin_read_endpoints(self):
        self.assertEqual(main.list_signatures(self.admin_request, self.db), [])
        self.assertEqual(main.list_signature_groups(self.admin_request, self.db), [])
        self.assertEqual(main.list_signature_rules(self.admin_request, self.db), [])
        self.assertEqual(main.read_domains(self.admin_request, self.db), [])
        self.assertEqual(main.list_identity_providers(self.admin_request, self.db), [])



    def test_stalwart_hook_rejects_missing_and_invalid_bearer_tokens(self):
        async def payload():
            return {}

        request = SimpleNamespace(json=payload)
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "stalwart-test-token"}):
            for authorization in (None, "Bearer wrong-token", "Basic stalwart-test-token"):
                with self.subTest(authorization=authorization):
                    with self.assertRaises(HTTPException) as denied:
                        asyncio.run(main.stalwart_mta_hook(request, authorization, self.db))
                    self.assertEqual(denied.exception.status_code, 401)

    def test_stalwart_hook_accepts_case_insensitive_bearer_scheme(self):
        async def payload():
            return {"context": {"stage": "data"}}

        request = SimpleNamespace(json=payload)
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "stalwart-test-token"}):
            result = asyncio.run(
                main.stalwart_mta_hook(request, "bEaReR stalwart-test-token", self.db)
            )
        self.assertEqual(result, {"action": "accept"})

    def test_stalwart_hook_rejects_unsupported_stage_after_authentication(self):
        async def payload():
            return {"context": {"stage": "mail"}}

        request = SimpleNamespace(json=payload)
        with patch.dict(os.environ, {"STALWART_HOOK_TOKEN": "stalwart-test-token"}):
            with self.assertRaises(HTTPException) as denied:
                asyncio.run(
                    main.stalwart_mta_hook(request, "Bearer stalwart-test-token", self.db)
                )
        self.assertEqual(denied.exception.status_code, 400)
