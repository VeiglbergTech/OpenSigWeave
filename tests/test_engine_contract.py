import unittest

from core.engine_contract import normalize_engine_request


class EngineContractTests(unittest.TestCase):
    def test_normalizes_sender(self):
        request = normalize_engine_request("  User@Example.ORG  ")
        self.assertEqual(request.sender_email, "user@example.org")
        self.assertFalse(request.marker_requested)

    def test_invalid_sender_is_rejected(self):
        for value in ("invalid", "@example.org", "user@", "user @example.org"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_engine_request(value)

    def test_signature_marker_is_preserved_for_checksum_validation(self):
        request = normalize_engine_request("user@example.org", "Sales", "a1b2")
        self.assertEqual(request.signature_name, "Sales")
        self.assertEqual(request.signature_code, "a1b2")
        self.assertTrue(request.marker_requested)


if __name__ == "__main__":
    unittest.main()
