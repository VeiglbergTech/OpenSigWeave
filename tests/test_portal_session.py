"""Regression coverage for portal sessions and stale-tab navigation."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import asyncio
from unittest.mock import Mock

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")

import main

ROOT = Path(__file__).resolve().parents[1]


class PortalSessionTests(unittest.TestCase):
    def test_expired_session_is_unauthorized(self):
        with self.assertRaises(main.HTTPException) as error:
            main.portal_session_status(SimpleNamespace(session={}))
        self.assertEqual(error.exception.status_code, 401)

    def test_portal_role_is_required(self):
        request = SimpleNamespace(session={"user": {
            "email": "user@example.org", "groups": [],
        }})
        with self.assertRaises(main.HTTPException) as error:
            main.portal_session_status(request)
        self.assertEqual(error.exception.status_code, 403)

    def test_authenticated_portal_user_gets_non_cacheable_status(self):
        for group in ("signature-users", "signature-admins"):
            with self.subTest(group=group):
                request = SimpleNamespace(session={"user": {
                    "email": "user@example.org", "groups": [group],
                }})
                response = main.portal_session_status(request)
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["cache-control"], "no-store, private")

    def test_canonical_paths_and_legacy_query_links(self):
        def request(path, query, groups):
            return SimpleNamespace(
                url=SimpleNamespace(path=path),
                query_params=query,
                session={"user": {"email": "operator@example.org", "groups": groups}},
            )

        for query, expected in (
            ({}, "/user"),
            ({"view": "personal"}, "/user"),
            ({"view": "admin", "section": "signatures"}, "/admin/signatures"),
            ({"view": "admin", "section": "imports"}, "/admin/import-export"),
            ({"view": "admin", "section": "scheduling"}, "/admin/rules"),
        ):
            with self.subTest(query=query):
                response = asyncio.run(main.read_root(
                    request("/", query, ["signature-admins"]), db=Mock()))
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], expected)

        for path, groups, expected in (
            ("/admin/signatures", ["signature-users"], 403),
            ("/admin/unknown", ["signature-admins"], 404),
        ):
            with self.subTest(path=path):
                with self.assertRaises(main.HTTPException) as error:
                    asyncio.run(main.read_root(request(path, {}, groups), db=Mock(),
                                               section=path.split("/")[-1]))
                self.assertEqual(error.exception.status_code, expected)

    def test_websocket_origin_and_authentication_contract(self):
        def fake(origin, host="signature.example.test"):
            return SimpleNamespace(headers={"origin": origin, "host": host})
        self.assertTrue(main._portal_websocket_same_origin(
            fake("https://signature.example.test")))
        self.assertTrue(main._portal_websocket_same_origin(
            fake("http://localhost:8085", "localhost:8085")))
        for origin in ("", "null", "https://evil.example.test",
                       "https://signature.example.test.evil.test",
                       "https://signature.example.test:8443",
                       "https://signature.example.test/path"):
            with self.subTest(origin=origin):
                self.assertFalse(main._portal_websocket_same_origin(fake(origin)))

        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.websocket("/ws/portal")', source)
        self.assertIn("require_signature_user(websocket)", source)
        self.assertIn('if not _portal_websocket_same_origin(websocket)', source)
        self.assertIn('is_admin = "signature-admins" in user.get("groups", [])', source)
        self.assertIn("and is_admin:", source)

    def test_realtime_status_and_backup_avoid_browser_polling(self):
        portal = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        self.assertIn('new WebSocket(scheme + "//" + window.location.host + "/ws/portal")', portal)
        self.assertIn('send("subscribe-status")', portal)
        self.assertIn('send("subscribe-backups")', portal)
        self.assertIn('portalRealtime.refreshStatus()', portal)
        self.assertIn('applyStatus(data)', portal)
        self.assertNotIn('setInterval(', portal)
        self.assertNotIn('fetch("/api/admin/system-status"', portal)
        self.assertIn('Live via WebSocket', template)
        self.assertIn('@osw-live-backups.window="fetchAll()"', template)

    def test_navigation_and_api_failures_check_session(self):
        portal = (ROOT / "static/js/portal.js").read_text(encoding="utf-8")
        template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        self.assertIn('originalFetch("/api/session/status"', portal)
        self.assertIn('window.location.replace("/login")', portal)
        self.assertIn("async setAdminTab(section)", portal)
        self.assertIn("async openPersonal()", portal)
        self.assertIn("async openAdmin()", portal)
        self.assertIn('window.addEventListener("popstate", async', portal)
        self.assertIn('response.status === 401 || response.status === 403', portal)
        # Cache-busting versions change with fixes; the security check must
        # assert the guarded script is loaded, not a specific release string.
        self.assertIn('<script src="/static/js/portal.js?v=', template)


if __name__ == "__main__":
    unittest.main()
