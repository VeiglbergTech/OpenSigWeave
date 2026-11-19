"""Contract tests for the Stalwart MTA-hook adapter."""
import unittest
from quopri import decodestring

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

    def test_body_idempotency_marker_prevents_partial_alternative_mutation(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/alternative; boundary="alt"\r\n'],
            ],
            "--alt\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n"
            "Plain body without marker\r\n"
            "--alt\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body>Hello<!-- OpenSigWeave:INJECTED --></body></html>\r\n"
            "--alt--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertEqual(result, {"action": "accept"})

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
        self.assertEqual(added_headers["content-transfer-encoding"], "quoted-printable")
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

    def test_skips_malformed_multipart_without_closing_boundary(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/alternative; boundary="broken"\r\n'],
            ],
            "--broken\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body>Hello</body></html>\r\n",
        )
        self.assertEqual(
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"}),
            {"action": "accept"},
        )

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
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
            ],
            "--outer\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            "--outer\r\nContent-Type: application/pkcs7-mime; name=signature.p7m\r\n"
            "Content-Disposition: attachment; filename=signature.p7m\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\nQUJDRA==\r\n"
            "--outer--\r\n",
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

    def test_removes_duplicate_control_headers_in_descending_occurrence_order(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["X-OpenSigWeave-Mode", " PROFESSIONAL\r\n"],
                ["X-OpenSigWeave-Mode", " INTERNAL\r\n"],
                ["X-OpenSigWeave-Signature", " Old\r\n"],
                ["X-OpenSigWeave-Mode", " NONE\r\n"],
                ["X-OpenSigWeave-Signature", " New\r\n"],
            ],
            "Hello",
        )
        result = build_stalwart_hook_response(payload, {"html": ""})
        self.assertEqual(result["modifications"], [
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Mode", "index": 2},
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Mode", "index": 1},
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Mode", "index": 0},
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Signature", "index": 1},
            {"type": "deleteHeader", "name": "X-OpenSigWeave-Signature", "index": 0},
        ])

    def test_rejects_bare_carriage_return_in_header_value(self):
        payload = request([["From", " sender@example.org\rInjected: yes\r\n"]], "Hello")
        with self.assertRaises(ValueError):
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})

    def test_rejects_malformed_headers_even_when_no_signature_is_selected(self):
        invalid_headers = [
            [["From", " sender@example.org\rInjected: yes\r\n"]],
            [["Bad Header Name", " value\r\n"]],
            [["From", " sender@example.org\nInjected: yes\n"]],
            [["From", None]],
        ]
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                payload = request(headers, "Hello")
                with self.assertRaises(ValueError):
                    build_stalwart_hook_response(payload, {"html": ""})

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
        decoded_body = decodestring(body.encode("utf-8")).decode("utf-8", errors="replace")
        self.assertIn("<p>Signature</p>", decoded_body)
        from email import policy
        from email.parser import BytesParser

        parsed = BytesParser(policy=policy.default).parsebytes(
            ('MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=outer\r\n\r\n'
             + body).encode("utf-8")
        )
        inline_image = next(part for part in parsed.walk() if part.get("Content-ID") == "<logo@example.org>")
        pdf_attachment = next(part for part in parsed.walk() if part.get_filename() == "report.pdf")
        self.assertEqual(inline_image.get_payload(decode=True), b"image-data")
        self.assertEqual(pdf_attachment.get_payload(decode=True), b"pdf-data")
        self.assertIn("<p>Signature</p>", decoded_body)


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


    def _round_trip_text_part(self, headers, contents, expected_text):
        from email import policy
        from email.parser import BytesParser

        payload = request(headers, contents)
        result = build_stalwart_hook_response(payload, {"html": "<p>Signatur äöü</p>"})
        body = next(
            item["value"] for item in result["modifications"]
            if item["type"] == "replaceContents"
        )
        effective_headers = [(name, value) for name, value in headers]
        for change in result["modifications"]:
            if change["type"] == "deleteHeader":
                target = change["name"].casefold()
                for index, (name, _) in enumerate(effective_headers):
                    if name.casefold() == target:
                        effective_headers.pop(index)
                        break
            elif change["type"] == "addHeader":
                effective_headers.append((change["name"], " " + change["value"] + "\r\n"))
        raw_headers = "".join(
            name + ":" + value.rstrip("\r\n") + "\r\n"
            for name, value in effective_headers
        )
        parsed = BytesParser(policy=policy.default).parsebytes(
            (raw_headers + "\r\n" + body).encode("utf-8")
        )
        self.assertIn(expected_text, parsed.get_content())

    def test_decodes_and_reencodes_quoted_printable_text(self):
        self._round_trip_text_part(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"],
             ["Content-Transfer-Encoding", " quoted-printable\r\n"]],
            "Gr=C3=BC=C3=9Fe aus M=C3=BCnchen",
            "Grüße aus München",
        )

    def test_decodes_and_reencodes_base64_text(self):
        self._round_trip_text_part(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"],
             ["Content-Transfer-Encoding", " base64\r\n"]],
            "R3LDvMOfZSBhdXMgTcO8bmNoZW4=",
            "Grüße aus München",
        )

    def test_preserves_legacy_charset_when_injecting(self):
        self._round_trip_text_part(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=windows-1252\r\n"],
             ["Content-Transfer-Encoding", " quoted-printable\r\n"]],
            "Gr=FC=DFe aus M=FCnchen",
            "Grüße aus München",
        )

    def test_handles_nested_alternative_inside_mixed_message(self):
        headers = [
            ["From", " sender@example.org\r\n"],
            ["MIME-Version", " 1.0\r\n"],
            ["Content-Type", ' multipart/mixed; boundary="outer"\r\n'],
        ]
        contents = (
            '--outer\r\nContent-Type: multipart/alternative; boundary="alt"\r\n\r\n'
            "--alt\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPlain body\r\n"
            "--alt\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<html><body>HTML body</body></html>\r\n"
            "--alt--\r\n"
            "--outer\r\nContent-Type: application/pdf\r\n"
            "Content-Disposition: attachment; filename=report.pdf\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\n"
            "cGRmLWRhdGE=\r\n--outer--\r\n"
        )
        result = build_stalwart_hook_response(request(headers, contents), {"html": "<p>Signature</p>"})
        body = next(
            item["value"] for item in result["modifications"]
            if item["type"] == "replaceContents"
        )
        self.assertIn("Plain body", body)
        self.assertIn("HTML body", body)
        self.assertIn("Signature", body)
        self.assertIn("cGRmLWRhdGE=", body)


    def test_preserves_decoded_binary_attachment_bytes(self):
        import base64
        from email import policy
        from email.parser import BytesParser

        binary = bytes(range(256)) + b"\x00\xff\x10OpenSigWeave"
        encoded = base64.b64encode(binary).decode("ascii")
        headers = [
            ["From", " sender@example.org\r\n"],
            ["MIME-Version", " 1.0\r\n"],
            ["Content-Type", ' multipart/mixed; boundary="binary-boundary"\r\n'],
        ]
        contents = (
            "--binary-boundary\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
            "--binary-boundary\r\nContent-Type: application/octet-stream\r\n"
            "Content-Disposition: attachment; filename=payload.bin\r\n"
            "Content-Transfer-Encoding: base64\r\n\r\n"
            + encoded + "\r\n--binary-boundary--\r\n"
        )
        result = build_stalwart_hook_response(request(headers, contents), {"html": "<p>Signature</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        parsed = BytesParser(policy=policy.default).parsebytes(
            ("MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=binary-boundary\r\n\r\n"
             + body).encode("utf-8")
        )
        attachment = next(part for part in parsed.walk() if part.get_filename() == "payload.bin")
        self.assertEqual(attachment.get_payload(decode=True), binary)

    def test_places_plain_signature_before_outlook_quoted_history(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"]],
            "Hallo Max,\n\ndanke für deine Nachricht.\n\n"
            "Von: Max Mustermann\nGesendet: Montag\nAn: Ada\nBetreff: Frage\n\n"
            "Hallo, kannst du helfen?",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Viele Gruesse</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        body = decodestring(body).decode("utf-8")
        self.assertLess(body.index("Viele Gruesse"), body.index("Von: Max Mustermann"))

    def test_places_html_signature_before_gmail_quote_container(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/html; charset=utf-8\r\n"]],
            '<html><body><div>Hallo Max,</div><div>danke für deine Nachricht.</div>'
            '<div class="gmail_quote">On Monday Max wrote:<blockquote>Old message</blockquote></div>'
            '</body></html>',
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Viele Gruesse</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        body = decodestring(body).decode("utf-8")
        self.assertLess(body.index("Viele Gruesse"), body.index('class="gmail_quote"'))

    def test_places_signature_before_plain_blockquote_lines(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"]],
            "Antworttext\n\n> Alte Nachricht\n> Noch eine Zeile",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signatur</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        self.assertLess(body.index("Signatur"), body.index("> Alte Nachricht"))

    def test_falls_back_to_end_without_recognised_quote_boundary(self):
        payload = request(
            [["From", " sender@example.org\r\n"],
             ["Content-Type", " text/plain; charset=utf-8\r\n"]],
            "Einfacher neuer Text ohne Zitat",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signatur</p>"})
        body = next(item["value"] for item in result["modifications"] if item["type"] == "replaceContents")
        normalized_body = decodestring(body).decode("utf-8").replace("\r\n", "\n").rstrip("\n")
        self.assertEqual(normalized_body, "Einfacher neuer Text ohne Zitat\n\nSignatur")


    def test_sogo_multipart_reply_places_signature_before_quoted_history(self):
        # SOGo emits multipart/alternative messages with its OpenGroupware MIME
        # boundary. Plain-text replies commonly quote prior lines with ">"; the
        # HTML part uses a structural blockquote when that markup is present.
        boundary = "OpenGroupware_org_NGMime-test"
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["User-Agent", " SOGoMail 5.12.9\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", f' multipart/alternative; boundary="{boundary}"\r\n'],
            ],
            f"--{boundary}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
            "Hallo Max,\n\ndanke für deine Nachricht.\n\n> Deine ursprüngliche Nachricht\n"
            f"--{boundary}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body><p>Hallo Max,</p><p>danke für deine Nachricht.</p>"
            "<blockquote type=\"cite\">Deine ursprüngliche Nachricht</blockquote>"
            "</body></html>\r\n"
            f"--{boundary}--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>SOGo-Signatur</p>"})
        body = next(
            item["value"] for item in result["modifications"]
            if item["type"] == "replaceContents"
        )
        plain_start = body.index("Content-Type: text/plain")
        html_start = body.index("Content-Type: text/html")
        plain_part = body[plain_start:html_start]
        html_part = body[html_start:]
        decoded_plain_part = decodestring(plain_part.encode("utf-8")).decode("utf-8", errors="replace")
        decoded_html_part = decodestring(html_part.encode("utf-8")).decode("utf-8", errors="replace")
        self.assertLess(decoded_plain_part.index("SOGo-Signatur"), decoded_plain_part.index("> Deine ursprüngliche Nachricht"))
        self.assertLess(decoded_html_part.index("SOGo-Signatur"), decoded_html_part.index("<blockquote type=\"cite\""))



    def test_skips_text_part_with_unknown_transfer_encoding(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["Content-Type", " text/plain; charset=utf-8\r\n"],
                ["Content-Transfer-Encoding", " x-unknown-encoding\r\n"],
            ],
            "Encoded payload that must not be reinterpreted",
        )
        self.assertEqual(
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"}),
            {"action": "accept"},
        )

    def test_skips_entire_multipart_alternative_with_unknown_transfer_encoding(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["MIME-Version", " 1.0\r\n"],
                ["Content-Type", ' multipart/alternative; boundary="alt"\r\n'],
            ],
            "--alt\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Transfer-Encoding: x-unknown-encoding\r\n\r\n"
            "Opaque payload\r\n"
            "--alt\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n"
            "<html><body>Hello</body></html>\r\n"
            "--alt--\r\n",
        )
        result = build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"})
        self.assertNotIn("replaceContents", [item["type"] for item in result.get("modifications", [])])

    def test_skips_text_part_with_unknown_charset(self):
        payload = request(
            [
                ["From", " sender@example.org\r\n"],
                ["Content-Type", " text/plain; charset=x-unknown-codec\r\n"],
            ],
            "Hello",
        )
        self.assertEqual(
            build_stalwart_hook_response(payload, {"html": "<p>Signature</p>"}),
            {"action": "accept"},
        )


if __name__ == "__main__":
    unittest.main()
