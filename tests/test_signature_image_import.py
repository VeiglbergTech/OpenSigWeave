"""Tests for one-shot remote image import without real network access."""
import io
import socket
import unittest
from unittest.mock import patch

from signature_image_import import SignatureImageImportError, _validated_url, import_image_from_url


class _FakeSocket:
    def __init__(self, response):
        self.response = io.BytesIO(response)
        self.sent = b""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return None
    def sendall(self, data):
        self.sent += data
    def makefile(self, *args, **kwargs):
        return self.response
    def settimeout(self, timeout):
        assert timeout > 0


class _FakeTLSContext:
    def __init__(self, sock):
        self.sock = sock
        self.server_hostname = None
    def wrap_socket(self, raw, server_hostname):
        self.server_hostname = server_hostname
        return self.sock


class _Store:
    def write_image(self, content, content_type):
        assert content == b"\\x89PNG\\r\\n\\x1a\\n" + b"hello"
        assert content_type == "image/png"
        return "asset.png"


def _getaddrinfo(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.215.14", port))]


class ImageImportTests(unittest.TestCase):
    def test_url_validation(self):
        for url in (
            "http://example.org/logo.png",
            "https://user:pass@example.org/logo.png",
            "https://example.org:8443/logo.png",
            "https://example.org/logo.png#secret",
            "https://example.org/\\foo",
            "https://example.org/logo.png\\r\\nHost: internal",
        ):
            with self.subTest(url=url), self.assertRaises(SignatureImageImportError):
                _validated_url(url)

    def test_private_dns_and_mixed_dns_are_rejected_without_connecting(self):
        with patch("signature_image_import.socket.create_connection") as connect:
            for ip in ("127.0.0.1", "10.35.10.16", "169.254.169.254", "::1", "fc00::1"):
                with self.subTest(ip=ip), patch("signature_image_import.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, 1, 6, "", (ip, 443))]):
                    with self.assertRaises(SignatureImageImportError):
                        import_image_from_url(_Store(), "https://example.org/logo.png")
            connect.assert_not_called()

    def test_download_pins_dns_ip_uses_sni_and_stores_local_image(self):
        png = b"\\x89PNG\\r\\n\\x1a\\nhello"
        response = (b"HTTP/1.1 200 OK\\r\\nContent-Type: image/png\\r\\n"
                    + f"Content-Length: {len(png)}\\r\\n".encode() + b"\\r\\n" + png)
        fake = _FakeSocket(response)
        context = _FakeTLSContext(fake)
        with patch("signature_image_import.socket.getaddrinfo", side_effect=_getaddrinfo), \\
             patch("signature_image_import.socket.create_connection", return_value=fake) as connect, \\
             patch("signature_image_import.ssl.create_default_context", return_value=context):
            result = import_image_from_url(_Store(), "https://example.org/logo.png")
        self.assertEqual(result, "asset.png")
        connect.assert_called_once_with(("93.184.215.14", 443), timeout=5.0)
        self.assertEqual(context.server_hostname, "example.org")
        self.assertIn(b"Host: example.org\\r\\n", fake.sent)

    def test_redirect_does_not_follow(self):
        fake = _FakeSocket(b"HTTP/1.1 302 Found\\r\\nLocation: http://127.0.0.1/\\r\\nContent-Length: 0\\r\\n\\r\\n")
        with patch("signature_image_import.socket.getaddrinfo", side_effect=_getaddrinfo), \\
             patch("signature_image_import.socket.create_connection", return_value=fake), \\
             patch("signature_image_import.ssl.create_default_context", return_value=_FakeTLSContext(fake)):
            with self.assertRaises(SignatureImageImportError):
                import_image_from_url(_Store(), "https://example.org/logo.png")


if __name__ == "__main__":
    unittest.main()
