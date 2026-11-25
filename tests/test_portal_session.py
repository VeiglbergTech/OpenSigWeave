"""Regression coverage for portal sessions and stale-tab navigation."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

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
