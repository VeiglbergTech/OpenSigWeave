"""Support alert queue: durable dedup, retry, cooldown and recovery without mail contents."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import smtplib

from scripts.opensigweave_support_notifier import (
    connect_database, flush_outbox, process_log_line, record_failure, record_success,
    check_rspamd_health,
)


class AlertNotifierTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.dbpath = str(Path(self.root.name) / "state" / "alerts.sqlite3")
        self.db = connect_database(self.dbpath)

    def tearDown(self):
        self.db.close()
        self.root.cleanup()

    def test_deduplication_and_persistent_retry_without_contents(self):
        record_failure(self.db, "abcdef0123456789", "ENGINE_HTTP_FAILURE", 1700000000)
        self.assertFalse(record_failure(self.db, "abcdef0123456789", "ENGINE_HTTP_FAILURE", 1700000001))
        record_failure(self.db, "12345678abcdef00", "ENGINE_HTTP_FAILURE", 1700000002)
        with patch.dict(os.environ, {"OSW_ALERT_SUPPORT_TO": "support@example.org", "OSW_ALERT_FROM": "alerts@example.org"}):
            def unavailable(_):
                raise smtplib.SMTPServerDisconnected("secret-should-not-leak")
            self.assertEqual(flush_outbox(self.db, unavailable, now=1700000003), 0)
            messages = []
            self.assertEqual(flush_outbox(self.db, messages.append, now=1700000004), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox WHERE delivered_at IS NOT NULL").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT failures FROM incidents WHERE reason='ENGINE_HTTP_FAILURE'").fetchone()[0], 2)
        self.assertEqual(len(messages), 1)
        self.assertIn("Occurrence count: 2", messages[0].get_content())
        self.assertNotIn("secret-should-not-leak", messages[0].get_content())
        self.db.close()
        self.db = connect_database(self.dbpath)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0], 2)

    def test_cooldown_and_observed_recovery(self):
        record_failure(self.db, "0000000012345678", "MIME_REWRITE_FAILED", 100000000)
        with patch.dict(os.environ, {"OSW_ALERT_SUPPORT_TO": "support@example.org", "OSW_ALERT_FROM": "alerts@example.org"}):
            self.assertEqual(flush_outbox(self.db, lambda msg: None, now=100000001), 1)
        record_failure(self.db, "0000000012345679", "MIME_REWRITE_FAILED", 100000002)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)
        record_failure(self.db, "000000001234567a", "MIME_REWRITE_FAILED", 100000901)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 2)
        record_success(self.db, 100000902)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 3)
        self.assertEqual(self.db.execute("SELECT active FROM incidents WHERE reason='MIME_REWRITE_FAILED'").fetchone()[0], 0)

    def test_log_parser_ignores_untrusted_contents_and_non_event(self):
        self.assertFalse(process_log_line(self.db, "From: secret@example.org Subject: secrets", now=10))
        self.assertTrue(process_log_line(
            self.db, "2026-11-21 OpenSigWeave failure_id=abcdef0123456789 reason=ENGINE_HTTP_FAILURE policy=tempfail",
            now=100000000
        ))
        self.assertFalse(process_log_line(
            self.db, "2026-11-21 OpenSigWeave failure_id=abcdef0123456789 reason=ENGINE_HTTP_FAILURE policy=tempfail",
            now=100000001
        ))
        process_log_line(self.db, "OpenSigWeave processing_success adapter=rspamd", now=100000002)
        self.assertFalse(self.db.execute("SELECT active FROM incidents").fetchone()[0])

    def test_docker_container_outage_produces_rate_limited_metadata(self):
        with patch("scripts.opensigweave_support_notifier.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            check_rspamd_health(self.db, "rspamd-mailcow", now=100000000)
            check_rspamd_health(self.db, "rspamd-mailcow", now=100000001)
        self.assertEqual(self.db.execute("SELECT failures FROM incidents").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
