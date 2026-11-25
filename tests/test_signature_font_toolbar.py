"""CKEditor font controls must use one shared CKEditor toolbar integration."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/rich-editor.js").read_text(encoding="utf-8")
ENTRY = (ROOT / "static/js/ckeditor-entry.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/portal.css").read_text(encoding="utf-8")


class SignatureFontToolbarTests(unittest.TestCase):
    def test_shared_ck_editor_editable_font_inputs(self):
        self.assertIn('componentFactory.add("signatureFontFamily"', JS)
        self.assertIn('componentFactory.add("signatureFontSize"', JS)
        self.assertIn('"signatureFontFamily", "signatureFontSize"', JS)
        self.assertNotIn('installSharedFontControls', JS)
        self.assertNotIn('osw-editor-font-toolbar', JS)
        self.assertNotIn('osw-editor-font-toolbar', CSS)
        # The shared fields extend CKEditor's View directly, without the
        # native labeled input component that painted overlapping labels.
        self.assertIn("View", ENTRY)
        self.assertIn("class FontInputView extends ui.View", JS)
        self.assertIn('class: ["osw-font-text-field"]', JS)
        self.assertNotIn('class: ["ck", "ck-input", "ck-input-text"]', JS)
        self.assertIn(".osw-ckeditor-font-input input.osw-font-text-field", CSS)

    def test_email_safe_families_and_legacy_fallbacks(self):
        block = JS.split("const SIGNATURE_FONT_FAMILIES = [", 1)[1].split("\n    ];", 1)[0]
        families = dict(re.findall(r'\["([^"]+)", "([^"]+)"\]', block))
        self.assertEqual(list(families), [
            "Arial", "Arial Black", "Calibri", "Cambria", "Courier New",
            "Garamond", "Georgia", "Helvetica", "Lucida Sans Unicode",
            "Palatino Linotype", "Segoe UI", "Tahoma", "Times New Roman",
            "Trebuchet MS", "Verdana",
        ])
        legacy_stacks = {
            "Arial": "Arial, Helvetica, sans-serif",
            "Calibri": "Calibri, Arial, sans-serif",
            "Courier New": "Courier New, Courier, monospace",
            "Georgia": "Georgia, serif",
            "Tahoma": "Tahoma, Geneva, sans-serif",
            "Times New Roman": "Times New Roman, Times, serif",
            "Trebuchet MS": "Trebuchet MS, Helvetica, sans-serif",
            "Verdana": "Verdana, Geneva, sans-serif",
        }
        for name, stack in families.items():
            with self.subTest(font=name):
                self.assertTrue(stack.startswith(name + ","))
                self.assertRegex(stack, r", (?:sans-serif|serif|monospace)$")
                self.assertEqual(stack, legacy_stacks.get(name, stack))
        self.assertIn('createInputControl(locale', JS)
        # A searchable, scrollable ARIA listbox replaces the unstyleable
        # native datalist. Options are rendered on demand when opened.
        self.assertIn('listbox = document.createElement("div")', JS)
        self.assertIn('listbox.setAttribute("role", "listbox")', JS)
        self.assertIn('matches = options.filter(name => name.toLowerCase().includes(query))', JS)
        self.assertIn('input.setAttribute("aria-expanded", "true")', JS)
        self.assertIn(".osw-font-listbox", CSS)
        self.assertIn("overflow-y: auto;", CSS)
        self.assertNotIn('document.createElement("datalist")', JS)
        self.assertIn('editor.execute(commandName, { value: parsed })', JS)
        self.assertIn('supportAllValues: true', JS)

    def test_custom_size_goes_through_original_font_size_command(self):
        self.assertIn('[8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36]', JS)
        self.assertNotIn('dropdown.panelView.children.add(input)', JS)
        self.assertIn('editor.execute(commandName, { value: parsed })', JS)
        self.assertIn('input.setAttribute("inputmode", "decimal")', JS)
        self.assertIn('supportAllValues: true', JS)
        self.assertIn('match[1].replace(",", ".")', JS)
        self.assertIn('size < 6 || size > 96', JS)


if __name__ == "__main__":
    unittest.main()
