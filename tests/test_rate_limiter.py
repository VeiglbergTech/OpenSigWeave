"""Tests for database-backed request rate limiting."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from starlette.requests import Request
import ipaddress
import hashlib
import hmac
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete
from database import engine as application_engine

from sqlalchemy import create_engine

from models import Base, RateLimitBucketDB
from rate_limiter import _policy_for_path, _rate_limit_hash_key, client_identity, consume_rate_limit

TEST_HASH_KEY = b"opensigweave-test-rate-limit-key"


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



    def test_legacy_and_managed_oidc_callbacks_share_rate_limit_policy(self):
        with patch.dict("os.environ", {"AUTH_CALLBACK_RATE_LIMIT": "7"}, clear=False):
            expected = ("auth-callback", 7, 300)
            self.assertEqual(_policy_for_path("/auth"), expected)
            self.assertEqual(_policy_for_path("/auth/provider-123"), expected)
            self.assertEqual(_policy_for_path("/auth/another-provider"), expected)
            self.assertIsNone(_policy_for_path("/authentic"))

    def test_admin_component_exports_have_a_separate_rate_limit(self):
        with patch.dict("os.environ", {"ADMIN_EXPORT_RATE_LIMIT": "3"}, clear=False):
            self.assertEqual(_policy_for_path("/api/admin/exports"), ("admin-export", 3, 60))
            self.assertIsNone(_policy_for_path("/api/admin/exports/extra"))

    def test_rate_limit_hash_key_uses_configured_session_secret(self):
        with patch.dict("os.environ", {"SECRET_KEY": "s" * 48}, clear=False):
            first = _rate_limit_hash_key()
            second = _rate_limit_hash_key()
        self.assertEqual(len(first), 32)
        self.assertEqual(first, second)
        self.assertNotEqual(first, ("s" * 48).encode("utf-8"))

    def test_rate_limit_hash_key_rejects_placeholder_secret(self):
        with patch.dict("os.environ", {"SECRET_KEY": "REPLACE_WITH_RANDOM_SECRET"}, clear=False):
            with self.assertRaises(RuntimeError):
                _rate_limit_hash_key()

    def test_fixed_window_increments_atomically_and_enforces_limit(self):
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine, hash_key=TEST_HASH_KEY), 1)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=122, bind=self.engine, hash_key=TEST_HASH_KEY), 2)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=123, bind=self.engine, hash_key=TEST_HASH_KEY), 3)

    def test_new_window_resets_counter(self):
        consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine, hash_key=TEST_HASH_KEY)
        self.assertEqual(consume_rate_limit("login:client-a", 2, 60, now=180, bind=self.engine, hash_key=TEST_HASH_KEY), 1)

    def test_buckets_are_isolated(self):
        consume_rate_limit("login:client-a", 2, 60, now=121, bind=self.engine, hash_key=TEST_HASH_KEY)
        self.assertEqual(consume_rate_limit("login:client-b", 2, 60, now=121, bind=self.engine, hash_key=TEST_HASH_KEY), 1)

    def test_only_digest_is_persisted(self):
        identity = "198.51.100.25"
        consume_rate_limit(f"login:{identity}", 2, 60, now=121, bind=self.engine, hash_key=TEST_HASH_KEY)
        with self.engine.connect() as connection:
            stored = connection.execute(
                RateLimitBucketDB.__table__.select()
            ).scalar_one()
        self.assertNotIn(identity, stored)
        self.assertNotEqual(stored, hashlib.sha256(f"login:{identity}".encode("utf-8")).hexdigest())



    @unittest.skipUnless(application_engine.dialect.name == "postgresql", "PostgreSQL concurrency test")
    def test_postgresql_concurrent_increments_are_atomic(self):
        bucket = f"concurrency-test:{uuid.uuid4().hex}"
        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                counts = list(executor.map(
                    lambda _: consume_rate_limit(bucket, 100, 60, hash_key=TEST_HASH_KEY),
                    range(32),
                ))
            self.assertEqual(sorted(counts), list(range(1, 33)))
        finally:
            digest = hmac.new(TEST_HASH_KEY, bucket.encode("utf-8"), hashlib.sha256).hexdigest()
            with application_engine.begin() as connection:
                connection.execute(
                    delete(RateLimitBucketDB.__table__).where(
                        RateLimitBucketDB.bucket_key == digest
                    )
                )

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
