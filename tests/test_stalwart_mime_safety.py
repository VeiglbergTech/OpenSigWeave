"""Safety regression tests for nested signed MIME structures in Stalwart."""
import unittest

from adapters.stalwart import build_stalwart_hook_response


def request(headers, contents):
    return {
        "context": {"stage": "data"},
        "envelope": {"from": {"address": "sender@example.org"}, "to": []},
        "message": {"headers": headers, "contents": contents, "size": len(contents)},
    }


class NestedMimeSafetyTests(unittest.TestCase):
    def test_nested_inline_signed_part_causes_message_to_be_left_unchanged(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            'Content-Type: multipart/signed; boundary="signed"; protocol="application/pgp-signature"\r\n\r\n'
            "--signed\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nSigned content\r\n"
            "--signed\r\n"
            "Content-Type: application/pgp-signature\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\n"
            "c2lnbmF0dXJl\r\n"
            "--signed--\r\n"
            "--outer\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nOuter content\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertEqual(result, {"action": "accept"})

    def test_nested_encrypted_multipart_is_left_unchanged(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            'Content-Type: multipart/encrypted; boundary="encrypted"; protocol="application/pgp-encrypted"\r\n\r\n'
            "--encrypted\r\n"
            "Content-Type: application/pgp-encrypted\r\n\r\nVersion: 1\r\n"
            "--encrypted\r\n"
            "Content-Type: application/octet-stream\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\n"
            "ZW5jcnlwdGVk\r\n"
            "--encrypted--\r\n"
            "--outer\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nOuter text\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertEqual(result, {"action": "accept"})

    def test_nested_multipart_missing_inner_boundary_is_left_unchanged(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            'Content-Type: multipart/alternative; boundary="inner"\r\n\r\n'
            "--inner\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nInner text\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertEqual(result, {"action": "accept"})


    def test_inline_message_rfc822_is_not_modified(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\nOuter body\r\n"
            "--outer\r\n"
            "Content-Type: message/rfc822\r\n\r\n"
            "From: forwarded@example.org\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Forwarded original body\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        body = next(
            item["value"] for item in result.get("modifications", [])
            if item["type"] == "replaceContents"
        )
        self.assertIn("Outer body", body)
        self.assertIn("Signature", body)
        self.assertIn("Forwarded original body", body)
        forwarded = body.split("Content-Type: message/rfc822", 1)[1]
        self.assertNotIn("Signature", forwarded)


    def test_inline_smime_and_pgp_encrypted_parts_block_mutation(self):
        crlf = chr(13) + chr(10)
        protected_parts = [
            (
                "application/pkcs7-mime; smime-type=enveloped-data",
                "Content-Transfer-Encoding: base64" + crlf + crlf + "ZW5jcnlwdGVk",
            ),
            (
                "application/pgp-encrypted",
                "Content-Transfer-Encoding: base64" + crlf + crlf + "ZW5jcnlwdGVk",
            ),
        ]
        for content_type, extra_headers in protected_parts:
            with self.subTest(content_type=content_type):
                headers = [
                    ["From", " sender@example.org" + crlf],
                    ["MIME-Version", " 1.0" + crlf],
                    ["Content-Type", ' multipart/mixed; boundary="outer"' + crlf],
                ]
                contents = (
                    "--outer" + crlf
                    + "Content-Type: text/plain; charset=utf-8" + crlf + crlf
                    + "Outer text" + crlf
                    + "--outer" + crlf
                    + "Content-Type: " + content_type + crlf + extra_headers + crlf
                    + "--outer--" + crlf
                )
                result = build_stalwart_hook_response(
                    request(headers, contents), {"html": "<p>Signature</p>"}
                )
                self.assertEqual(result, {"action": "accept"})


    def test_skips_entire_multipart_alternative_with_unknown_charset(self):
        payload = request(
            [
                ["From", " sender@example.org\\r\\n"],
                ["MIME-Version", " 1.0\\r\\n"],
                ["Content-Type", ' multipart/alternative; boundary="alt"\\r\\n'],
            ],
            "--alt\\r\\n"
            "Content-Type: text/plain; charset=x-unknown-codec\\r\\n\\r\\n"
            "Opaque plain body\\r\\n"
            "--alt\\r\\n"
            "Content-Type: text/html; charset=utf-8\\r\\n\\r\\n"
            "<html><body>Hello</body></html>\\r\\n"
            "--alt--\\r\\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertNotIn("replaceContents", [item["type"] for item in result.get("modifications", [])])

    def test_ascii_part_skips_signature_that_cannot_be_encoded(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["Content-Type", " text/plain; charset=us-ascii\r\n"],
            ],
            "ASCII-only body\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Grüße</p>"})
        self.assertEqual(result, {"action": "accept"})

if __name__ == "__main__":
    unittest.main()
