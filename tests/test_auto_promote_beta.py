"""Regression tests for the fail-closed dev -> beta promotion gate."""
import subprocess
import unittest
from unittest.mock import patch

from scripts.auto_promote_beta import (
    POSTFIX_WORKFLOW,
    api,
    SECURITY_WORKFLOW,
    TESTS_WORKFLOW,
    expected_sha,
    requires_postfix,
    workflow_state,
)


class AutoPromotionPolicyTests(unittest.TestCase):
    def test_both_required_workflows_must_succeed(self):
        runs = [
            {"path": TESTS_WORKFLOW, "status": "completed", "conclusion": "success"},
            {"path": SECURITY_WORKFLOW, "status": "completed", "conclusion": "success"},
        ]
        self.assertEqual(workflow_state(runs, TESTS_WORKFLOW), "success")
        self.assertEqual(workflow_state(runs, SECURITY_WORKFLOW), "success")
        self.assertEqual(workflow_state(runs, POSTFIX_WORKFLOW), "pending")

    def test_missing_or_running_checks_cannot_pass(self):
        self.assertEqual(workflow_state([], TESTS_WORKFLOW), "pending")
        runs = [{"path": TESTS_WORKFLOW, "status": "in_progress", "conclusion": None}]
        self.assertEqual(workflow_state(runs, TESTS_WORKFLOW), "pending")

    def test_failure_and_cancelled_checks_block_promotion(self):
        for conclusion in ("failure", "cancelled", "timed_out", "skipped"):
            with self.subTest(conclusion=conclusion):
                runs = [{"path": SECURITY_WORKFLOW, "status": "completed", "conclusion": conclusion}]
                self.assertEqual(workflow_state(runs, SECURITY_WORKFLOW), "failed")

    def test_mixed_attempt_results_fail_closed(self):
        runs = [
            {"path": TESTS_WORKFLOW, "status": "completed", "conclusion": "success"},
            {"path": TESTS_WORKFLOW, "status": "completed", "conclusion": "failure"},
        ]
        self.assertEqual(workflow_state(runs, TESTS_WORKFLOW), "failed")

    def test_postfix_required_only_for_relevant_files(self):
        self.assertTrue(requires_postfix(["adapters/postfix_milter.py"]))
        self.assertTrue(requires_postfix([".github/workflows/postfix-milter-compat.yml"]))
        self.assertFalse(requires_postfix(["templates/index.html", "README.md"]))

    def test_github_api_errors_are_actionable(self):
        denied = subprocess.CompletedProcess(
            args=["gh", "api"], returncode=1, stdout="",
            stderr="HTTP 403: Resource not accessible by integration",
        )
        with patch("scripts.auto_promote_beta.subprocess.run", return_value=denied):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                api("POST", "repos/VeiglbergTech/OpenSigWeave/pulls")

    def test_commit_sha_validation(self):
        sha = "a" * 40
        self.assertEqual(expected_sha(sha), sha)
        for invalid in ("dev", "abc", "A" * 40, "a" * 39, "a" * 40 + "x"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    expected_sha(invalid)


if __name__ == "__main__":
    unittest.main()
