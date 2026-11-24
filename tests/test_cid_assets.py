"""CID asset renderer regression tests."""
import tempfile
import unittest
from pathlib import Path

from core.cid_assets import build_cid_payload
from core.engine_contract import SignatureEngineError
from signature_storage import SignatureFileStore


class CIDAssetsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SignatureFileStore(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_inline_image_is_local_and_duplicate_asset_is_reused(self):
        png = b"\x89PNG\r\n\x1a\n" + b"test"
        filename = self.store.write_image(png, "image/png")
        source = f"https://signature.example.org/signature-assets/{filename}"
        html = f'<p>Person <img src="{source}"><img src="{source}"></p>'
        rendered, assets = build_cid_payload(html, self.store)
        self.assertEqual(len(assets), 1)
        self.assertEqual(rendered.count("cid:"), 2)
        self.assertNotIn("https://", rendered)
        self.assertEqual(assets[0]["content_type"], "image/png")
        self.assertTrue(assets[0]["content_id"].startswith("osw-"))
        import base64
        self.assertEqual(base64.b64decode(assets[0]["base64"]), png)

    def test_tampered_managed_asset_fails_closed(self):
        original = b"\\x89PNG\\r\\n\\x1a\\n" + b"original"
        filename = self.store.write_image(original, "image/png")
        # Preserve a valid PNG magic prefix: only the stored digest proves that
        # these are not the immutable bytes originally imported by the editor.
        self.store.image_path(filename).write_bytes(
            b"\\x89PNG\\r\\n\\x1a\\n" + b"tampered"
        )
        source = f"https://signature.example.org/signature-assets/{filename}"
        with self.assertRaises(SignatureEngineError) as raised:
            build_cid_payload(f'<img src="{source}">', self.store)
        self.assertEqual(raised.exception.code, "INLINE_IMAGE_ASSET_UNAVAILABLE")

    def test_remote_images_never_get_fetched(self):
        for source in ("https://untrusted.org/logo.png",
                       "https://untrusted.org/signature-assets/invalid.png",
                       "data:image/png;base64,AAAA",
                       "https://untrusted.org/signature-assets/" + "a" * 64 + ".png?secret=1"):
            with self.subTest(source=source), self.assertRaises(SignatureEngineError):
                build_cid_payload(f'<img src="{source}">', self.store)

    def test_missing_asset_fails_instead_of_linking_external_site(self):
        html = 'hello <img src="https://example.com/signature-assets/' + "a" * 64 + '.png">'
        with self.assertRaises(SignatureEngineError) as raised:
            build_cid_payload(html, self.store)
        self.assertEqual(raised.exception.code, "INLINE_IMAGE_ASSET_UNAVAILABLE")

    def test_plain_signatures_stay_unchanged(self):
        html = '<p>Hello</p>'
        self.assertEqual(build_cid_payload(html, self.store), (html, []))

    def test_maximum_unique_asset_count_is_enforced(self):
        sources = []
        for i in range(7):
            image = b"\x89PNG\r\n\x1a\n" + bytes([i])
            filename = self.store.write_image(image, "image/png")
            sources.append(f'<img src="https://assets.example.com/signature-assets/{filename}">')
        with self.assertRaises(SignatureEngineError) as raised:
            build_cid_payload("".join(sources), self.store)
        self.assertEqual(raised.exception.code, "INLINE_IMAGE_LIMIT")


if __name__ == "__main__":
    unittest.main()
