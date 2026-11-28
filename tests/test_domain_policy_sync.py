"""Validate strict, atomic policy synchronization without network access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "sync_domain_policy.py"
spec = importlib.util.spec_from_file_location("sync_domain_policy", SOURCE)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def example(self):
        return {"format": sync.FORMAT, "generated_at": 1000, "expires_at": 2000,
                "domains": {"one.test": False, "two.test": True}}

    def test_strict_validity_and_exact_domain_names(self):
        self.assertEqual(sync.validate_snapshot(self.example(), now=1500)["domains"]["one.test"], False)
        for override in [
            {"expires_at": 1500}, {"generated_at": 4000}, {"domains": {"foo.test": "false"}},
            {"domains": {"*.one.test": False}}, {"format": "unknown"},
            {"domains": {"one.test": False, "ONE.test": True}},
        ]:
            changed = {**self.example(), **override}
            with self.assertRaises(ValueError):
                sync.validate_snapshot(changed, now=1500)
        with self.assertRaises(ValueError):
            json.loads('{"a":1,"a":2}', object_pairs_hook=sync.strict_object)

    def test_atomic_install_and_preserve_last_known_good(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "policy.json"
            target.write_text("last-good", encoding="utf-8")
            sync.atomic_install(self.example(), target)
            self.assertEqual(json.loads(target.read_text())["domains"]["one.test"], False)
            saved = target.read_bytes()
            with self.assertRaises(ValueError):
                sync.atomic_install(self.example(), Path(root) / "missing" / "policy.json")
            self.assertEqual(target.read_bytes(), saved)
            target.unlink()
            target.symlink_to(Path(root) / "other")
            with self.assertRaises(ValueError):
                sync.atomic_install(self.example(), target)


if __name__ == "__main__":
    unittest.main()
