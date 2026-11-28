"""Static regressions for shared UI attribution, table geometry and status copy."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("index.html", "login.html", "setup.html", "error.html")
GITHUB = "https://github.com/VeiglbergTech/OpenSigWeave"


class GlobalUIConsistencyTests(unittest.TestCase):
    def test_attribution_is_unconditional_on_every_page(self):
        for name in PAGES:
            html = (ROOT / "templates" / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertEqual(html.count('class="osw-site-footer"'), 1)
                self.assertIn(f'href="{GITHUB}"', html)
                self.assertIn(">Powered by OpenSigWeave</a>", html)
                footer = html.split('<footer class="osw-site-footer"', 1)[1]
                self.assertNotIn("{%", footer.split("</footer>", 1)[0])
                self.assertNotIn("x-show=", footer.split("</footer>", 1)[0])
                self.assertIn("portal.css", html)

    def test_shared_flow_and_corner_rules(self):
        css = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
        self.assertIn("body.osw-application-page, body.osw-auth-page", css)
        self.assertIn(".osw-app-content { flex:", css)
        self.assertIn(".osw-site-footer", css)
        self.assertNotIn("position: fixed", css.split(".osw-site-footer {", 1)[1].split("}", 1)[0])
        self.assertIn("overflow: clip;", css)
        self.assertIn("border-collapse: separate;", css)
        self.assertIn("border-spacing: 0;", css)
        self.assertIn("border-top-left-radius: calc(.5rem - 1px)", css)
        self.assertIn("border-top-right-radius: calc(.5rem - 1px)", css)

    def test_status_copy_preserves_internal_fields(self):
        html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        for expression in ("group.is_active", "rule.is_enabled", "item.enabled"):
            with self.subTest(expression=expression):
                self.assertIn(expression, html)
        # Provider status moved into role-specific view-model methods; both
        # methods still respect the unchanged is_enabled metadata field.
        portal_js = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
        self.assertIn("provider.is_enabled", portal_js)
        self.assertIn('webProviderStatus(provider)', html)
        self.assertIn('provisioningProviderStatus(provider)', html)
        self.assertNotIn("? 'Enabled' : 'Disabled'", html)
        self.assertGreaterEqual(html.count("? 'Active' : 'Disabled'"), 5)
        for binding in ('x-model="form.is_enabled"', 'x-model="ruleForm.enabled"',
                        'x-model="locationForm.enabled"'):
            self.assertIn(binding, html)

    def test_button_branding_does_not_override_links(self):
        js = (ROOT / "static/js/branding.js").read_text(encoding="utf-8")
        self.assertIn('"--osw-action-accent"', js)
        self.assertNotIn('setProperty("--osw-link"', js)
        css = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
        self.assertIn("color: var(--osw-link)", css)
        self.assertIn("text-decoration: underline", css)


if __name__ == "__main__":
    unittest.main()
