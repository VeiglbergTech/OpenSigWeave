"""Regression checks for static, application-owned English UI copy.

User-created signature content, LDAP attributes and imported values are deliberately
excluded from these checks. Technical identifiers are not translated.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def source(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


class EnglishUICopyTests(unittest.TestCase):
    def test_no_german_copy_in_first_party_ui(self):
        for path in (
            "templates/index.html",
            "templates/login.html",
            "templates/error.html",
            "static/js/portal.js",
            "static/js/rich-editor.js",
            "static/js/branding.js",
            "static/js/theme.js",
        ):
            with self.subTest(path=path):
                content = source(path)
                self.assertIsNone(re.search(r"[äöüÄÖÜß]", content))
                self.assertNotRegex(
                    content,
                    r"\b(?:Anmelden mit|Outlook-spezifische|Branding auf Standard|"
                    r"zurückgesetzt|fehlgeschlagen)\b",
                )

    def test_navigation_and_main_sections_use_consistent_names(self):
        html = source("templates/index.html")
        for label in (
            "Identity Providers",
            "Domains",
            "Signatures",
            "Signature Groups",
            "Rules",
            "Schedules",
            "Branding",
            "Backup",
            "Import / Export",
            "Backup Locations",
            "Backup Rules",
            "Backup History",
            "System Status",
        ):
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertIn(">Schedules</button>", html)
        self.assertIn(">Backup</button>", html)

    def test_important_actions_and_warnings(self):
        html = source("templates/index.html")
        for label in (
            "Test Connection",
            "Run Backup",
            "Reset to Default",
            "Save Changes",
            "Import Image from URL",
            "Outlook-specific conditional comments or VML were detected.",
        ):
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertEqual(html.count("Outlook-specific conditional comments or VML were detected."), 2)
        self.assertNotIn("Outlook-spezifische", html)

    def test_login_branding_and_editor_copy(self):
        self.assertIn(
            "Sign in with {{ provider.display_name }}",
            source("templates/login.html"),
        )
        portal = source("static/js/portal.js")
        self.assertIn("Reset Branding to Default?", portal)
        self.assertIn("Branding reset to default settings.", portal)
        self.assertIn("Could not reset branding: ", portal)
        self.assertIn("Import Image from URL", source("static/js/rich-editor.js"))
        self.assertNotIn("Import Picture from URL", source("static/js/rich-editor.js"))


if __name__ == "__main__":
    unittest.main()
