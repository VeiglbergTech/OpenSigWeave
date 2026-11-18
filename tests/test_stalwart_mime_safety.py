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
                ["From", " sender@example.org\\r\\n"],
                ["MIME-Version", " 1.0\\r\\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\\r\\n'],
            ],
            "--outer\\r\\n"
            "Content-Type: text/plain; charset=utf-8\\r\\n\\r\\nOuter body\\r\\n"
            "--outer\\r\\n"
            "Content-Type: message/rfc822\\r\\n\\r\\n"
            "From: forwarded@example.org\\r\\n"
            "Content-Type: text/plain; charset=utf-8\\r\\n\\r\\n"
            "Forwarded original body\\r\\n"
            "--outer--\\r\\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        body = next(
            item["value"] for item in result.get("modifications", [])
            if item["type"] == "replaceContents"
        )
        self.assertIn("Outer body", body)
        self.assertIn("<p>Signature</p>", body)
        self.assertIn("Forwarded original body", body)
        forwarded = body.split("Content-Type: message/rfc822", 1)[1]
        self.assertNotIn("<p>Signature</p>", forwarded)


if __name__ == "__main__":
    unittest.main()
