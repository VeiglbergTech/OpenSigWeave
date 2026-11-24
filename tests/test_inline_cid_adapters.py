"""Cross-adapter tests for managed CID images and unsafe MIME rejection."""
import base64
import hashlib
import unittest
from email import policy
from email.parser import BytesParser

from core.inline_mime import InlineImageError, attach_inline_images
from adapters.postfix_milter import rewrite_message
from adapters.stalwart import build_stalwart_hook_response


def asset():
    data = b"\x89PNG\r\n\x1a\n" + b"sample"
    digest = hashlib.sha256(data).hexdigest()
    cid = f"osw-{digest}@assets.opensigweave.invalid"
    return {"filename": digest + ".png", "content_id": cid,
            "content_type": "image/png", "base64": base64.b64encode(data).decode()}, data


class CIDTransportTests(unittest.TestCase):
    def test_postfix_preserves_existing_attachment_and_embeds_image(self):
        item, raw = asset()
        headers = [("MIME-Version", "1.0"), ("Content-Type", 'multipart/mixed; boundary="mix"')]
        body = (b"--mix\r\nContent-Type: text/html; charset=utf-8\r\n\r\nHello\r\n"
                b"--mix\r\nContent-Type: application/octet-stream\r\n"
                b'Content-Disposition: attachment; filename="report.bin"\r\n'
                b"Content-Transfer-Encoding: base64\r\n\r\nAAECAw==\r\n--mix--\r\n")
        out = rewrite_message(headers, body, f'<p><img src="cid:{item["content_id"]}"></p>',
                              inject_on_replies=True, inline_assets=[item])
        self.assertTrue(out.changed)
        changed_headers = {h.name: h.value for h in out.header_changes if h.value is not None}
        headers = {**dict(headers), **changed_headers}
        wire = b"\r\n".join(k.encode() + b": " + v.encode() for k,v in headers.items()) + b"\r\n\r\n" + out.body
        parsed = BytesParser(policy=policy.default).parsebytes(wire)
        self.assertFalse(parsed.defects)
        self.assertEqual(parsed.get_content_type(), "multipart/related")
        img = next(p for p in parsed.walk() if p.get("Content-ID") == f'<{item["content_id"]}>')
        self.assertEqual(img.get_payload(decode=True), raw)
        self.assertEqual(img.get_content_disposition(), "inline")
        report = next(p for p in parsed.walk() if p.get_filename() == "report.bin")
        self.assertEqual(report.get_payload(decode=True), bytes(range(4)))

    def test_plain_only_never_gets_orphan_image(self):
        item, _ = asset()
        out = rewrite_message([("Content-Type", "text/plain; charset=utf-8")], b"Hello",
                              f'<img src="cid:{item["content_id"]}">',
                              inject_on_replies=True, inline_assets=[item])
        self.assertTrue(out.changed)
        self.assertFalse(any(
            change.name.casefold() == "content-type" and change.value and
            "multipart/related" in change.value
            for change in out.header_changes
        ))

    def test_stalwart_keeps_existing_inline_content_ids(self):
        item, raw = asset()
        payload = {
            "context": {"stage": "data"},
            "envelope": {"from": {"address": "sender@example.org"}},
            "message": {
                "headers": [["From"," sender@example.org\r\n"],
                    ["MIME-Version"," 1.0\r\n"], ["Content-Type"," multipart/related; boundary=\"a\"\r\n"]],
                "contents": ("--a\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
                    "Existing cid:old@example.org\r\n"
                    "--a\r\nContent-Type: image/png\r\nContent-ID: <old@example.org>\r\n"
                    "Content-Transfer-Encoding: base64\r\n\r\naGVsbG8=\r\n--a--\r\n")
            }
        }
        out = build_stalwart_hook_response(payload, {
            "html": f'<img src="cid:{item["content_id"]}">',
            "inject_on_replies": True, "inline_assets": [item]})
        changes = out["modifications"]
        replacement = next(c["value"] for c in changes if c["type"] == "replaceContents")
        ct = next(c["value"] for c in changes if c["type"] == "addHeader" and c["name"] == "Content-Type")
        parsed = BytesParser(policy=policy.default).parsebytes(
            ("MIME-Version: 1.0\r\nContent-Type: " + ct + "\r\n\r\n" + replacement).encode())
        self.assertFalse(parsed.defects)
        cids = [p.get("Content-ID") for p in parsed.walk() if p.get("Content-ID")]
        self.assertIn("<old@example.org>", cids)
        self.assertEqual(len(cids), len(set(cids)))
        image = next(p for p in parsed.walk() if p.get("Content-ID") == f'<{item["content_id"]}>')
        self.assertEqual(image.get_payload(decode=True), raw)

    def test_rejects_tampering_and_collisions_before_mutation(self):
        item, data = asset()
        raw = BytesParser(policy=policy.default).parsebytes(
            (f'Content-Type: text/html\r\n\r\n<img src="cid:{item["content_id"]}">').encode())
        for tampered in (
            {**item, "base64": base64.b64encode(data + b"tamper").decode()},
            {**item, "content_type": "image/gif"},
            {**item, "base64": base64.b64encode(data * 250000).decode()},
        ):
            with self.assertRaises(InlineImageError):
                attach_inline_images(raw, [tampered])
            self.assertEqual(raw.get_content_type(), "text/html")
        with self.assertRaises(InlineImageError):
            attach_inline_images(raw, [item, item])
        self.assertEqual(raw.get_content_type(), "text/html")


if __name__ == "__main__":
    unittest.main()
