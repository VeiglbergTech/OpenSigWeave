"""Identity-management regression coverage: safe provider edits and override removal."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
import main
from database import Base
from identity.registry import IdentityProviderRegistry
from models import DomainDB, IdentityCacheDB, IdentityProviderDB, UserOverrideDB

ROOT = Path(__file__).resolve().parents[1]


class OverrideRemovalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([DomainDB(domain_name="example.org"),
                         DomainDB(domain_name="other.org")])
        self.db.commit()
        self.db.add_all([
            UserOverrideDB(user_email="alice@example.org", html_content="<p>Alice</p>"),
            UserOverrideDB(user_email="bob@example.org", html_content="<p>Bob</p>"),
            UserOverrideDB(user_email="carol@other.org", html_content="<p>Carol</p>"),
            IdentityCacheDB(email="ldap:alice@example.org", first_name="Alice"),
        ])
        self.db.commit()
        self.ids = {r.user_email: r.id for r in self.db.query(UserOverrideDB).all()}
        self.admin = SimpleNamespace(session={"user":{"email":"admin@example.org",
             "groups":["signature-admins"]}}, headers={})
        self.user = SimpleNamespace(session={"user":{"email":"bob@example.org",
             "groups":["signature-users"]}}, headers={})

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def delete(self, ids, domain="example.org", request=None):
        payload = main.OverrideBulkDeleteRequest(domain_name=domain, ids=ids)
        return main.bulk_delete_admin_overrides(payload, request or self.admin, self.db)

    def test_single_removal_preserves_directory_and_other_user_records(self):
        alice = self.ids["alice@example.org"]
        result = self.delete([alice])
        self.assertEqual(result["deleted_ids"], [alice])
        self.assertIsNone(self.db.get(UserOverrideDB, alice))
        self.assertIsNotNone(self.db.get(IdentityCacheDB, "ldap:alice@example.org"))
        self.assertEqual(self.db.query(DomainDB).count(), 2)
        self.assertEqual(self.db.query(UserOverrideDB).count(), 2)

    def test_bulk_only_deletes_confirmed_in_domain_and_reports_partial_results(self):
        alice, bob, carol = (self.ids[x] for x in
            ["alice@example.org", "bob@example.org", "carol@other.org"])
        result = self.delete([alice, carol, 999999, bob])
        self.assertEqual(result["deleted_ids"], [alice, bob])
        self.assertEqual(result["not_found_ids"], [999999])
        self.assertEqual(result["out_of_scope_ids"], [carol])
        self.assertIsNotNone(self.db.get(UserOverrideDB, carol))
        self.assertEqual(self.db.query(UserOverrideDB).count(), 1)

    def test_non_admin_cannot_delete_any_selected_id(self):
        alice = self.ids["alice@example.org"]
        with self.assertRaises(HTTPException) as error:
            self.delete([alice], request=self.user)
        self.assertEqual(error.exception.status_code, 403)
        self.assertIsNotNone(self.db.get(UserOverrideDB, alice))

    def test_domain_scope_must_exist(self):
        with self.assertRaises(HTTPException) as error:
            self.delete([self.ids["alice@example.org"]], domain="unmanaged.org")
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.db.query(UserOverrideDB).count(), 3)

    def test_selection_validation_refuses_implicit_or_ambiguous_deletion(self):
        for ids in ([], [0], [-4], [True], ["1"], [1, 1], list(range(1, 5002))):
            with self.subTest(ids=ids[:3]):
                with self.assertRaises(ValidationError):
                    main.OverrideBulkDeleteRequest(domain_name="example.org", ids=ids)

    def test_failed_transaction_is_rolled_back_completely(self):
        ids = [self.ids["alice@example.org"], self.ids["bob@example.org"]]
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("simulated failure")):
            with self.assertRaises(HTTPException) as error:
                self.delete(ids)
        self.assertEqual(error.exception.status_code, 500)
        self.assertTrue(all(self.db.get(UserOverrideDB, item) is not None for item in ids))

    def test_effective_signature_uses_normal_rule_immediately_after_removal(self):
        alice = self.ids["alice@example.org"]
        result = SimpleNamespace(template_html="<p>Rule</p>", html="<p>Rule</p>")
        async def effective():
            with patch.object(main, "resolve_signature_engine", new=AsyncMock(return_value=result)):
                return await main._portal_effective_signature(self.db, "alice@example.org")
        self.assertEqual(asyncio.run(effective())["source"], "override")
        self.delete([alice])
        after = asyncio.run(effective())
        self.assertEqual(after["source"], "rule")
        self.assertFalse(after["has_override"])


class ProviderClassificationTests(unittest.TestCase):
    def test_authentik_api_and_oidc_credentials_survive_independent_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = IdentityProviderRegistry(Path(tmp) / ".idp")
            values = {
                "AUTHENTIK_URL":"https://auth.example.org",
                "AUTHENTIK_API_TOKEN":"private-api-token",
                "AUTHENTIK_TIMEOUT":"5",
                "OIDC_DISCOVERY_URL":"https://auth.example.org/application/o/app/.well-known/openid-configuration",
                "OIDC_CLIENT_ID":"opensigweave",
                "OIDC_CLIENT_SECRET":"private-oidc-secret",
                "OIDC_REDIRECT_URI":"https://sig.example.org/auth/company",
            }
            registry.save("company", "Company", "authentik", values)
            safe = registry.describe("company")
            self.assertTrue(safe["oidc_secret_configured"])
            self.assertTrue(safe["authentik_api_secret_configured"])
            self.assertNotIn("AUTHENTIK_API_TOKEN", safe["settings"])
            self.assertNotIn("OIDC_CLIENT_SECRET", safe["settings"])
            # This is the exact browser behavior: send all nonsecret settings
            # plus blank secret placeholders, whether editing web or directory.
            registry.save("company", "Company edited", "authentik", {
                **safe["settings"], "OIDC_CLIENT_ID":"revised-client",
                "AUTHENTIK_API_TOKEN":"", "OIDC_CLIENT_SECRET":"",
            })
            current = registry.get_config("company")
            self.assertEqual(current["OIDC_CLIENT_ID"], "revised-client")
            self.assertEqual(current["AUTHENTIK_API_TOKEN"], "private-api-token")
            self.assertEqual(current["OIDC_CLIENT_SECRET"], "private-oidc-secret")

    def test_existing_authentik_and_ldap_capabilities_remain_supported(self):
        html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        js = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
        page = html.split('x-data="identityProviderManager"',1)[1].split('x-data="domainManager"',1)[0]
        self.assertIn("webProviders()", page)
        self.assertIn("provisioningProviders()", page)
        self.assertEqual(page.count('class="form-dialog osw-modal-shell'), 2)
        self.assertNotIn('x-text="provider.id"', page)
        self.assertIn("['oidc', 'authentik', 'ldap']", js)
        self.assertIn("['authentik', 'ldap']", js)
        self.assertIn("provider.oidc_secret_configured", js)
        self.assertIn("provider.authentik_api_secret_configured", js)
        self.assertIn('x-model="form.settings.OIDC_CLIENT_SECRET"', page)
        self.assertIn('x-model="form.settings.LDAP_BIND_PASSWORD"', page)
        self.assertIn('x-model="form.settings.AUTHENTIK_API_TOKEN"', page)

    def test_overlays_and_selection_keep_shared_accessible_design(self):
        html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        css = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
        self.assertIn('aria-label="Web Interface provider editor"', html)
        self.assertIn('aria-label="User Provisioning provider editor"', html)
        self.assertIn("osw-modal-body", html)
        self.assertIn("osw-modal-footer", html)
        self.assertIn(".osw-idp-dialog .osw-idp-input", css)
        self.assertIn("padding: .65rem .875rem !important", css)
        self.assertIn("var(--osw-input-bg)", css)
        self.assertIn("overflow-y: auto", css)
        for action in ('toggleAllOverrides($event.target.checked)',
                       'deleteSelectedOverrides()', 'deleteSingleOverride(item.id)',
                       'selectedOverrideCount()', 'displayedOverrides()'):
            self.assertIn(action, html)
        self.assertNotIn("Remove all", html)
        self.assertIn("every override in this domain across all pages and filters", html)


if __name__ == "__main__":
    unittest.main()
