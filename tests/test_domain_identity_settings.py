"""Domain provider assignments are edited only in Domain Management."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")


class DomainIdentitySettingsTests(unittest.TestCase):
    def test_overview_has_read_only_provider_names(self):
        overview = HTML.split('x-data="domainManager"')[1].split("adminTab === 'signatures'")[0]
        self.assertIn("providerLabel(domain.identity_provider_id, 'profile')", overview)
        self.assertIn("providerLabel(domain.authentication_provider_id, 'login')", overview)
        self.assertNotIn('x-model="domain.identity_provider_id"', overview)
        self.assertNotIn('x-model="domain.authentication_provider_id"', overview)
        self.assertNotIn("setAuthenticationProvider(", JS)
        self.assertNotIn("setProvider(", JS)

    def test_modal_exposes_separate_provider_roles(self):
        modal = HTML.split('x-data="domainSettingsModal"')[1].split('x-data="sanitizerModal"')[0]
        self.assertIn("Identity &amp; Authentication", modal)
        self.assertIn('x-model="identityProviderId"', modal)
        self.assertIn('x-model="authenticationProviderId"', modal)
        self.assertIn("No identity provider", modal)
        self.assertIn("Local Sign-in (No SSO)", modal)
        self.assertIn("selectedProfileUnavailable()", modal)
        self.assertIn("selectedLoginUnavailable()", modal)

    def test_domain_settings_save_assignments_atomically_and_preserve_unchanged(self):
        controller = JS.split("function domainSettingsModal()")[1].split("window.addEventListener('submit-new-domain'")[0]
        self.assertIn("if (profileChanged) payload.identity_provider_id", controller)
        self.assertIn("if (loginChanged) payload.authentication_provider_id", controller)
        self.assertIn("if (!response.ok)", controller)
        self.assertIn("this.show = false", controller)
        self.assertNotIn("save-domain-settings", JS)
        self.assertIn("providerLoading || providerError || saving", HTML)

    def test_domain_fallback_editor_has_all_optional_fields_and_round_trips(self):
        modal = HTML.split('x-data="domainSettingsModal"')[1].split('x-data="sanitizerModal"')[0]
        controller = JS.split("function domainSettingsModal()")[1].split("window.addEventListener('submit-new-domain'")[0]
        self.assertIn("<details", modal)
        self.assertIn("Fallback Values", modal)
        for field in ("first_name", "last_name", "title", "phone", "company",
                      "department", "location", "mobile", "website"):
            self.assertIn(f'x-model="fallbackValues.{field}"', modal)
        self.assertIn("domainData.fallback_values || {}", controller)
        self.assertIn("fallback_values: { ...this.fallbackValues }", controller)
        self.assertNotIn('x-model="fallbackValues.email"', modal)

    def test_available_provider_types_are_separated(self):
        self.assertIn("provider.provider_type === 'ldap' || provider.provider_type === 'authentik'", JS)
        self.assertIn("provider.provider_type === 'authentik' || provider.provider_type === 'oidc'", JS)
        self.assertIn("provider.secret_configured", JS)


if __name__ == "__main__":
    unittest.main()
