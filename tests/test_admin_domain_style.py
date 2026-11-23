"""Regression checks for Domain Management style consistency in admin views."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")


class AdminDomainParityTests(unittest.TestCase):
    def test_backup_directory_is_not_an_editable_control(self):
        self.assertNotIn("Backup directory", HTML)
        self.assertNotIn('x-model="settings.directory"', HTML)
        # The protected legacy backup operator still receives its saved directory.
        self.assertIn("...this.settings,", JS)

    def test_versions_not_displayed_in_system_overview(self):
        system = HTML.split('x-show="adminTab === \'system\'"')[1].split("x-data=\"domainSettingsModal\"")[0]
        self.assertNotIn('x-text="version"', system)
        self.assertNotIn("Version:", system)
        self.assertIn('key !== "versions"', JS)
        self.assertIn('>Versions</th>', HTML)  # Signature history remains available.

    def test_all_admin_sections_use_shared_domain_table_style(self):
        for selector in (
            ".osw-admin-section .osw-section-header > :is(h1, h2)",
            ".osw-admin-section .osw-table-scroll",
            ".osw-admin-section .osw-table-scroll table > thead th",
            ".osw-admin-section .osw-table-scroll table > tbody td",
            ".osw-admin-status",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, CSS)
        for name in ("identity-providers", "domains", "signatures", "signature-groups",
                     "rules", "scheduling", "branding", "backups", "import-export", "system"):
            with self.subTest(tab=name):
                self.assertRegex(HTML, r'x-show="adminTab === \'%s\'".*?class="osw-admin-section' % re.escape(name))

    def test_admin_headings_are_uniform(self):
        for section in ("Branding", "Import / Export", "Backup Management", "System Status"):
            self.assertIn('class="text-2xl font-semibold">' + section + '</h2>', HTML)


if __name__ == "__main__":
    unittest.main()
