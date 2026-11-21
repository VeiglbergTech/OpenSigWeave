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

    def test_persisted_asset_urls_survive_but_data_and_blob_urls_do_not(self):
        asset_url = "https://signature.example.org/signature-assets/" + ("a" * 64) + ".webp"
        source = (
            f'<img src="{asset_url}" alt="Persisted">'
            '<img src="data:image/png;base64,AAAA" alt="Inline">'
            '<img src="blob:https://signature.example.org/temporary" alt="Blob">'
        )
        result = sanitize_signature_html(source)
        self.assertIn(asset_url, result)
        self.assertNotIn("data:image", result)
        self.assertNotIn("blob:", result)

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

    def test_resized_image_width_survives_email_sanitization(self):
        source = (
            '<figure class="image image_resized" style="width: 220px;">'
            '<img src="https://example.org/logo.png" alt="Brand"></figure>'
        )
        result = sanitize_signature_html(source)
        self.assertIn('width="220px"', result)
        self.assertIn("width: 220px", result)
        self.assertNotIn("<figure", result)
        malicious = sanitize_signature_html(
            '<figure class="image_resized" style="width: 200px">'
            '<img src="javascript:alert(1)" onclick="alert(1)"></figure>'
        )
        self.assertNotIn("javascript:", malicious)
        self.assertNotIn("onclick", malicious)

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
