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
                self.assertIn('class="w-full text-left border-collapse"', markup)
                self.assertIn('class="divide-y divide-gray-700"', markup)
                self.assertIn('class="hover:bg-gray-750 transition duration-150"', markup)
        self.assertIn("osw-admin-status", SIGNATURES)
        self.assertIn("var(--osw-panel-raised)", CSS)

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
                        'toggleDomain(domain.id', 'insertGroupTag(selectedGroupSlug',
                        'saveSignature()', 'x-model="form.is_active"'):
            with self.subTest(binding=binding):
                self.assertIn(binding, SIGNATURES)
        self.assertIn('async previewSignature(signature)', JS)
        self.assertIn('async toggleSignatureStatus(signature)', JS)
        self.assertIn('signature-catalog-updated', JS)

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
