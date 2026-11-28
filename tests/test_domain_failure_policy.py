"""Versioned domain-policy export, strict validation and defaults."""
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")

from core.domain_failure_policy import build_snapshot, FORMAT, policy_version
from main import DomainCreate, DomainUpdate
from models import DomainDB


def row(name, required=False, active=True):
    return SimpleNamespace(domain_name=name, is_active=active, signature_required=required)


class DomainFailurePolicyTests(unittest.TestCase):
    def test_new_domains_are_optional(self):
        domain = DomainCreate(domain_name="example.test")
        self.assertIs(domain.signature_required, False)
        self.assertIsNone(DomainDB(domain_name="example.test").signature_required)  # ORM default applies on INSERT

    def test_old_update_clients_can_omit_new_field(self):
        update = DomainUpdate(
            is_active=True, allow_overrides=True,
            inject_on_replies=False, trim_whitespace=True,
            strip_device_signatures=False,
        )
        self.assertNotIn("signature_required", update.model_fields_set)

    def test_exact_domain_boundaries_and_stable_version(self):
        rows = [row("EXAMPLE.test"), row("sub.example.test", True), row("inactive.test", False, False)]
        snapshot = build_snapshot(rows, now=1000)
        self.assertEqual(snapshot["format"], FORMAT)
        self.assertNotIn("expires_at", snapshot)
        self.assertEqual(snapshot["generated_at"], 1000)
        self.assertEqual(snapshot["domains"], {"example.test": False, "sub.example.test": True})
        self.assertEqual(snapshot["policy_version"], policy_version(snapshot["domains"]))
        self.assertEqual(snapshot["policy_version"], build_snapshot(rows, now=1000000)["policy_version"])

    def test_switching_requirement_changes_acknowledgment_version(self):
        optional = build_snapshot([row("example.test", False)], now=1000)
        required = build_snapshot([row("example.test", True)], now=1001)
        optional_again = build_snapshot([row("example.test", False)], now=1002)
        self.assertNotEqual(optional["policy_version"], required["policy_version"])
        self.assertEqual(optional["policy_version"], optional_again["policy_version"])

    def test_invalid_domains_and_duplicates(self):
        with self.assertRaises(ValueError):
            build_snapshot([row("-bad.test")], now=1)
        with self.assertRaises(ValueError):
            build_snapshot([row("EXAMPLE.test", True), row("example.test")], now=1)


if __name__ == "__main__":
    unittest.main()
