"""Signature deletion controls, clipboard behavior and responsive group layout."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")

SIGNATURES = HTML.split('x-show="adminTab === \'signatures\'"')[1].split(
    'x-show="adminTab === \'signature-groups\'"')[0]


class SignatureDeletionUiTests(unittest.TestCase):
    def test_status_filter_uses_any_without_changing_all_value(self):
        self.assertIn('<option value="all">Any</option>', SIGNATURES)
        self.assertNotIn('<option value="all">All statuses</option>', SIGNATURES)
        self.assertIn("statusFilter: 'all'", JS)
        self.assertIn("this.statusFilter === 'active'", JS)
        self.assertIn("this.statusFilter === 'inactive'", JS)

    def test_marker_itself_is_copyable_and_accessible(self):
        self.assertIn('osw-signature-marker-copy', SIGNATURES)
        self.assertIn('@click.stop="copySignatureMarker(signature)"', SIGNATURES)
        self.assertIn(':aria-label="\'Copy signature marker for \' + signature.name"', SIGNATURES)
        self.assertIn("navigator.clipboard.writeText(marker)", JS)
        self.assertIn("notify('Signature marker copied.')", JS)
        self.assertIn("Could not copy signature marker", JS)
        self.assertIn(".osw-signature-marker-copy:focus-visible", CSS)

    def test_delete_uses_impact_preview_and_confirmation(self):
        self.assertIn('@click="deleteSignature(signature)"', SIGNATURES)
        self.assertIn("async deleteSignature(signature)", JS)
        self.assertIn("'/deletion-impact'", JS)
        self.assertIn("oswAskConfirmation({", JS)
        self.assertIn("impact.mandatory_domains.length", JS)
        self.assertIn("method: 'DELETE'", JS)
        self.assertIn("signature-catalog-updated", JS)
        self.assertIn("signature-groups-updated", JS)

    def test_group_table_keeps_compact_name_and_flexible_member_column(self):
        self.assertIn("container-name: osw-groups", CSS)
        self.assertIn("max-width: clamp(9ch, 18cqi, 17rem)", CSS)
        self.assertIn("flex: 1 1 11ch;", CSS)
        self.assertIn("justify-content: space-between;", CSS)
        self.assertIn("vertical-align: middle;", CSS)
        self.assertIn(".osw-group-actions", CSS)
        self.assertIn("@container osw-groups (max-width: 70rem)", CSS)
        self.assertNotIn('class="align-top" data-label="Name"', HTML)


if __name__ == "__main__":
    unittest.main()
