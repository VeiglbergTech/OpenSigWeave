"""Domain fallback precedence, identity outage handling, API persistence and rotation."""
import asyncio
import hashlib
import os
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from core.domain_fallbacks import FALLBACK_FIELDS, signature_profile_values, validate_fallback_values
from core.engine_contract import SignatureEngineError, normalize_engine_request
from core.engine_service import SignatureEngineService
from database import Base
from identity.attributes import IdentityAttributes
from identity.providers.base import IdentityProviderUnavailable
from models import (
    DomainDB, GroupSelectionStateDB, IdentityCacheDB, IdentityProviderDB,
    SignatureDB, SignatureGroupDB, SignatureGroupMemberDB,
    SignatureRuleDB, SignatureRuleConditionDB, SignatureVersionDB,
)


class FallbackValueTests(unittest.TestCase):
    def test_complete_profile_has_priority_over_fallbacks(self):
        profile = IdentityAttributes(
            first_name="Alice", last_name="Example", title="Engineer", phone="123",
            attributes={"company": "Actual Inc.", "department": ["Ops"],
                        "location": "Berlin", "mobile": "456", "website": "https://actual.test"},
        )
        fallback = {key: "Fallback" for key in FALLBACK_FIELDS}
        resolved = signature_profile_values(profile, "alice@example.org", "example.org", fallback)
        self.assertEqual(resolved["first_name"], "Alice")
        self.assertEqual(resolved["last_name"], "Example")
        self.assertEqual(resolved["title"], "Engineer")
        self.assertEqual(resolved["phone"], "123")
        self.assertEqual(resolved["company"], "Actual Inc.")
        self.assertEqual(resolved["department"], "Ops")
        self.assertEqual(resolved["location"], "Berlin")
        self.assertEqual(resolved["mobile"], "456")
        self.assertEqual(resolved["website"], "https://actual.test")
        self.assertEqual(resolved["email"], "alice@example.org")
        self.assertEqual(resolved["domain_name"], "example.org")

    def test_only_missing_or_whitespace_attributes_use_fallback(self):
        profile = IdentityAttributes(
            first_name="  ", last_name="Source", phone="",
            attributes={"company": [], "department": "Internal"},
        )
        values = signature_profile_values(
            profile, "missing@example.org", "example.org",
            {"first_name": "Generic", "last_name": "Not used",
             "phone": "+49 100", "company": "Example Ltd.", "department": "Fallback"},
        )
        self.assertEqual(values["first_name"], "Generic")
        self.assertEqual(values["last_name"], "Source")
        self.assertEqual(values["phone"], "+49 100")
        self.assertEqual(values["company"], "Example Ltd.")
        self.assertEqual(values["department"], "Internal")
        self.assertEqual(values["title"], "")
        self.assertEqual(values["mobile"], "")

    def test_unknown_user_uses_domain_values_but_no_fallback_means_blank(self):
        missing = IdentityAttributes()
        with_default = signature_profile_values(
            missing, "unknown@example.org", "example.org",
            {"first_name": "Team", "company": "Example"},
        )
        self.assertEqual(with_default["first_name"], "Team")
        self.assertEqual(with_default["company"], "Example")
        self.assertEqual(with_default["email"], "unknown@example.org")
        without_default = signature_profile_values(missing, "unknown@example.org", "example.org", {})
        self.assertTrue(all(without_default[key] == "" for key in FALLBACK_FIELDS))

    def test_fallbacks_must_not_override_sender_or_domain(self):
        with self.assertRaises(ValueError):
            validate_fallback_values({"email": "forged@example.net"})
        with self.assertRaises(ValueError):
            validate_fallback_values({"domain_name": "example.net"})
        with self.assertRaises(ValueError):
            validate_fallback_values({"first_name": "x" * 257})
        with self.assertRaises(ValueError):
            validate_fallback_values({"title": 123})
        self.assertEqual(validate_fallback_values({"title": " ", "company": " Example "}),
                         {"company": "Example"})

    def test_nested_provider_claims_supported_without_mutation(self):
        data = {"department": ["Real Dept"]}
        profile = IdentityAttributes(attributes=data, claims={"company": "Real Co"})
        result = signature_profile_values(
            profile, "x@a.test", "a.test", {"department": "Fallback", "company": "Fallback"})
        self.assertEqual(result["company"], "Real Co")
        self.assertEqual(result["department"], "Real Dept")
        self.assertEqual(data, {"department": ["Real Dept"]})


class DomainFallbackDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_signature_rule(self, template="<p>{{ first_name }} {{ company }} {{ email }}</p>"):
        sig = SignatureDB(id=uuid.uuid4().hex, name="Fallback Signature",
                          scope="GLOBAL", mode="UNUSED", is_active=True)
        version = SignatureVersionDB(
            id=uuid.uuid4().hex, signature_id=sig.id, version_number=1,
            relative_path="fallback/test/signature.html", sha256="a" * 64, size_bytes=len(template),
        )
        rule = SignatureRuleDB(id=uuid.uuid4().hex, name="All senders",
                               priority=5, action_mode="SIGNATURE", signature_id=sig.id, is_enabled=True)
        self.db.add_all([sig, version, rule])
        self.db.commit()
        return template

    def _resolve(self, email, profile, template):
        store = Mock()
        store.read.return_value = template
        service = SignatureEngineService(AsyncMock(return_value=profile), store)
        return asyncio.run(service.resolve(self.db, normalize_engine_request(email)))

    def test_multiple_domain_fallbacks_do_not_cross_contaminate(self):
        self.db.add_all([
            DomainDB(domain_name="one.test", fallback_values={"first_name": "One", "company": "Company One"}),
            DomainDB(domain_name="two.test", fallback_values={"first_name": "Two", "company": "Company Two"}),
        ])
        self.db.commit()
        template = self._add_signature_rule()
        one = self._resolve("unknown@one.test", IdentityAttributes(), template)
        two = self._resolve("unknown@two.test", IdentityAttributes(), template)
        self.assertIn("One Company One unknown@one.test", one.html)
        self.assertIn("Two Company Two unknown@two.test", two.html)
        self.assertNotIn("Company Two", one.html)

    def test_known_user_with_partial_attributes_renders_from_fallback(self):
        self.db.add(DomainDB(domain_name="example.org", fallback_values={
            "first_name": "Fallback", "last_name": "Family", "company": "Example",
        }))
        self.db.commit()
        template = self._add_signature_rule("<p>{{ first_name }} {{ last_name }} / {{ company }}</p>")
        result = self._resolve("employee@example.org", IdentityAttributes(first_name="Real"), template)
        self.assertEqual(result.html, "<p>Real Family / Example</p>")

    def test_unknown_user_rotation_uses_anonymous_principal_and_no_repeat(self):
        self.db.add(DomainDB(domain_name="example.org", fallback_values={"first_name": "Team"}))
        group = SignatureGroupDB(id=uuid.uuid4().hex, name="Team", slug="team", is_active=True)
        self.db.add(group)
        self.db.flush()
        for name in ("A", "B"):
            signature = SignatureDB(id=uuid.uuid4().hex, name=name, scope="GLOBAL", is_active=True)
            self.db.add(signature)
            self.db.add(SignatureVersionDB(
                id=uuid.uuid4().hex, signature_id=signature.id, version_number=1,
                relative_path=name + "/signature.html", sha256="a" * 64, size_bytes=20,
            ))
            self.db.add(SignatureGroupMemberDB(group_id=group.id, signature_id=signature.id, weight=1, is_enabled=True))
        self.db.add(SignatureRuleDB(id=uuid.uuid4().hex, name="Group", action_mode="GROUP",
                                    group_id=group.id, is_enabled=True))
        self.db.commit()
        store = Mock()
        store.read.return_value = "<p>{{ first_name }}</p>"
        service = SignatureEngineService(AsyncMock(return_value=IdentityAttributes()), store)
        first = asyncio.run(service.resolve(self.db, normalize_engine_request("unknown@example.org")))
        first_id = self.db.query(GroupSelectionStateDB).one().last_signature_id
        second = asyncio.run(service.resolve(self.db, normalize_engine_request("unknown@example.org")))
        second_id = self.db.query(GroupSelectionStateDB).one().last_signature_id
        self.assertEqual(first.html, "<p>Team</p>")
        self.assertEqual(second.html, "<p>Team</p>")
        self.assertNotEqual(first_id, second_id)
        state = self.db.query(GroupSelectionStateDB).one()
        self.assertEqual(state.principal_id, "fallback:" + hashlib.sha256(b"unknown@example.org").hexdigest())

    def test_domain_admin_api_persists_updates_and_omission_is_compatible(self):
        with patch.object(main, "is_admin_user", return_value=True):
            created = main.create_domain(
                main.DomainCreate(domain_name="example.org", fallback_values={"title": "Engineer"}),
                None, self.db,
            )
            self.assertEqual(created.fallback_values, {"title": "Engineer"})
            self.assertEqual(main.read_domains(None, self.db)[0]["fallback_values"], {"title": "Engineer"})
            settings = {
                "is_active": True, "allow_overrides": True, "inject_on_replies": False,
                "trim_whitespace": True, "strip_device_signatures": False,
            }
            main.update_domain(created.id, main.DomainUpdate(**settings), None, self.db)
            self.assertEqual(self.db.get(DomainDB, created.id).fallback_values, {"title": "Engineer"})
            main.update_domain(created.id, main.DomainUpdate(**settings, fallback_values={"company": "Acme"}), None, self.db)
            self.assertEqual(self.db.get(DomainDB, created.id).fallback_values, {"company": "Acme"})
            main.update_domain(created.id, main.DomainUpdate(**settings, fallback_values={}), None, self.db)
            self.assertEqual(self.db.get(DomainDB, created.id).fallback_values, {})

    def test_admin_api_rejects_reserved_email_fallback(self):
        with self.assertRaises(ValidationError):
            main.DomainCreate(domain_name="example.org", fallback_values={"email": "forged@example.org"})
        with self.assertRaises(ValidationError):
            main.DomainUpdate(
                is_active=True, allow_overrides=True, inject_on_replies=False,
                trim_whitespace=True, strip_device_signatures=False,
                fallback_values={"first_name": "x" * 257},
            )

    def test_fail_open_and_fail_closed_for_ldap_and_authentik_api_outage(self):
        for kind in ("ldap", "authentik"):
            with self.subTest(kind=kind):
                pid = kind + "-profile"
                self.db.add(IdentityProviderDB(id=pid, display_name=kind, provider_type=kind, is_enabled=True))
                self.db.add(IdentityCacheDB(email=pid + ":user@example.org", first_name="Cached",
                                            last_name="", title="", phone="", groups=[], claims={}, attributes={}))
                self.db.commit()
                provider = Mock()
                provider.get_attributes = AsyncMock(side_effect=IdentityProviderUnavailable("offline"))
                with patch.object(main.identity_provider_registry, "get", return_value=provider):
                    with patch.object(main, "IDENTITY_LOOKUP_FAIL_CLOSED", False):
                        result = asyncio.run(main.get_identity_attributes(self.db, "user@example.org", pid))
                    self.assertEqual(result.first_name, "Cached")
                    self.assertNotIn("Fallback", self.db.get(IdentityCacheDB, pid + ":user@example.org").first_name)
                    with patch.object(main, "IDENTITY_LOOKUP_FAIL_CLOSED", True):
                        with self.assertRaises(SignatureEngineError) as failure:
                            asyncio.run(main.get_identity_attributes(self.db, "user@example.org", pid))
                        self.assertEqual(failure.exception.code, "IDENTITY_PROVIDER_UNAVAILABLE")
                        self.assertEqual(main._engine_http_exception(failure.exception).status_code, 503)

    def test_confirmed_unknown_does_not_fail_closed_or_cache_fallback_values(self):
        self.db.add(IdentityProviderDB(id="ldap", display_name="LDAP", provider_type="ldap", is_enabled=True))
        self.db.commit()
        provider = Mock()
        provider.get_attributes = AsyncMock(return_value=IdentityAttributes())
        with patch.object(main.identity_provider_registry, "get", return_value=provider), patch.object(
            main, "IDENTITY_LOOKUP_FAIL_CLOSED", True
        ):
            identity = asyncio.run(main.get_identity_attributes(self.db, "missing@example.org", "ldap"))
        self.assertEqual(identity, IdentityAttributes())
        cached = self.db.get(IdentityCacheDB, "ldap:missing@example.org")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.first_name, "")
        self.assertEqual(cached.attributes, {})


if __name__ == "__main__":
    unittest.main()
