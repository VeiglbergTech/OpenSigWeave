"""Release version, main provenance, workflow and no-cost runner tests."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release_tags


class VersionTests(unittest.TestCase):
    def test_alpha_sequence(self):
        self.assertEqual(release_tags.parse_release_tag("2026.11-alpha"),
                         ("2026.11-alpha", "alpha"))
        self.assertEqual(release_tags.parse_release_tag("2026.11-alpha1"),
                         ("2026.11-alpha1", "alpha"))
        self.assertEqual(release_tags.parse_release_tag("2026.11-alpha2"),
                         ("2026.11-alpha2", "alpha"))

    def test_beta_and_latest_are_separate(self):
        self.assertEqual(release_tags.parse_release_tag("2026.11-beta"),
                         ("2026.11-beta", "beta"))
        self.assertEqual(release_tags.parse_release_tag("2026.11-beta1"),
                         ("2026.11-beta1", "beta"))
        self.assertEqual(release_tags.parse_release_tag("2026.11"),
                         ("2026.11", "latest"))

    def test_rejects_invalid_versions(self):
        for invalid in (
            "2026.11-alpha0", "2026.11-alpha01", "2026.13-alpha",
            "2026.00", "v2026.11-alpha", "2026.11-rc",
            "2026.11-alpha-1", "2026.11-alpha;exit",
        ):
            with self.subTest(tag=invalid):
                with self.assertRaises(ValueError):
                    release_tags.parse_release_tag(invalid)

    @patch.object(release_tags, "github_get")
    def test_main_head_must_match_tag_commit(self, get):
        get.return_value = {"object": {"sha": "a" * 40}}
        self.assertTrue(release_tags.main_matches_sha("secret", "a" * 40))
        self.assertFalse(release_tags.main_matches_sha("secret", "b" * 40))

    @patch.object(release_tags, "github_get")
    def test_does_not_accept_dev_or_pr_checks(self, get):
        revision = "c" * 40
        get.return_value = {"workflow_runs": [
            {"head_sha": revision, "head_branch": "dev", "event": "push",
             "status": "completed", "conclusion": "success"},
            {"head_sha": revision, "head_branch": "main", "event": "pull_request",
             "status": "completed", "conclusion": "success"},
        ]}
        self.assertEqual(release_tags.workflow_state("secret", "tests.yml", revision),
                         "waiting")
        get.return_value["workflow_runs"].append(
            {"head_sha": revision, "head_branch": "main", "event": "push",
             "status": "completed", "conclusion": "success"}
        )
        self.assertEqual(release_tags.workflow_state("secret", "tests.yml", revision),
                         "success")

    def test_image_only_compose_and_explicit_build_overlay(self):
        root = Path(__file__).resolve().parents[1]
        release = (root / "compose.yaml").read_text()
        self.assertIn("ghcr.io/veiglbergtech/opensigweave:", release)
        self.assertNotIn("    build:", release)
        overlay = (root / "compose.build.yaml").read_text()
        self.assertIn("    build:", overlay)
        self.assertIn("pull_policy: build", overlay)

    def test_publish_only_self_hosted_without_artifacts(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/publish-ghcr.yml").read_text()
        self.assertIn("runs-on: [self-hosted, linux, x64]", workflow)
        self.assertIn("scripts/release_tags.py", workflow)
        self.assertNotIn("ubuntu-latest", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertIn("packages: write", workflow)


if __name__ == "__main__":
    unittest.main()
