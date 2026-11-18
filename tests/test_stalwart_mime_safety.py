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


if __name__ == "__main__":
    unittest.main()
