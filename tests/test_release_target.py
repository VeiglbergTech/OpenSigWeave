"""Release selector must include prereleases, reject missing releases and validate tags."""
import json
import unittest
from unittest.mock import patch
from scripts.release_target import latest_release, release_tag_sha, DeploymentError


class ReleaseTargetTests(unittest.TestCase):
    def test_latest_pre_release_used(self):
        items = [
            {"tag_name": "2026.11-alpha", "published_at": "2026-11-25T01:00:00Z", "draft": False, "prerelease": True},
            {"tag_name": "2026.10", "published_at": "2026-10-25T01:00:00Z", "draft": False, "prerelease": False},
            {"tag_name": "2026.12", "published_at": "2026-12-01T01:00:00Z", "draft": True},
        ]
        self.assertEqual(latest_release(fetch=lambda: json.dumps(items).encode()), "2026.11-alpha")

    def test_no_release_fails_closed(self):
        with self.assertRaises(DeploymentError):
            latest_release(fetch=lambda: b"[]")

    def test_annotated_tag_uses_peeled_commit(self):
        with patch("scripts.release_target.git", return_value=(
            "a" * 40 + " refs/tags/2026.11-alpha\n"
            + "b" * 40 + " refs/tags/2026.11-alpha^{}\n"
        )):
            self.assertEqual(release_tag_sha("2026.11-alpha"), "b" * 40)

    def test_tag_injection_rejected(self):
        with self.assertRaises(DeploymentError):
            release_tag_sha("main; rm -rf /")

if __name__ == "__main__":
    unittest.main()
