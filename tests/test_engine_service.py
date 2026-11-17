"""Direct tests for the framework-independent signature engine service."""

import asyncio
import hashlib
import uuid
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.engine_contract import SignatureEngineError, normalize_engine_request
from core.engine_service import SignatureEngineService
from database import Base
from identity.attributes import IdentityAttributes
from models import (
    DomainDB,
    GroupSelectionStateDB,
    SignatureDB,
    SignatureGroupDB,
    SignatureGroupMemberDB,
    SignatureRuleConditionDB,
    SignatureRuleDB,
    SignatureVariantDB,
    SelectionStateDB,
    SignatureVersionDB,
    UserOverrideDB,
)
from signature_storage import SignatureStorageError


class SignatureEngineServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_versioned_signature(self, name, mode, html):
        signature_id = uuid.uuid4().hex
        version_id = uuid.uuid4().hex
        signature = SignatureDB(
            id=signature_id,
            name=name,
            domain_id=None,
            scope="GLOBAL",
            mode=mode,
            is_active=True,
        )
        version = SignatureVersionDB(
            id=version_id,
            signature_id=signature_id,
            version_number=1,
            relative_path=f"{signature_id}/{version_id}/signature.html",
            sha256="a" * 64,
            size_bytes=len(html.encode("utf-8")),
        )
        self.db.add_all([signature, version])
        return signature

    def _service(self, identity=None, file_store=None):
        resolver = AsyncMock(return_value=identity or IdentityAttributes(
            principal_id="idp-immutable:principal-1",
            first_name="Ada",
            last_name="Lovelace",
        ))
        store = file_store or Mock()
        return SignatureEngineService(resolver, store), resolver, store

    def test_service_renders_default_domain_template_with_injected_dependencies(self):
        domain = DomainDB(
            domain_name="example.org",
            is_active=True,
            inject_on_replies=True,
            trim_whitespace=False,
            strip_device_signatures=True,
            template_html="<p>Hello {{ first_name }} {{ last_name }}</p>",
        )
        self.db.add(domain)
        self.db.commit()

        identity = IdentityAttributes(
            principal_id="principal-1",
            first_name="Ada",
            last_name="Lovelace",
        )
        service, identity_resolver, file_store = self._service(identity)

        result = asyncio.run(
            service.resolve(self.db, normalize_engine_request("Ada@Example.org"))
        )

        self.assertEqual(result.html, "<p>Hello Ada Lovelace</p>")
        self.assertTrue(result.inject_on_replies)
        self.assertFalse(result.trim_whitespace)
        self.assertTrue(result.strip_device_signatures)
        identity_resolver.assert_awaited_once_with(self.db, "ada@example.org", None)
        file_store.read.assert_not_called()

    def test_explicit_mode_selects_and_commits_a_versioned_variant(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        signature = self._add_versioned_signature(
            "Professional", "PROFESSIONAL", "<p>Professional signature</p>"
        )
        variant = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="PROFESSIONAL",
            weight=1,
            is_enabled=True,
        )
        self.db.add(variant)
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>Professional signature</p>"
        service, resolver, _ = self._service(file_store=store)
        result = asyncio.run(
            service.resolve(
                self.db,
                normalize_engine_request("ada@example.org", "professional"),
            )
        )

        self.assertEqual(result.html, "<p>Professional signature</p>")
        store.read.assert_called_once()
        resolver.assert_awaited_once()
        state = self.db.query(SelectionStateDB).one()
        self.assertEqual(state.sender_email, "ada@example.org")
        self.assertEqual(state.last_variant_id, variant.id)

    def test_valid_body_marker_selects_global_signature_without_explicit_mode(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        signature = self._add_versioned_signature(
            "Sales", "AUTO", "<p>Sales signature</p>"
        )
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>Sales signature</p>"
        service, resolver, _ = self._service(file_store=store)
        code = hashlib.sha256(b"sales").hexdigest()[:4]
        result = asyncio.run(
            service.resolve(
                self.db,
                normalize_engine_request(
                    "ada@example.org", None, "Sales", code.upper()
                ),
            )
        )

        self.assertEqual(result.html, "<p>Sales signature</p>")
        store.read.assert_called_once()
        resolver.assert_awaited_once()

    def test_invalid_body_marker_returns_empty_without_identity_lookup(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        self.db.commit()
        service, resolver, store = self._service()

        result = asyncio.run(
            service.resolve(
                self.db,
                normalize_engine_request("ada@example.org", None, "Sales", "0000"),
            )
        )

        self.assertEqual(result.html, "")
        resolver.assert_not_awaited()
        store.read.assert_not_called()

    def test_allowed_personal_override_is_rendered_before_automatic_rules(self):
        self.db.add(DomainDB(
            domain_name="example.org",
            is_active=True,
            allow_overrides=True,
        ))
        self.db.add(UserOverrideDB(
            user_email="ada@example.org",
            html_content="<p>Personal {{ first_name }}</p>",
            is_disabled=False,
        ))
        self.db.commit()

        service, resolver, store = self._service()
        result = asyncio.run(
            service.resolve(self.db, normalize_engine_request("ADA@example.org"))
        )

        self.assertEqual(result.html, "<p>Personal Ada</p>")
        resolver.assert_awaited_once()
        store.read.assert_not_called()

    def test_persisted_rule_selects_its_target_signature_variant(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        signature = self._add_versioned_signature(
            "Rule target", "PROFESSIONAL", "<p>Rule selected</p>"
        )
        variant = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="PROFESSIONAL",
            weight=1,
            is_enabled=True,
        )
        rule = SignatureRuleDB(
            id=uuid.uuid4().hex,
            name="Domain rule",
            priority=10,
            action_mode="PROFESSIONAL",
            signature_id=signature.id,
            is_enabled=True,
        )
        condition = SignatureRuleConditionDB(
            rule_id=rule.id,
            kind="domain_equals",
            key="",
            value="example.org",
        )
        self.db.add_all([variant, rule, condition])
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>Rule selected</p>"
        service, _, _ = self._service(file_store=store)
        result = asyncio.run(
            service.resolve(self.db, normalize_engine_request("ada@example.org"))
        )

        self.assertEqual(result.html, "<p>Rule selected</p>")
        state = self.db.query(SelectionStateDB).one()
        self.assertEqual(state.last_variant_id, variant.id)

    def test_group_rule_selects_signature_and_persists_rotation_state(self):
        domain = DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        signature = self._add_versioned_signature(
            "Group member", "AUTO", "<p>Group selected</p>"
        )
        group = SignatureGroupDB(
            id=uuid.uuid4().hex,
            name="Sales team",
            slug="sales-team",
            is_active=True,
        )
        rule = SignatureRuleDB(
            id=uuid.uuid4().hex,
            name="Sales group rule",
            priority=10,
            action_mode="GROUP",
            group_id=group.id,
            is_enabled=True,
        )
        member = SignatureGroupMemberDB(
            group_id=group.id,
            signature_id=signature.id,
            weight=1,
            is_enabled=True,
        )
        self.db.add_all([group, rule, member])
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>Group selected</p>"
        identity = IdentityAttributes(
            principal_id="idp-immutable:ada-1",
            first_name="Ada",
        )
        service, _, _ = self._service(identity, store)
        result = asyncio.run(
            service.resolve(self.db, normalize_engine_request("ada@example.org"))
        )

        self.assertEqual(result.html, "<p>Group selected</p>")
        state = self.db.query(GroupSelectionStateDB).one()
        self.assertEqual(state.principal_id, "idp-immutable:ada-1")
        self.assertEqual(state.last_signature_id, signature.id)

    def test_adapter_can_defer_rotation_commit_until_message_is_accepted(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        signature = self._add_versioned_signature(
            "Deferred professional", "PROFESSIONAL", "<p>Deferred signature</p>"
        )
        self.db.add(SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="PROFESSIONAL",
            weight=1,
            is_enabled=True,
        ))
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>Deferred signature</p>"
        service, _, _ = self._service(file_store=store)
        with patch.object(
            self.db, "commit", wraps=self.db.commit
        ) as commit:
            result = asyncio.run(
                service.resolve(
                    self.db,
                    normalize_engine_request("ada@example.org", "PROFESSIONAL"),
                    commit_selection=False,
                )
            )

        self.assertEqual(result.html, "<p>Deferred signature</p>")
        commit.assert_not_called()
        self.assertEqual(self.db.query(SelectionStateDB).count(), 1)
        self.db.rollback()
        self.assertEqual(self.db.query(SelectionStateDB).count(), 0)

    def test_failed_variant_render_does_not_commit_rotation_state(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        signature = self._add_versioned_signature(
            "Broken professional", "PROFESSIONAL", "<p>{% if %}</p>"
        )
        self.db.add(SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="PROFESSIONAL",
            weight=1,
            is_enabled=True,
        ))
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>{% if %}</p>"
        service, _, _ = self._service(file_store=store)
        with self.assertRaises(SignatureEngineError) as raised:
            asyncio.run(
                service.resolve(
                    self.db,
                    normalize_engine_request("ada@example.org", "PROFESSIONAL"),
                )
            )

        self.assertEqual(raised.exception.code, "INVALID_TEMPLATE")
        # The service flushes the choice inside the caller's transaction, but
        # only the successful-render path may commit it.
        self.db.rollback()
        self.assertEqual(self.db.query(SelectionStateDB).count(), 0)

    def test_failed_group_render_does_not_commit_rotation_state(self):
        domain = DomainDB(domain_name="example.org", is_active=True)
        self.db.add(domain)
        signature = self._add_versioned_signature(
            "Broken group member", "AUTO", "<p>{% if %}</p>"
        )
        group = SignatureGroupDB(
            id=uuid.uuid4().hex,
            name="Broken group",
            slug="broken-group",
            is_active=True,
        )
        rule = SignatureRuleDB(
            id=uuid.uuid4().hex,
            name="Broken group rule",
            priority=10,
            action_mode="GROUP",
            group_id=group.id,
            is_enabled=True,
        )
        member = SignatureGroupMemberDB(
            group_id=group.id,
            signature_id=signature.id,
            weight=1,
            is_enabled=True,
        )
        self.db.add_all([group, rule, member])
        self.db.commit()

        store = Mock()
        store.read.return_value = "<p>{% if %}</p>"
        identity = IdentityAttributes(
            principal_id="idp-immutable:ada-transaction-test",
            first_name="Ada",
        )
        service, _, _ = self._service(identity, store)
        with self.assertRaises(SignatureEngineError) as raised:
            asyncio.run(
                service.resolve(self.db, normalize_engine_request("ada@example.org"))
            )

        self.assertEqual(raised.exception.code, "INVALID_TEMPLATE")
        self.db.rollback()
        self.assertEqual(self.db.query(GroupSelectionStateDB).count(), 0)

    def test_unavailable_versioned_asset_returns_stable_engine_error(self):
        self.db.add(DomainDB(domain_name="example.org", is_active=True))
        self._add_versioned_signature(
            "Professional", "PROFESSIONAL", "<p>Not available</p>"
        )
        signature = self.db.query(SignatureDB).one()
        self.db.add(SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature.id,
            pool_name="PROFESSIONAL",
            weight=1,
            is_enabled=True,
        ))
        self.db.commit()

        store = Mock()
        store.read.side_effect = SignatureStorageError("private storage detail")
        service, _, _ = self._service(file_store=store)
        with self.assertRaises(SignatureEngineError) as raised:
            asyncio.run(
                service.resolve(
                    self.db,
                    normalize_engine_request("ada@example.org", "PROFESSIONAL"),
                )
            )

        self.assertEqual(raised.exception.code, "SIGNATURE_ASSET_UNAVAILABLE")
        self.assertEqual(raised.exception.safe_message, "Signature asset unavailable")

    def test_service_module_has_no_http_transport_dependency(self):
        source = (
            Path(__file__).resolve().parents[1] / "core" / "engine_service.py"
        ).read_text(encoding="utf-8")

        self.assertNotRegex(source, r"(?m)^\s*(?:from|import)\s+fastapi")
        self.assertNotIn("HTTPException", source)
        self.assertNotIn("Response", source)
        # Match the standalone HTTP Request symbol, not SignatureEngineRequest.
        self.assertNotRegex(source, r"\bRequest\b")
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)


if __name__ == "__main__":
    unittest.main()
