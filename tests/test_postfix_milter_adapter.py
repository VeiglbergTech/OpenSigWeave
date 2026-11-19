import json
import quopri
import unittest
import urllib.request
from unittest.mock import patch
import ssl
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

    def test_client_supplied_idempotency_header_does_not_bypass_injection(self):
        result = rewrite_message([("Content-Type", "text/plain"), ("X-OpenSigWeave-Injected", "true")],
            b"Body", "<p>Signature</p>", inject_on_replies=True)
        self.assertTrue(result.changed)
        self.assertIn(b"Signature", result.body)

    def test_client_supplied_html_comment_does_not_bypass_injection(self):
        result = rewrite_message([("Content-Type", "text/html; charset=utf-8")],
            b"<html><body>Hello<!-- OpenSigWeave:INJECTED --></body></html>",
            "<p>Signature</p>", inject_on_replies=True)
        self.assertTrue(result.changed)
        self.assertIn(b"Signature", result.body)

    def test_signed_message_is_unchanged(self):
        headers = [("MIME-Version", "1.0"),
            ("Content-Type", 'multipart/signed; boundary="sig"; protocol="application/pgp-signature"')]
        body = (b"--sig\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
                b"--sig\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n--sig--\r\n")
        result = rewrite_message(headers, body, "<p>Signature</p>", inject_on_replies=True)
        self.assertFalse(result.changed)
        self.assertEqual(result.body, body)

    def test_nested_signed_part_keeps_entire_message_unchanged(self):
        headers = [("MIME-Version", "1.0"),
            ("Content-Type", 'multipart/mixed; boundary="outer"')]
        body = (
            b"--outer\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nCover note\r\n"
            b"--outer\r\nContent-Type: multipart/signed; boundary=\"inner\"; "
            b"protocol=\"application/pgp-signature\"\r\n\r\n"
            b"--inner\r\nContent-Type: text/plain\r\n\r\nSigned content\r\n"
            b"--inner\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n"
            b"--inner--\r\n--outer--\r\n"
        )
        result = rewrite_message(headers, body, "<p>Signature</p>", inject_on_replies=True)
        self.assertFalse(result.changed)
        self.assertEqual(result.body, body)

    def test_signed_attached_email_blocks_outer_message_rewrite_to_preserve_raw_bytes(self):
        headers = [("MIME-Version", "1.0"),
            ("Content-Type", 'multipart/mixed; boundary="outer"')]
        attached_email = (
            b"From: sender@example.org\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: multipart/signed; boundary="inner"; '
            b'protocol="application/pgp-signature"\r\n\r\n'
            b"--inner\r\nContent-Type: text/plain\r\n\r\nSigned content\r\n"
            b"--inner\r\nContent-Type: application/pgp-signature\r\n\r\nsignature\r\n"
            b"--inner--\r\n"
        )
        body = (
            b"--outer\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nCover note\r\n"
            b"--outer\r\nContent-Type: message/rfc822\r\n"
            b"Content-Disposition: attachment; filename=forwarded.eml\r\n\r\n"
            + attached_email +
            b"\r\n--outer--\r\n"
        )
        result = rewrite_message(headers, body, "<p>Signature</p>", inject_on_replies=True)
        self.assertFalse(result.changed)
        # Exact equality is intentional: MIME parsing and regeneration can
        # refold nested message headers and invalidate the attached signature.
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


    def test_engine_client_uses_versioned_endpoint_and_selection_contract(self):
        payload = {
            "html": "<p>Signature</p>",
            "inject_on_replies": True,
            "trim_whitespace": False,
            "strip_device_signatures": True,
        }

        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return json.dumps(payload).encode("utf-8")

        class Opener:
            def __init__(self):
                self.request = None
                self.timeout = None
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        opener = Opener()
        with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=opener):
            result = fetch_signature(
                "https://engine.example/",
                "k" * 40,
                "User+tag@Example.org",
                mode="PROFESSIONAL",
                signature_name="Sales",
                signature_code="A1B2",
                timeout=3.5,
            )

        self.assertEqual(result, payload)
        self.assertEqual(
            opener.request.full_url,
            "https://engine.example/api/v1/engine/signatures/User%2Btag%40Example.org",
        )
        headers = {name.casefold(): value for name, value in opener.request.header_items()}
        self.assertEqual(headers["x-engine-key"], "k" * 40)
        self.assertEqual(headers["x-opensigweave-mode"], "PROFESSIONAL")
        self.assertEqual(headers["x-opensigweave-signature"], "Sales")
        self.assertEqual(headers["x-opensigweave-signature-code"], "A1B2")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(opener.timeout, 3.5)

    def test_engine_client_enables_system_ca_and_hostname_verification(self):
        payload = {
            "html": "",
            "inject_on_replies": True,
            "trim_whitespace": True,
            "strip_device_signatures": False,
        }

        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return json.dumps(payload).encode("utf-8")

        class Opener:
            def open(self, request, timeout):
                return Response()

        with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=Opener()) as build:
            fetch_signature("https://engine.example", "k" * 40, "sender@example.org")

        handlers = build.call_args.args
        https_handler = next(handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler))
        context = https_handler._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


    def test_engine_client_rejects_invalid_response_contract(self):
        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return b'{"html": 42}'

        class Opener:
            def open(self, request, timeout):
                return Response()

        with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(EngineRequestError):
                fetch_signature("https://engine.example", "k" * 40, "sender@example.org")

    def test_engine_client_rejects_non_boolean_policy_fields(self):
        for field in ("inject_on_replies", "trim_whitespace", "strip_device_signatures"):
            payload = {
                "html": "<p>Signature</p>",
                "inject_on_replies": False,
                "trim_whitespace": True,
                "strip_device_signatures": False,
            }
            payload[field] = "false"

            class Response:
                status = 200
                def __enter__(self):
                    return self
                def __exit__(self, *_args):
                    return False
                def read(self, limit):
                    return json.dumps(payload).encode("utf-8")

            class Opener:
                def open(self, request, timeout):
                    return Response()

            with self.subTest(field=field):
                with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=Opener()):
                    with self.assertRaises(EngineRequestError):
                        fetch_signature("https://engine.example", "k" * 40, "sender@example.org")

    def test_engine_client_rejects_missing_policy_fields(self):
        payload = {"html": "<p>Signature</p>", "inject_on_replies": False}

        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return json.dumps(payload).encode("utf-8")

        class Opener:
            def open(self, request, timeout):
                return Response()

        with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(EngineRequestError):
                fetch_signature("https://engine.example", "k" * 40, "sender@example.org")

    def test_engine_client_rejects_oversized_response(self):
        from adapters.postfix_milter import MAX_ENGINE_RESPONSE_BYTES

        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return b"x" * limit

        class Opener:
            def open(self, request, timeout):
                return Response()

        with patch("adapters.postfix_milter.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(EngineRequestError):
                fetch_signature("https://engine.example", "k" * 40, "sender@example.org")


    def test_trim_whitespace_removes_trailing_plain_editor_whitespace(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Reply  \r\n\r\n", "<p>Signature</p>", inject_on_replies=True,
            trim_whitespace=True)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertTrue(result.changed)
        self.assertNotIn("Reply  ", decoded)

    def test_trim_whitespace_false_preserves_trailing_plain_whitespace(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Reply  \r\n\r\n", "<p>Signature</p>", inject_on_replies=True,
            trim_whitespace=False)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertTrue(result.changed)
        self.assertIn("Reply  ", decoded)

    def test_strip_device_signature_plain_text(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Reply\r\nSent from my iPhone", "<p>Signature</p>",
            inject_on_replies=True, strip_device_signatures=True)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertTrue(result.changed)
        self.assertNotIn("Sent from my iPhone", decoded)

    def test_strip_device_signature_html(self):
        result = rewrite_message([("Content-Type", "text/html; charset=utf-8")],
            b"<html><body>Reply<br>Get Outlook for Android</body></html>",
            "<p>Signature</p>", inject_on_replies=True, strip_device_signatures=True)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertTrue(result.changed)
        self.assertNotIn("Get Outlook for Android", decoded)

    def test_whitespace_and_device_policies_are_disabled_by_default(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Reply\r\nSent from my iPhone\r\n  ", "<p>Signature</p>",
            inject_on_replies=True)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertIn("Sent from my iPhone", decoded)

    def test_trim_whitespace_false_preserves_signature_whitespace(self):
        result = rewrite_message([("Content-Type", "text/plain; charset=utf-8")],
            b"Reply", "<p> Signature </p>", inject_on_replies=True,
            trim_whitespace=False)
        decoded = quopri.decodestring(result.body).decode("utf-8")
        self.assertTrue(result.changed)
        self.assertIn(" Signature ", decoded)
    def test_message_size_is_bounded(self):
        with self.assertRaises(ValueError):
            rewrite_message([], b"x" * (MAX_MESSAGE_BYTES + 1), "<p>Sig</p>", inject_on_replies=True)

if __name__ == "__main__":
    unittest.main()
