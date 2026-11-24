"""Secure, read-only support alert status snapshots and incident recovery."""
import json
import tempfile
from pathlib import Path
import unittest

from core.support_alert_status import read_support_alert_status
from scripts.opensigweave_support_notifier import (
    connect_database, publish_status_snapshot, record_failure, record_success,
)


class AlertStatusTests(unittest.TestCase):
    def test_export_shows_aggregates_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status").mkdir()
            db = connect_database(str(root / "alerts.sqlite3"))
            try:
                target = str(root / "status" / "status.json")
                self.assertFalse(publish_status_snapshot(db, None))
                record_failure(db, "1234567890abcdef", "ENGINE_HTTP_FAILURE", 1000)
                self.assertTrue(publish_status_snapshot(db, target, now=1010))
                current = read_support_alert_status(target, now=1020)
                self.assertEqual(current["status"], "active")
                self.assertEqual(current["active_count"], 1)
                self.assertEqual(current["pending_count"], 1)
                snapshot = Path(target).read_text(encoding="utf-8")
                self.assertNotIn("password", snapshot.lower())
                self.assertNotIn("@", snapshot)
                self.assertEqual(json.loads(snapshot)["incidents"][0]["reason"], "ENGINE_HTTP_FAILURE")
                self.assertEqual(read_support_alert_status(target, now=1200)["status"], "stale")
                record_success(db, 1030)
                self.assertTrue(publish_status_snapshot(db, target, now=1040))
                self.assertEqual(read_support_alert_status(target, now=1050)["status"], "ok")
            finally:
                db.close()

    def test_invalid_status_and_symlink_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "status.json"
            target.write_text('{"generated_at":1,"active_count":-3,"pending_count":0,"incidents":[]}')
            self.assertEqual(read_support_alert_status(str(target), now=1)["status"], "unavailable")
            target.write_text("broken json")
            self.assertEqual(read_support_alert_status(str(target), now=1)["status"], "unavailable")
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            self.assertEqual(read_support_alert_status(str(link), now=1)["status"], "unavailable")
            self.assertEqual(read_support_alert_status(None)["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
