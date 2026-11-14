"""Email-signature HTML compatibility checks for the editor evaluation.

These tests cover the markup the current server-side sanitizer is expected to retain.
They intentionally do not claim that arbitrary Outlook VML or conditional comments
survive: nh3 currently strips comments and disallowed VML elements.
"""
import unittest

from signature_sanitizer import sanitize_signature_html


class SignatureEditorCompatibilityTests(unittest.TestCase):
    def test_nested_tables_and_layout_attributes_survive(self):
        source = (
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
            'style="border-collapse: collapse; width: 600px;">'
            '<tbody><tr><td width="180" valign="top" style="padding: 8px;">'
            '<table><tr><td>Logo</td></tr></table>'
            '</td><td valign="middle">Name</td></tr></tbody></table>'
        )
        result = sanitize_signature_html(source)
        self.assertIn("<table", result)
        self.assertIn("<tbody>", result)
        self.assertIn("<tr>", result)
        self.assertIn("<td", result)
        self.assertIn('width="600"', result)
        self.assertIn('valign="top"', result)
        self.assertIn("border-collapse", result)
        self.assertIn("padding", result)
        self.assertIn("Logo", result)

    def test_common_inline_email_styles_survive(self):
        source = (
            '<p style="font-family: Arial, sans-serif; font-size: 12px; '
            'font-weight: bold; color: #333333; line-height: 18px; '
            'text-align: center; margin: 0; padding: 4px;">Signature</p>'
        )
        result = sanitize_signature_html(source)
        for prop in ("font-family", "font-size", "font-weight", "color",
                     "line-height", "text-align", "margin", "padding"):
            with self.subTest(property=prop):
                self.assertIn(prop, result)
        self.assertIn("Signature", result)

    def test_links_images_and_template_tokens_survive(self):
        source = (
            '<div><a href="https://example.org" title="Website">Website</a>'
            '<img src="https://example.org/logo.png" alt="Company" width="120" height="40">'
            '{{ first_name }} {{ group:staff }}</div>'
        )
        result = sanitize_signature_html(source)
        self.assertIn('href="https://example.org"', result)
        self.assertIn('src="https://example.org/logo.png"', result)
        self.assertIn('alt="Company"', result)
        self.assertIn('width="120"', result)
        self.assertIn("{{ first_name }}", result)
        self.assertIn("{{ group:staff }}", result)

    def test_unsafe_markup_is_removed_without_losing_signature_text(self):
        source = (
            '<p onclick="alert(1)" style="color: red; position: fixed">'
            '<script>alert(1)</script><a href="javascript:alert(1)">Contact</a>'
            '</p>'
        )
        result = sanitize_signature_html(source)
        self.assertIn("Contact", result)
        self.assertNotIn("onclick", result)
        self.assertNotIn("javascript:", result)
        self.assertNotIn("<script", result)
        self.assertNotIn("position", result)

    def test_outlook_conditional_vml_is_not_currently_preserved(self):
        source = (
            '<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml">'
            '<v:textbox>Outlook only</v:textbox></v:roundrect><![endif]-->'
            '<p>Visible fallback</p>'
        )
        result = sanitize_signature_html(source)
        self.assertIn("Visible fallback", result)
        self.assertNotIn("roundrect", result)
        self.assertNotIn("Outlook only", result)
        self.assertNotIn("[if mso]", result)


if __name__ == "__main__":
    unittest.main()
