"""Static regressions for shared CKEditor layout and safe HTML editing."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/rich-editor.js").read_text(encoding="utf-8")
PORTAL = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")


class SignatureEditorResponsiveTests(unittest.TestCase):
    def test_shared_adapter_and_permissions(self):
        for host, source in (
            ("user-rich-editor", "raw-html-editor"),
            ("admin-rich-editor", "admin-raw-html-editor"),
        ):
            self.assertIn(f"mountRichEditor('{host}', '{source}'", PORTAL)
            self.assertIn(f'id="{host}"', HTML)
        self.assertIn("OpenSigWeaveEditor.create(target", PORTAL)
        self.assertIn('readOnly: document.body.dataset.canEdit !== "true"', PORTAL)
        self.assertIn("imageUpload: !!options.imageUpload", PORTAL)
        self.assertIn("sourceHtml = editorConfig.initialData", EDITOR)
        self.assertIn("normalized === normalizedBaseline ? sourceHtml : normalized", EDITOR)

    def test_accessible_toolbar_wrapping_and_width(self):
        self.assertEqual(EDITOR.count("shouldNotGroupWhenFull: true"), 2)
        self.assertNotIn("shouldNotGroupWhenFull: false", EDITOR)
        rule = CSS.split(".osw-rich-editor .ck-toolbar__items {", 1)[1].split("}", 1)[0]
        self.assertIn("flex-wrap: wrap", rule)
        self.assertNotIn("nowrap", rule)
        self.assertIn("grid-template-columns: minmax(14rem, 1fr) minmax(0, 3.25fr)", CSS)
        self.assertIn('class="osw-personal-editor-column space-y-6"', HTML)
        self.assertIn("max-width: calc(100vw - 1rem)", CSS)
        self.assertIn("overflow-x: auto", CSS)

    def test_regular_tables_are_not_locked_but_outlook_markup_is(self):
        lock = PORTAL.split("function toggleLock(", 1)[1].split("function checkOutlookMarkupWarnings", 1)[0]
        self.assertNotIn("includes('<table')", lock)
        self.assertIn("hasUnsupportedOutlookMarkup", lock)
        self.assertIn("mso", lock)
        self.assertIn("v:[a-z]", lock)
        self.assertIn("lockEl.classList.toggle('hidden', !hasUnsupportedOutlookMarkup)", lock)
        self.assertIn("Outlook-specific markup", HTML)

    def test_label_and_both_themes_remain_supported(self):
        self.assertIn('placeholder: "Font"', EDITOR)
        self.assertIn('label: "Font family"', EDITOR)
        self.assertIn('html[data-theme="light"] .ck', CSS)
        self.assertIn("--ck-color-base-background: var(--osw-panel)", CSS)
        self.assertIn("background: #fff !important; color: #111827 !important", CSS)


if __name__ == "__main__":
    unittest.main()
