"""Tests for the transport-neutral shared engine request contract."""

import unittest

from core.engine_contract import normalize_engine_request


class EngineRequestContractTests(unittest.TestCase):
    def test_normalizes_sender_and_explicit_mode(self):
        request = normalize_engine_request(
            "  User@Example.ORG  ", "professional", "Sales", "a1b2"
        )
        self.assertEqual(request.sender_email, "user@example.org")
        self.assertEqual(request.mode, "PROFESSIONAL")
        self.assertTrue(request.explicit_mode)
        self.assertTrue(request.marker_requested)
        self.assertEqual(request.signature_name, "Sales")
        self.assertEqual(request.signature_code, "a1b2")

    def test_invalid_mode_falls_back_to_automatic_selection(self):
        request = normalize_engine_request("user@example.org", "not-a-mode")
        self.assertIsNone(request.mode)
        self.assertFalse(request.explicit_mode)
        self.assertFalse(request.marker_requested)

    def test_marker_without_explicit_mode_is_preserved_for_checksum_validation(self):
        request = normalize_engine_request(
            "user@example.org", None, "Sales", "a1b2"
        )
        self.assertTrue(request.marker_requested)
        self.assertFalse(request.explicit_mode)
        self.assertIsNone(request.mode)

    def test_rejects_invalid_sender_addresses(self):
        for sender in ("invalid", "a@@example.org", "a b@example.org", "@example.org"):
            with self.subTest(sender=sender):
                with self.assertRaisesRegex(ValueError, "Invalid sender email"):
                    normalize_engine_request(sender)


if __name__ == "__main__":
    unittest.main()
