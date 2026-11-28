"""Domain failure policy export and model regression."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")

from core.domain_failure_policy import build_snapshot, FORMAT, VALID_SECONDS
from main import DomainCreate, DomainUpdate
from models import DomainDB


class DomainFailurePolicyTests(unittest.TestCase):
    def test_new_domains_are_optional(self):
        domain = DomainCreate(domain_name="example.test")
        self.assertIs(domain.signature_required, False)
        self.assertIs(DomainDB(domain_name="example.test").signature_required, None)  # ORM default applies on INSERT

    def test_old_update_clients_can_omit_new_field(self):
        update = DomainUpdate(
            is_active=True, allow_overrides=True,
            inject_on_replies=False, trim_whitespace=True,
            strip_device_signatures=False,
        )
        self.assertNotIn("signature_required", update.model_fields_set)

    def test_exact_domain_boundaries_and_expiration(self):
        rows = [
            SimpleNamespace(domain_name="EXAMPLE.test", is_active=True, signature_required=False),
            SimpleNamespace(domain_name="sub.example.test", is_active=True, signature_required=True),
            SimpleNamespace(domain_name="inactive.test", is_active=False, signature_required=False),
        ]
        snapshot = build_snapshot(rows, now=1000)
        self.assertEqual(snapshot["format"], FORMAT)
        self.assertEqual(snapshot["expires_at"], 1000 + VALID_SECONDS)
        self.assertEqual(snapshot["domains"], {"example.test": False, "sub.example.test": True})

    def test_bad_domains_and_duplicates_fail(self):
        with self.assertRaises(ValueError):
            build_snapshot([SimpleNamespace(domain_name="-bad.test", is_active=True, signature_required=False)], now=1)
        with self.assertRaises(ValueError):
            build_snapshot([
                SimpleNamespace(domain_name="EXAMPLE.test", is_active=True, signature_required=True),
                SimpleNamespace(domain_name="example.test", is_active=True, signature_required=False),
            ], now=1)


if __name__ == "__main__":
    unittest.main()
