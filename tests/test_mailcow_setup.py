"""Offline, non-SMTP tests for managed Mailcow installation safety."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import mailcow_setup as setup


class MailcowSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "mailcow"
        self.conf = self.root / "data/conf/rspamd"
        (self.conf / "plugins.d").mkdir(parents=True)
        (self.conf / "lua").mkdir()
        (self.conf / "local.d").mkdir()
        self.config_path = self.conf / "rspamd.conf.local"
        self.config_path.write_text('foo { enabled = true; }\n', encoding="utf-8")
        self.source = base / "source"
        for name in (*setup.FILES, "scripts/sync_domain_policy.py",
                     "scripts/mailcow_setup.py"):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("-- pinned asset: " + name)
        self.patches = [
            patch.object(setup, "RUNTIME", base / "runtime"),
            patch.object(setup, "SECRET", base / "etc/engine.key"),
            patch.object(setup, "STATE", base / "etc/install-state.json"),
            patch.object(setup, "_key", return_value="k" * 40),
            patch.object(setup, "sync_policy"),
            patch.object(setup, "run"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_prepare_preserves_unrelated_configuration_and_is_repeatable(self):
        setup.prepare(self.root, self.source, "a" * 40,
                      "https://sig.example.test/api/v1/engine/signatures/")
        saved = self.config_path.read_text()
        self.assertIn("foo { enabled = true; }", saved)
        self.assertIn(setup.BEGIN, saved)
        self.assertIn('failure_policy = "enabled"', saved)
        self.assertIn("domain_policy_file", saved)
        self.assertIn("/etc/rspamd/local.d/opensigweave-policy/policy.json", saved)
        self.assertEqual(setup.POLICY_REL, "local.d/opensigweave-policy/policy.json")
        self.assertEqual(self.config_path.stat().st_mode & 0o777, 0o600)
        state = setup.load_state()
        self.assertEqual(len(state["files"]), 4)
        self.assertEqual(len(state["sync_hash"]), 64)
        setup.prepare(self.root, self.source, "a" * 40,
                      "https://sig.example.test/api/v1/engine/signatures/")
        self.assertEqual(saved, self.config_path.read_text())
        for rel in setup.FILES.values():
            self.assertTrue((self.conf / rel).exists())

    def test_refuse_unmanaged_or_edited_settings(self):
        self.config_path.write_text('opensigweave { engine_api_key = "private"; }\n')
        with self.assertRaisesRegex(ValueError, "Unmanaged"):
            setup.prepare(self.root, self.source, "a" * 40,
                          "https://sig.example.test/api/v1/engine/signatures/")
        self.config_path.write_text("unrelated { yes = true; }\n")
        setup.prepare(self.root, self.source, "a" * 40,
                      "https://sig.example.test/api/v1/engine/signatures/")
        self.config_path.write_text(self.config_path.read_text() + "# user change\n")
        setup.prepare(self.root, self.source, "a" * 40,
                      "https://sig.example.test/api/v1/engine/signatures/")
        self.config_path.write_text(self.config_path.read_text().replace(
            'failure_policy = "enabled"', 'failure_policy = "disabled"'))
        with self.assertRaisesRegex(ValueError, "changed"):
            setup.prepare(self.root, self.source, "a" * 40,
                          "https://sig.example.test/api/v1/engine/signatures/")

    def test_no_overwrite_of_unmanaged_module(self):
        (self.conf / "lua/opensigweave_cid.lua").write_text("-- administrator customization")
        with self.assertRaisesRegex(ValueError, "Unmanaged"):
            setup.prepare(self.root, self.source, "a" * 40,
                          "https://sig.example.test/api/v1/engine/signatures/")

    def test_reject_unsafe_endpoint_and_revisions(self):
        for url in ("http://sig.example.test/api/v1/engine/signatures/",
                    "https://sig.example.test/other",
                    'https://host.example/" evil = true;'):
            with self.assertRaises(ValueError):
                setup.engine_url(url)
        with self.assertRaisesRegex(ValueError, "pinned"):
            setup.prepare(self.root, self.source, "main",
                          "https://sig.example.test/api/v1/engine/signatures/")

    def test_configtest_failure_restores_old_files_and_state(self):
        original = self.config_path.read_bytes()
        with patch.object(setup, "run", side_effect=RuntimeError("configtest failed")):
            with self.assertRaisesRegex(RuntimeError, "configtest"):
                setup.prepare(self.root, self.source, "a" * 40,
                              "https://sig.example.test/api/v1/engine/signatures/")
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertIsNone(setup.load_state())
        for rel in setup.FILES.values():
            self.assertFalse((self.conf / rel).exists())


if __name__ == "__main__":
    unittest.main()
