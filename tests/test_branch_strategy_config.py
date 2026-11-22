"""Regression checks for repository-encoded branch strategy.

These checks validate files in git, not the GitHub-hosted rulesets.
Repository administrators still need to enable branch protection in GitHub.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPECTED_BRANCHES = ["dev", "beta", "main"]


def _target_branches(path: Path, event: str) -> list[str]:
    content = path.read_text(encoding="utf-8")
    match = re.search(
        rf"(?m)^  {re.escape(event)}:\n    branches: \[([^\n]+)\]",
        content,
    )
    if match is None:
        raise AssertionError(f"{path.name} missing {event} branch trigger")
    return re.findall(r"\b(?:dev|beta|main)\b", match.group(1))


class BranchStrategyConfigurationTests(unittest.TestCase):
    def test_regular_and_security_ci_cover_all_managed_branches(self):
        for filename in ("tests.yml", "security.yml"):
            for event in ("push", "pull_request"):
                with self.subTest(filename=filename, event=event):
                    self.assertEqual(
                        _target_branches(WORKFLOWS / filename, event),
                        EXPECTED_BRANCHES,
                    )

    def test_dependabot_only_targets_dev(self):
        content = (ROOT / ".github" / "dependabot.yml").read_text(
            encoding="utf-8"
        )
        targets = re.findall(r"(?m)^\s+target-branch:\s+(\S+)", content)
        self.assertEqual(targets, ["dev", "dev", "dev"])

    def test_promotion_gate_has_no_automated_release(self):
        content = (WORKFLOWS / "promotion-policy.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("TARGET_BRANCH", content)
        self.assertIn("SOURCE_BRANCH", content)
        self.assertIn("SOURCE_REPOSITORY", content)
        self.assertTrue((ROOT / "scripts" / "verify_promotion.py").is_file())
        self.assertNotIn("workflow_run:", content)
        self.assertNotIn("push:", content)
        self.assertNotIn("release:", content)

    def test_postfix_compatibility_checks_are_path_filtered(self):
        content = (WORKFLOWS / "postfix-milter-compat.yml").read_text(
            encoding="utf-8"
        )
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                self.assertEqual(
                    _target_branches(WORKFLOWS / "postfix-milter-compat.yml", event),
                    EXPECTED_BRANCHES,
                )
        self.assertEqual(content.count("    paths:"), 2)


if __name__ == "__main__":
    unittest.main()
