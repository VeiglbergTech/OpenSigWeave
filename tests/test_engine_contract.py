"""Tests for the transport-neutral shared engine request contract."""

import unittest

from dataclasses import FrozenInstanceError

from core.engine_contract import (
    SignatureEngineError,
    SignatureEngineResult,
    normalize_engine_request,
)


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
        for sender in ("invalid", "a@@example.org", "a b@example.org", "@example.org", "user@"):
            with self.subTest(sender=sender):
                with self.assertRaisesRegex(ValueError, "Invalid sender email"):
                    normalize_engine_request(sender)


    def test_engine_result_serializes_stable_transport_neutral_fields(self):
        result = SignatureEngineResult(
            html="<p>Hello</p>",
            inject_on_replies=True,
            trim_whitespace=False,
            strip_device_signatures=True,
        )
        self.assertEqual(result.to_payload(), {
            "html": "<p>Hello</p>",
            "inject_on_replies": True,
            "trim_whitespace": False,
            "strip_device_signatures": True,
        })
        with self.assertRaises(FrozenInstanceError):
            result.html = "changed"

    def test_engine_error_exposes_stable_code_and_safe_message(self):
        error = SignatureEngineError("ASSET_UNAVAILABLE", "Signature asset unavailable")
        self.assertEqual(error.code, "ASSET_UNAVAILABLE")
        self.assertEqual(error.safe_message, "Signature asset unavailable")
        self.assertEqual(str(error), "Signature asset unavailable")


if __name__ == "__main__":
    unittest.main()
