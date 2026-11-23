"""CKEditor font controls must use one shared CKEditor toolbar integration."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/rich-editor.js").read_text(encoding="utf-8")
ENTRY = (ROOT / "static/js/ckeditor-entry.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")


class SignatureFontToolbarTests(unittest.TestCase):
    def test_shared_ck_editor_font_dropdowns(self):
        self.assertIn('componentFactory.add("signatureFontFamily"', JS)
        self.assertIn('componentFactory.add("signatureFontSize"', JS)
        self.assertIn('"signatureFontFamily", "signatureFontSize"', JS)
        self.assertNotIn('installSharedFontControls', JS)
        self.assertNotIn('osw-editor-font-toolbar', JS)
        self.assertNotIn('osw-editor-font-toolbar', CSS)
        for name in ("createDropdown", "addListToDropdown", "Collection",
                     "UIModel", "LabeledFieldView", "createLabeledInputText"):
            self.assertIn(name, ENTRY)

    def test_custom_size_goes_through_original_font_size_command(self):
        self.assertIn('[8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36]', JS)
        self.assertIn('dropdown.panelView.children.add(input)', JS)
        self.assertIn('editor.execute("fontSize", { value })', JS)
        self.assertIn('supportAllValues: true', JS)
        self.assertIn('match[1].replace(",", ".")', JS)
        self.assertIn('size < 6 || size > 96', JS)


if __name__ == "__main__":
    unittest.main()
