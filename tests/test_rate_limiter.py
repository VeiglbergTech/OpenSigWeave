"""Tests for database-backed request rate limiting."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request
import ipaddress

from sqlalchemy import create_engine

from models import Base, RateLimitBucketDB
from rate_limiter import client_identity, consume_rate_limit


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


    def test_untrusted_forwarded_header_is_ignored(self):
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.99")],
             "client": ("198.51.100.10", 12345), "server": ("test", 80), "scheme": "http", "query_string": b""}
        )
        with patch("rate_limiter.TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/8"),)):
            self.assertEqual(client_identity(request), "198.51.100.10")

    def test_trusted_proxy_forwarded_chain_is_resolved_from_the_right(self):
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.2")],
             "client": ("10.0.0.3", 12345), "server": ("test", 80), "scheme": "http", "query_string": b""}
        )
        with patch("rate_limiter.TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/8"),)):
            self.assertEqual(client_identity(request), "203.0.113.7")

    def test_malformed_forwarded_header_falls_back_to_peer(self):
        request = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"attacker-controlled")],
             "client": ("10.0.0.3", 12345), "server": ("test", 80), "scheme": "http", "query_string": b""}
        )
        with patch("rate_limiter.TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/8"),)):
            self.assertEqual(client_identity(request), "10.0.0.3")


if __name__ == "__main__":
    unittest.main()
