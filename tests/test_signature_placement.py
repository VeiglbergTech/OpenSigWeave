"""Tests for conservative placement before quoted reply history."""
import unittest

from core.signature_placement import (
    find_html_quote_boundary,
    find_plain_quote_boundary,
    insert_signature,
)


class SignaturePlacementTests(unittest.TestCase):
    def test_german_outlook_headers_are_detected(self):
        body = "Antwort\n\nVon: Max\nGesendet: Montag\nAn: Ada\nBetreff: Frage\n"
        self.assertEqual(body[find_plain_quote_boundary(body):].splitlines()[0], "Von: Max")

    def test_english_reply_separator_is_detected(self):
        body = "Thanks for your message.\n\nOn Monday, Max wrote:\n> Old text"
        self.assertEqual(body[find_plain_quote_boundary(body):], "On Monday, Max wrote:\n> Old text")

    def test_plain_quoted_lines_are_detected(self):
        body = "New answer\n\n> quoted text"
        result, detected = insert_signature(body, "Signature", html=False)
        self.assertTrue(detected)
        self.assertLess(result.index("Signature"), result.index("> quoted text"))
        self.assertEqual(result, "New answer\n\nSignature\n\n> quoted text")

    def test_gmail_quote_container_is_detected(self):
        body = '<div>New answer</div><div class="gmail_quote">Old answer</div>'
        boundary = find_html_quote_boundary(body)
        self.assertTrue(body.startswith('<div class="gmail_quote"', boundary))

    def test_blockquote_is_detected_case_insensitively(self):
        body = "<div>New answer</div><BLOCKQUOTE>Old answer</BLOCKQUOTE>"
        result, detected = insert_signature(body, "<p>Signature</p>", html=True)
        self.assertTrue(detected)
        self.assertLess(result.index("Signature"), result.index("<BLOCKQUOTE>"))

    def test_unknown_format_uses_safe_append_fallback(self):
        body = "A new message without a recognised quote"
        result, detected = insert_signature(body, "Signature", html=False)
        self.assertFalse(detected)
        self.assertEqual(result, body + "\n\nSignature")

    def test_html_fallback_keeps_signature_inside_body(self):
        body = "<html><body>New message</body></html>"
        result, detected = insert_signature(body, "<p>Signature</p>", html=True)
        self.assertFalse(detected)
        self.assertLess(result.index("Signature"), result.index("</body>"))


    def test_thunderbird_html_cite_prefix_is_detected(self):
        body = (
            "<html><body><div>Neue Antwort</div>"
            '<div class="moz-cite-prefix">Am Dienstag schrieb Max:</div>'
            '<blockquote type="cite">Alte Nachricht</blockquote>'
            "</body></html>"
        )
        result, detected = insert_signature(body, "<p>Signatur</p>", html=True)
        self.assertTrue(detected)
        self.assertLess(result.index("Signatur"), result.index('class="moz-cite-prefix"'))
        self.assertLess(result.index("Signatur"), result.index("<blockquote type=\"cite\""))

    def test_outlook_html_reply_container_is_detected(self):
        body = (
            "<html><body><div>Neue Antwort</div>"
            '<div id="divRplyFwdMsg"><b>From:</b> Max<br>Sent: Monday</div>'
            "</body></html>"
        )
        result, detected = insert_signature(body, "<p>Signatur</p>", html=True)
        self.assertTrue(detected)
        self.assertLess(result.index("Signatur"), result.index('id="divRplyFwdMsg"'))


if __name__ == "__main__":
    unittest.main()
