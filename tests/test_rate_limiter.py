"""Tests for database-backed request rate limiting."""
import pathlib
import tempfile
import unittest

from sqlalchemy import create_engine

from models import Base, RateLimitBucketDB
from rate_limiter import consume_rate_limit


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{pathlib.Path(self.tempdir.name) / 'limits.db'}",
            connect_args={"check_same_thread": False},
        )
        RateLimitBucketDB.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_fixed_window_increments_atomically_and_enforces_limit(self):
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine), 1)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=122, bind=self.engine), 2)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=123, bind=self.engine), 3)

    def test_new_window_resets_counter(self):
        consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=180, bind=self.engine), 1)

    def test_buckets_are_isolated(self):
        consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine)
        self.assertEqual(consume_rate_limit("login:client-b", 2, 60, now=121, bind=self.engine), 1)

    def test_only_digest_is_persisted(self):
        identity = "198.51.100.25"
        consume_rate_limit(f"login:{identity}", 2, 60, now=121, bind=self.engine)
        with self.engine.connect() as connection:
            stored = connection.execute(
                RateLimitBucketDB.__table__.select()
            ).scalar_one()
        self.assertNotIn(identity, stored)


if __name__ == "__main__":
    unittest.main()
