"""Static Release 2 regressions for shared layout, badges and modal design."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")


class GlobalDesignSystemTests(unittest.TestCase):
    def test_fluid_content_and_navigation(self):
        layout = CSS.split(".osw-app-content {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: none", layout)
        self.assertIn("padding-inline: clamp(", layout)
        self.assertNotIn("max-w-7xl", HTML.split('class="osw-app-content', 1)[1].split('"', 1)[0])
        self.assertIn(".osw-application-page > nav", CSS)
        self.assertIn(".osw-application-page > nav .osw-tabs", CSS)

    def test_backup_card_primitive_shared_with_idp(self):
        self.assertIn(".osw-card {", CSS)
        self.assertEqual(HTML.count('class="osw-idp-section osw-card"'), 2)
        self.assertGreaterEqual(HTML.count('class="osw-card space-y-4"'), 3)

    def test_status_semantics_and_accessible_toggles(self):
        for token in ("--osw-status-neutral-bg", "--osw-status-success-bg",
                      "--osw-status-warning-bg", "--osw-status-error-bg",
                      ".osw-admin-status, .osw-health-result"):
            self.assertIn(token, CSS)
        self.assertNotIn(".osw-admin-status:is(.text-gray-500, .text-red-300", CSS)
        self.assertIn('!provider.is_enabled', HTML)
        self.assertIn('class="osw-admin-status" :aria-pressed="item.enabled"', HTML)
        self.assertIn(':aria-pressed="signature.is_active"', HTML)
        self.assertIn('item.status === \'failed\' ? \'text-red-300\'', HTML)

    def test_dialog_backdrop_is_single_and_theme_aware(self):
        self.assertEqual(HTML.count('id="osw-modal-backdrop"'), 1)
        self.assertIn(':root[data-theme="light"] .osw-dialog-backdrop', CSS)
        self.assertIn('backdrop-filter: blur(3px)', CSS)
        self.assertIn("body:not(.osw-overlay-open) .osw-dialog-backdrop", CSS)
        self.assertIn("max-height: calc(100dvh - 2rem)", CSS)

    def test_table_alignment_and_link_visibility(self):
        self.assertIn("> table.osw-data-table > thead th.text-right", CSS)
        self.assertIn("table-layout: auto;", CSS)
        self.assertIn(".osw-idp-actions {", CSS)
        self.assertIn("text-decoration: underline !important", CSS)
        self.assertNotIn("a, a:hover, a:focus-visible,", CSS)
        self.assertIn("osw-backup-rules-table", HTML)
        self.assertIn("osw-groups-table", HTML)


if __name__ == "__main__":
    unittest.main()
