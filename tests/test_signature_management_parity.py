"""Regression checks for Signatures/Domain Management UI parity."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")

SIGNATURES = HTML.split('x-show="adminTab === \'signatures\'"')[1].split(
    'x-show="adminTab === \'signature-groups\'"')[0]
DOMAINS = HTML.split('x-show="adminTab === \'domains\'"')[1].split(
    'x-show="adminTab === \'signatures\'"')[0]


class SignatureManagementParityTests(unittest.TestCase):
    def test_shared_table_header_and_row_styles(self):
        for markup in (SIGNATURES, DOMAINS):
            with self.subTest(view="signatures" if markup == SIGNATURES else "domains"):
                self.assertIn('class="osw-section-header mb-6"', markup)
                self.assertIn('class="bg-gray-800 rounded-lg shadow osw-table-scroll border border-gray-700"', markup)
                self.assertRegex(markup, r'<table class="[^"]*osw-data-table[^"]*"',
                                 "The shared table component must be used")
                self.assertIn('class="divide-y divide-gray-700"', markup)
                self.assertIn('class="hover:bg-gray-750 transition duration-150"', markup)
        self.assertIn("osw-admin-status", SIGNATURES)
        self.assertIn("var(--osw-panel-raised)", CSS)
        self.assertIn("table.osw-data-table > thead th", CSS)
        self.assertIn("table.osw-data-table > tbody td", CSS)

    def test_search_status_scope_and_empty_states(self):
        for binding in ('x-model="searchQuery"', 'x-model="statusFilter"', 'x-model="domainFilter"',
                        'signature in filteredSignatures', 'x-text="signatureResultLabel"',
                        'No signatures match the selected filters'):
            with self.subTest(binding=binding):
                self.assertIn(binding, SIGNATURES)
        self.assertIn('get filteredSignatures()', JS)
        self.assertIn("this.domainFilter === 'global'", JS)

    def test_versions_markers_and_editor_are_preserved(self):
        for binding in ('formatSignatureMarker(signature)', 'x-text="signature.versions"',
                        'x-model="selectedVersionId"', 'restoreSelectedVersion()',
                        'id="signature-template-editor"', 'id="signature-html-source"',
                        'toggleDomain(domain.id',
                        'saveSignature()', 'x-model="form.is_active"'):
            with self.subTest(binding=binding):
                self.assertIn(binding, SIGNATURES)
        self.assertIn('async previewSignature(signature)', JS)
        self.assertIn('async toggleSignatureStatus(signature)', JS)
        self.assertIn('signature-catalog-updated', JS)

    def test_legacy_group_picker_removed_and_advanced_preview_scoped(self):
        self.assertNotIn("Advanced: Insert signature group tag", SIGNATURES)
        self.assertNotIn("insertGroupTag(", JS)
        self.assertNotIn("selectedGroupSlug", JS)
        self.assertNotIn('id="group-preview-domain"', HTML)
        groups = HTML.split('x-show="adminTab === \'signature-groups\'"')[1].split(
            'x-show="adminTab === \'rules\'"')[0]
        for required in ('openAdvancedPreview(group)', 'id="group-advanced-preview-domain"',
                         'x-show="advancedPreviewOpen"', 'previewGroup(group)'):
            self.assertIn(required, groups)
        self.assertIn("previewDomainsForGroup(group)", JS)
        self.assertIn("result.rotation_state_changed !== false", JS)

    def test_modal_uses_shared_dialog_layout_and_sections(self):
        for markup in ('aria-label="Signature editor"', 'osw-modal-header osw-domain-modal-header',
                       'class="osw-modal-body"', 'class="osw-modal-footer"',
                       'General Settings', 'HTML Template &amp; Versions'):
            with self.subTest(markup=markup):
                self.assertIn(markup, SIGNATURES)
        self.assertIn('overflow-y: auto', CSS)
        self.assertIn('.osw-modal-footer button', CSS)
        self.assertIn('.osw-signature-marker', CSS)


if __name__ == "__main__":
    unittest.main()
