"""Tests for rule selection, retained backup verification and lazy key handling."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import backup_preupdate as pre
import backup_recovery as recovery


def rule(name, ident, location_id, *, enabled=True, content="full"):
    return type("Rule", (), {"name": name, "id": ident, "location_id": location_id,
                              "is_enabled": enabled, "content": content})()


def location(ident, *, enabled=True, kind="local", password=None):
    return type("Location", (), {"id": ident, "name": "Local", "type": kind,
                                 "is_enabled": enabled, "password_ciphertext": password})()


class FakeDb:
    def __init__(self, rules, locations):
        self.rules = rules
        self.locations = {entry.id: entry for entry in locations}

    def query(self, cls):
        query = Mock()
        query.filter.return_value.all.return_value = [
            entry for entry in self.rules if entry.is_enabled
        ]
        return query

    def get(self, cls, ident):
        return self.locations.get(ident)


class RuleSelectionTests(unittest.TestCase):
    def test_empty_rules_are_not_configured(self):
        self.assertIsNone(pre.select_rule(FakeDb([], [])))

    def test_disabled_rules_are_not_configured(self):
        db = FakeDb([rule("off", "a", "l", enabled=False)], [location("l")])
        self.assertIsNone(pre.select_rule(db))

    def test_selection_is_stable_and_preferred_rule_wins(self):
        db = FakeDb([rule("Zulu", "z", "l"), rule("Alpha", "a", "l")], [location("l")])
        self.assertEqual(pre.select_rule(db)[0].id, "a")
        self.assertEqual(pre.select_rule(db, "z")[0].id, "z")
        with self.assertRaises(pre.PreupdateBackupError):
            pre.select_rule(db, "missing")

    def test_automatic_selection_skips_unusable_rules(self):
        db = FakeDb([
            rule("Alpha broken", "a", "missing"),
            rule("Bravo usable", "b", "local"),
        ], [location("local")])
        selected, destination = pre.select_rule(db)
        self.assertEqual(selected.id, "b")
        self.assertEqual(destination.id, "local")

    def test_preferred_invalid_rule_fails_even_when_alternative_exists(self):
        db = FakeDb([
            rule("Alpha broken", "a", "missing"),
            rule("Bravo usable", "b", "local"),
        ], [location("local")])
        with self.assertRaises(pre.PreupdateBackupError):
            pre.select_rule(db, "a")

    def test_only_unusable_enabled_rules_fail_closed(self):
        db = FakeDb([
            rule("Alpha broken", "a", "missing"),
            rule("Bravo incomplete", "b", "local", content="selective"),
        ], [location("local")])
        with self.assertRaises(pre.PreupdateBackupError):
            pre.select_rule(db)

    def test_broken_rule_is_failure_not_missing_backup(self):
        for loc in (None, location("l", enabled=False),
                    location("l", kind="smb", password=None)):
            db = FakeDb([rule("Active", "a", "l")], [] if loc is None else [loc])
            with self.assertRaises(pre.PreupdateBackupError):
                pre.select_rule(db)

    def test_incomplete_contents_are_rejected(self):
        db = FakeDb([rule("partial", "a", "l", content="selective")], [location("l")])
        with self.assertRaises(pre.PreupdateBackupError):
            pre.select_rule(db)


class AgeKeyTests(unittest.TestCase):
    def test_lazy_key_generated_once_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_private = "# created: today\n# public key: age1test\nAGE-SECRET-KEY-TESTONLY\n"
            calls = []
            def fake_age(*args):
                calls.append(args)
                return "age1test\n" if args else fake_private
            with patch.object(recovery, "_age", side_effect=fake_age):
                key1 = recovery.automatic_identity(root)
                key2 = recovery.automatic_identity(root)
                self.assertEqual(key1, key2)
                self.assertIn("AGE-SECRET-KEY-", key1.read_text())
                self.assertEqual(stat.S_IMODE(key1.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(key1.parent.stat().st_mode), 0o700)
                self.assertEqual(len(calls), 1)

    def test_no_key_generated_with_existing_explicit_age_config(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"BACKUP_AGE_RECIPIENT": "age1existing",
                                         "BACKUP_AGE_IDENTITY": "AGE-SECRET-KEY-EXISTING",
                                         "BACKUP_AGE_IDENTITY_FILE": ""}):
                with patch.object(recovery, "automatic_identity") as generated:
                    self.assertEqual(recovery.recipient_for_backup(Path(temp)), "age1existing")
                    self.assertIsNone(recovery.automatic_identity_if_configured(Path(temp)))
                    generated.assert_not_called()

    def test_partial_legacy_age_settings_do_not_rotate_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {"BACKUP_AGE_RECIPIENT": "age1test",
                                         "BACKUP_AGE_IDENTITY": "",
                                         "BACKUP_AGE_IDENTITY_FILE": ""}):
                with self.assertRaises(recovery.RecoveryKeyError):
                    recovery.recipient_for_backup(Path(temp))
                self.assertFalse((Path(temp) / ".recovery").exists())


if __name__ == "__main__":
    unittest.main()
