"""Deterministic policy synchronization tests without external network access."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.domain_failure_policy import build_snapshot
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "sync_domain_policy.py"
spec = importlib.util.spec_from_file_location("sync_domain_policy", SOURCE)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class FakeResponse:
    def __init__(self, data, status=200):
        self.payload = io.BytesIO(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.payload.read(size)


class SyncTests(unittest.TestCase):
    def example(self, now=1000, required=False):
        rows = [SimpleNamespace(domain_name="one.test", is_active=True, signature_required=required),
                SimpleNamespace(domain_name="two.test", is_active=True, signature_required=True)]
        return build_snapshot(rows, now=now)

    def test_validation_versions_and_strict_domains(self):
        base = self.example()
        self.assertFalse(sync.validate_snapshot(base, now=1000)["domains"]["one.test"])
        for override in [
            {"generated_at": 2000}, {"domains": {"one.test": "false"}},
            {"domains": {"*.one.test": False}}, {"format": "unknown"},
            {"domains": {"one.test": False, "ONE.test": True}},
            {"policy_version": "f" * 64}, {"policy_version": "not-a-hash"},
            {"unexpected": True},
        ]:
            changed = {**base, **override}
            with self.assertRaises(ValueError, msg=str(override)):
                sync.validate_snapshot(changed, now=1000)
        with self.assertRaises(ValueError):
            json.loads('{"a":1,"a":2}', object_pairs_hook=sync.strict_object)

    def test_long_outage_and_restart_persist_last_good(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "policy.json"
            snapshot = self.example()
            self.assertTrue(sync.atomic_install(snapshot, target, now=1000))
            before = target.read_bytes()
            self.assertFalse(sync.atomic_install(self.example(now=1001), target, now=1001),
                             "unchanged version should avoid unnecessary writes")
            self.assertEqual(target.read_bytes(), before)
            after_restart = sync.installed_snapshot(target, now=1000 + 30 * 86400)
            self.assertEqual(after_restart["policy_version"], snapshot["policy_version"])
            self.assertFalse(after_restart["domains"]["one.test"])

    def test_tamper_missing_local_and_last_good_survives_failed_update(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "policy.json"
            self.assertIsNone(sync.installed_snapshot(target))
            prior = self.example()
            sync.atomic_install(prior, target, now=1000)
            preserved = target.read_bytes()
            with self.assertRaises(ValueError):
                sync.atomic_install({**prior, "domains": {"one.test": False}},
                                    target, now=1000)
            self.assertEqual(target.read_bytes(), preserved)
            with self.assertRaises(ValueError):
                sync.atomic_install(prior, Path(root) / "missing" / "policy.json", now=1000)
            self.assertEqual(target.read_bytes(), preserved)
            target.write_text('{"tampered":true}')
            with self.assertRaises(ValueError):
                sync.installed_snapshot(target, now=1000)
            sync.atomic_install(prior, target, now=1000)
            self.assertEqual(sync.installed_snapshot(target, now=200000)["policy_version"],
                             prior["policy_version"])
            target.unlink()
            target.symlink_to(Path(root) / "other")
            with self.assertRaises(ValueError):
                sync.atomic_install(prior, target, now=1000)

    def test_legacy_policy_only_valid_until_expiry(self):
        old = {"format": sync.LEGACY_FORMAT, "generated_at": 1000,
               "expires_at": 2000, "domains": {"one.test": False}}
        self.assertEqual(sync.validate_snapshot(old, now=1500, allow_legacy=True), old)
        with self.assertRaises(ValueError):
            sync.validate_snapshot(old, now=2001, allow_legacy=True)
        with self.assertRaises(ValueError):
            sync.validate_snapshot(old, now=1500)  # No downloading old snapshots.

    def test_http_errors_timeouts_invalid_json_preserve_existing(self):
        healthy = self.example(now=1000)
        encoded = json.dumps(healthy).encode()
        with patch.object(sync.time, "time", return_value=1000):
            for code in (401, 403, 500, 503):
                with self.subTest(status=code):
                    opener = type("Opener", (), {"open": lambda *a, **k: FakeResponse(encoded, code)})()
                    with patch.object(sync, "build_opener", return_value=opener):
                        with self.assertRaises(ValueError):
                            sync.fetch_snapshot("https://osw.example.test/api/v1/mail/domain-policy", "k" * 40)
            for raw in (b'{invalid', b'{"a":1,"a":2}', b'[]', b'', b'{}'):
                opener = type("Opener", (), {"open": lambda *a, **k: FakeResponse(raw)})()
                with patch.object(sync, "build_opener", return_value=opener):
                    with self.assertRaises((ValueError, json.JSONDecodeError)):
                        sync.fetch_snapshot("https://osw.example.test/api/v1/mail/domain-policy", "k" * 40)
            def timeout(*_args, **_kwargs):
                raise TimeoutError("controlled API timeout")
            opener = type("Opener", (), {"open": timeout})()
            with patch.object(sync, "build_opener", return_value=opener):
                with self.assertRaises(TimeoutError):
                    sync.fetch_snapshot("https://osw.example.test/api/v1/mail/domain-policy", "k" * 40)
            opener = type("Opener", (), {"open": lambda *a, **k: FakeResponse(encoded)})()
            with patch.object(sync, "build_opener", return_value=opener):
                self.assertEqual(sync.fetch_snapshot(
                    "https://osw.example.test/api/v1/mail/domain-policy", "k" * 40), healthy)

    def test_freshness_only_for_network_not_long_term_local_storage(self):
        saved = self.example(now=1000)
        self.assertEqual(sync.validate_snapshot(saved, now=1000 + 25 * 86400), saved)
        with self.assertRaises(ValueError):
            sync.validate_snapshot(saved, now=1000 + 301, fresh=True)

    def test_expected_version_acknowledgment_and_pending_exit(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "policy.json"
            key = Path(root) / "key"
            key.write_text("k" * 40)
            key.chmod(0o600)
            snapshot = self.example(now=1000)
            opts = ["--url", "https://osw.example.test/api/v1/mail/domain-policy",
                    "--key-file", str(key), "--output", str(out), "--dry-run"]
            with patch.object(sync, "fetch_snapshot", return_value=snapshot):
                self.assertEqual(sync.main(opts + ["--expect-version", snapshot["policy_version"]]), 0)
                self.assertEqual(sync.main(opts + ["--expect-version", "f" * 64]), 3)
            sync.atomic_install(snapshot, out, now=1000)
            with patch.object(sync.time, "time", return_value=1000):
                self.assertEqual(sync.main(["--output", str(out), "--status",
                                           "--expect-version", snapshot["policy_version"]]), 0)
                self.assertEqual(sync.main(["--output", str(out), "--status",
                                           "--expect-version", "f" * 64]), 3)


if __name__ == "__main__":
    unittest.main()
