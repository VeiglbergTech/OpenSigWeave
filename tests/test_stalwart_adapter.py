"""Contract tests for the Stalwart MTA-hook adapter."""
import unittest

from adapters.stalwart import build_stalwart_hook_response


def request(headers, contents):
    return {
        "context": {"stage": "data"},
        "envelope": {"from": {"address": "sender@example.org"}, "to": []},
        "message": {"headers": headers, "contents": contents, "size": len(contents)},
    }


class StalwartAdapterTests(unittest.TestCase):
    def test_injects_into_simple_html_and_adds_idempotency_header(self):
        payload = request(
            [["From", " sender@example.org\r\n"], ["Content-Type", " text/html; charset=utf-8\r\n"]],
            "<html><body>Hello</body></html>",
        )
        result = build_stalwart_hook_response(payload, {
            "html": "<p>Signature</p>", "inject_on_replies": False,
        })
        changes = result["modifications"]
        body = next(item["value"] for item in changes if item["type"] == "replaceContents")
        self.assertIn("<p>Signature</p>", body)
        self.assertIn("X-OpenSigWeave-Injected", [item.get("name") for item in changes])

    def test_reports_top_level_mime_header_changes(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/html; charset=utf-8\r\n"]],
            "<html><body>Grüße</body></html>",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signatur äöü</p>"})
        changes = result["modifications"]
        added_headers = {
            item["name"].casefold(): item["value"]
            for item in changes
            if item["type"] == "addHeader"
        }
        self.assertEqual(added_headers["content-transfer-encoding"], "8bit")
        self.assertEqual(added_headers["mime-version"], "1.0")

    def test_injects_into_both_multipart_alternative_text_parts(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/alternative; boundary="b1"\r\n'],
            ],
            "--b1\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            "--b1\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<html><body>Hello</body></html>\r\n"
            "--b1--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        self.assertIn("Signature", body)
        self.assertIn("text/plain", body)
        self.assertIn("text/html", body)

    def test_leaves_signed_messages_unchanged(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", ' multipart/signed; boundary="b1"; protocol="application/pgp-signature"\r\n']],
            "--b1\r\nContent-Type: text/plain\r\n\r\nHello\r\n--b1--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertEqual(result, {"action": "accept"})

    def test_signed_attachment_does_not_block_outer_message_injection(self):
        payload = request(
            [
                ["From", " sender@example.org\\r\\n"],
                ["MIME-Version", " 1.0\\r\\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\\r\\n'],
            ],
            "--outer\\r\\nContent-Type: text/plain; charset=utf-8\\r\\n\\r\\nHello\\r\\n"
            "--outer\\r\\nContent-Type: application/pkcs7-mime; name=signature.p7m\\r\\n"
            "Content-Disposition: attachment; filename=signature.p7m\\r\\n"
            "Content-Transfer-Encoding: base64\\r\\n\\r\\nQUJDRA==\\r\\n"
            "--outer--\\r\\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        changes = result.get("modifications", [])
        self.assertIn("replaceContents", [item["type"] for item in changes])
        body = next(item["value"] for item in changes if item["type"] == "replaceContents")
        self.assertIn("QUJDRA==", body)

    def test_does_not_duplicate_when_marker_header_exists(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"],
             ["X-OpenSigWeave-Injected", " true\r\n"]],
            "Hello",
        )
        self.assertEqual(
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"}),
            {"action": "accept"},
        )

    def test_skips_replies_when_domain_policy_disables_reply_injection(self):
        payload = request(
            [["From", " sender@example.org\r\n"], ["In-Reply-To", " <message@example.org>\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"]],
            "Reply",
        )
        self.assertEqual(
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>", "inject_on_replies": False}),
            {"action": "accept"},
        )

    def test_removes_control_headers_when_no_signature_is_selected(self):
        payload = request(
            [["From", " sender@example.org\r\n"], ["X-OpenSigWeave-Mode", " PROFESSIONAL\r\n"]],
            "Hello",
        )
        result = build_stalwart_hook_response(payload, {"html": ""})
        self.assertEqual(result["modifications"], [
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Mode", "index": 0}
        ])

    def test_rejects_non_data_stage(self):
        payload = request([], "Hello")
        payload["context"]["stage"] = "mail"
        with self.assertRaises(ValueError):
            build_stalwart_hook_response(payload, {"html": ""})


    def test_preserves_inline_images_and_file_attachments(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            'Content-Type: multipart/related; boundary="related"\r\n\r\n'
            "--related\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            '<html><body><img src="cid:logo@example.org"></body></html>\r\n'
            "--related\r\n"
            "Content-Type: image/png\r\n"
            "Content-Transfer-Encoding: base64\r\n"
            "Content-ID: <logo@example.org>\r\n"
            "Content-Disposition: inline; filename=logo.png\r\n\r\n"
            "aW1hZ2UtZGF0YQ==\r\n"
            "--related--\r\n"
            "--outer\r\n"
            "Content-Type: application/pdf\r\n"
            "Content-Transfer-Encoding: base64\r\n"
            "Content-Disposition: attachment; filename=report.pdf\r\n\r\n"
            "cGRmLWRhdGE=\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        body = next(
            item["value"] for item in result["modifications"]
            if item["type"] == "replaceContents"
        )
        self.assertIn("<p>Signature</p>", body)
        self.assertIn("Content-ID: <logo@example.org>", body)
        self.assertIn("Content-Disposition: inline; filename=logo.png", body)
        self.assertIn("aW1hZ2UtZGF0YQ==", body)
        self.assertIn("Content-Disposition: attachment; filename=report.pdf", body)
        self.assertIn("cGRmLWRhdGE=", body)


    def test_does_not_modify_attached_forwarded_messages(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body>Outer message</body></html>\r\n"
            "--outer\r\n"
            "Content-Type: message/rfc822\r\n"
            "Content-Disposition: attachment; filename=forwarded.eml\r\n\r\n"
            "From: colleague@example.org\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body>Forwarded message</body></html>\r\n"
            "--outer--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        body = next(
            item["value"] for item in result["modifications"]
            if item["type"] == "replaceContents"
        )
        self.assertIn("Outer message", body)
        self.assertIn("<p>Signature</p>", body)
        self.assertIn("Forwarded message", body)
        forwarded = body.split("Content-Type: message/rfc822", 1)[1]
        self.assertNotIn("<p>Signature</p>", forwarded)


if __name__ == "__main__":
    unittest.main()
