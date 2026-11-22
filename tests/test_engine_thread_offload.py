"""Regression: the synchronous engine work must not freeze the ASGI event loop."""
import asyncio
import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("APP_ALLOWED_HOSTS", "localhost,127.0.0.1")
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@example.org")
from auth_config import hash_local_admin_password
os.environ.setdefault("LOCAL_ADMIN_PASSWORD_HASH", hash_local_admin_password("correct horse battery staple"))

import main
from core.engine_contract import SignatureEngineResult, normalize_engine_request


class BlockingEngine:
    def __init__(self):
        self.thread_id = None

    async def resolve(self, db, request, *, commit_selection=True):
        self.thread_id = threading.get_ident()
        # Intentional blocking call: this simulates a slow synchronous DB query.
        time.sleep(0.15)
        return SignatureEngineResult(
            html="<p>Rendered</p>", inject_on_replies=False,
            trim_whitespace=True, strip_device_signatures=False,
        )


class EngineThreadOffloadTests(unittest.TestCase):
    def test_resolver_yields_to_asgi_event_loop_during_blocking_db_io(self):
        service = BlockingEngine()
        async def exercise():
            loop_thread_id = threading.get_ident()
            request = normalize_engine_request("sender@example.org")
            task = asyncio.create_task(main.resolve_signature_engine(None, request))
            # If the ASGI event loop were blocked by the database I/O, this
            # coroutine would only resume after the task had completed.
            await asyncio.sleep(0.03)
            self.assertFalse(task.done(), "ASGI event loop was blocked")
            result = await task
            self.assertEqual(result.html, "<p>Rendered</p>")
            self.assertNotEqual(service.thread_id, loop_thread_id)
        with patch.object(main, "_signature_engine_service", return_value=service):
            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
