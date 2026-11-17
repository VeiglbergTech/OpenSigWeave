import quopri
import unittest
import urllib.request
from adapters.postfix_milter import EngineRequestError, MAX_MESSAGE_BYTES, _NoRedirectHandler, fetch_signature, rewrite_message

class PostfixMilterAdapterTests(unittest.TestCase):
    def test_plain_text_signature_precedes_quote(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Antwort\n\nOn Monday Alice wrote:\n> previous", "<p>Viele Grüße</p>", inject_on_replies=True)
        self.assertTrue(result.changed)
        decoded_body = quopri.decodestring(result.body)
        self.assertLess(decoded_body.index("Viele Grüße".encode()), decoded_body.index(b"On Monday"))

    def test_html_signature_precedes_gmail_quote(self):
        result = rewrite_message([("Content-Type", "text/html; charset=utf-8")],
            b'<html><body><p>Reply</p><div class="gmail_quote">Old</div></body></html>',
            "<p>Signature</p>", inject_on_replies=True)
        self.assertTrue(result.changed)
        decoded_body = quopri.decodestring(result.body)
        self.assertLess(decoded_body.index(b"Signature"), decoded_body.index(b"gmail_quote"))

    def test_reply_policy_skips_injection(self):
        result = rewrite_message([("Content-Type", "text/plain"), ("In-Reply-To", "<old@example.org>")],
            b"Reply", "<p>Signature</p>", inject_on_replies=False)
        self.assertFalse(result.changed)
        self.assertEqual(result.body, b"Reply")

    def test_idempotency_marker_skips_message(self):
        result = rewrite_message([("Content-Type", "text/plain"), ("X-OpenSigWeave-Injected", "true")],
            b"Body", "<p>Signature</p>", inject_on_replies=True)
        self.assertFalse(result.changed)

    def test_signed_message_is_unchanged(self):
        headers = [("MIME-Version", "1.0"),
            ("Content-Type", 'multipart/signed; boundary="sig"; protocol="application/pgp-signature"')]
        body = (b"--sig\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
                b"--sig\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n--sig--\r\n")
        result = rewrite_message(headers, body, "<p>Signature</p>", inject_on_replies=True)
        self.assertFalse(result.changed)
        self.assertEqual(result.body, body)

    def test_binary_attachment_payload_is_preserved(self):
        import base64
        from email import policy
        from email.parser import BytesParser
        binary = bytes(range(256))
        encoded = base64.b64encode(binary)
        headers = [("MIME-Version", "1.0"), ("Content-Type", 'multipart/mixed; boundary="mix"')]
        body = (b"--mix\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            b"--mix\r\nContent-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=data.bin\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n" + encoded + b"\r\n--mix--\r\n")
        result = rewrite_message(headers, body, "<p>Signature</p>", inject_on_replies=True)
        self.assertTrue(result.changed)
        parsed = BytesParser(policy=policy.default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=mix\r\n\r\n" + result.body)
        attachment = next(part for part in parsed.walk() if part.get_filename() == "data.bin")
        self.assertEqual(attachment.get_payload(decode=True), binary)

    def test_engine_url_requires_https(self):
        with self.assertRaises(EngineRequestError):
            fetch_signature("http://engine.local", "x" * 40, "sender@example.org")

    def test_engine_redirects_are_never_followed(self):
        request = urllib.request.Request("https://engine.example/api")
        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "http://attacker.example/collect"
        ))

    def test_message_size_is_bounded(self):
        with self.assertRaises(ValueError):
            rewrite_message([], b"x" * (MAX_MESSAGE_BYTES + 1), "<p>Sig</p>", inject_on_replies=True)

if __name__ == "__main__":
    unittest.main()
