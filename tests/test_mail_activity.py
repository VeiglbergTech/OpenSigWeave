"""Controlled-clock authenticated mail activity and status-boundary tests."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import SystemSetupDB
from core.mail_activity import (
    connection_label, record_verified_adapter_activity, reset_activity_cache
)


class ConnectionLabelTests(unittest.TestCase):
    def test_exact_status_boundaries(self):
        now = datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)
        cases = [
            (None, "Unverified"),
            (timedelta(seconds=0), "Active"),
            (timedelta(minutes=2), "Active"),
            (timedelta(minutes=2, seconds=1), "Active 2m"),
            (timedelta(minutes=15), "Active 15m"),
            (timedelta(minutes=59, seconds=59), "Active 59m"),
            (timedelta(hours=1), "Active 1h"),
            (timedelta(hours=3, minutes=25), "Active 3h 25m"),
            (timedelta(hours=8), "Active 8h"),
            (timedelta(hours=8, seconds=1), "Unverified"),
        ]
        for ago, expected in cases:
            with self.subTest(ago=ago):
                when = now - ago if ago is not None else None
                self.assertEqual(connection_label(when, now=now), expected)


class AuthenticatedActivityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        reset_activity_cache()
        with self.Session() as db:
            db.add(SystemSetupDB(id=1, mail_server="rspamd", setup_completed=False))
            db.commit()
        self.now = datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)

    def tearDown(self):
        reset_activity_cache()
        self.engine.dispose()

    def record(self, adapter="engine", seconds=0, clock=100, **kwargs):
        with patch("core.mail_activity.time.monotonic", return_value=clock):
            return record_verified_adapter_activity(
                self.Session, adapter, now=self.now + timedelta(seconds=seconds), **kwargs)

    def test_first_activity_persisted_and_subsequent_requests_aggregated(self):
        events = []
        self.assertTrue(self.record(on_change=lambda: events.append("changed")))
        with self.Session() as db:
            row = db.get(SystemSetupDB, 1)
            self.assertTrue(row.setup_completed)
            self.assertEqual(row.last_authenticated_at.replace(tzinfo=timezone.utc), self.now)
        self.assertFalse(self.record(seconds=1, clock=101))
        self.assertEqual(events, ["changed"])
        self.assertTrue(self.record(seconds=46, clock=146, on_change=lambda: events.append("changed")))
        self.assertEqual(events, ["changed", "changed"])

    def test_wrong_adapter_never_marks_setup_verified(self):
        self.assertFalse(self.record(adapter="stalwart"))
        self.assertFalse(self.record(adapter="arbitrary", clock=101))
        with self.Session() as db:
            self.assertFalse(db.get(SystemSetupDB, 1).setup_completed)
        self.assertTrue(self.record(clock=102))

    def test_config_change_requires_new_matching_activity(self):
        self.assertTrue(self.record())
        with self.Session() as db:
            row = db.get(SystemSetupDB, 1)
            row.mail_server = "stalwart"
            row.setup_completed = False
            row.last_authenticated_at = None
            db.commit()
        reset_activity_cache()
        self.assertFalse(self.record(adapter="engine", clock=150))
        self.assertTrue(self.record(adapter="stalwart", clock=151))
        with self.Session() as db:
            self.assertTrue(db.get(SystemSetupDB, 1).setup_completed)

    def test_second_worker_does_not_duplicate_recent_write(self):
        self.assertTrue(self.record())
        reset_activity_cache()
        self.assertFalse(self.record(seconds=1, clock=200))
        with self.Session() as db:
            self.assertEqual(db.get(SystemSetupDB, 1).last_authenticated_at.replace(
                tzinfo=timezone.utc), self.now)

    def test_unconfigured_adapter_cannot_be_verified(self):
        with self.Session() as db:
            db.get(SystemSetupDB, 1).mail_server = "unconfigured"
            db.commit()
        self.assertFalse(self.record())
        with self.Session() as db:
            self.assertFalse(db.get(SystemSetupDB, 1).setup_completed)


if __name__ == "__main__":
    unittest.main()
