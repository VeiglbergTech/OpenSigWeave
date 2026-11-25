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
    promote,
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

    def test_fast_forward_moves_beta_without_merge_or_force(self):
        verified, old = "a" * 40, "b" * 40
        with patch("scripts.auto_promote_beta.branch_sha", side_effect=[verified, old, verified, old, verified]), \
             patch("scripts.auto_promote_beta.api", side_effect=[
                 {"ahead_by": 3, "behind_by": 0},
                 {"object": {"sha": verified}},
             ]) as github_api:
            promote("VeiglbergTech/OpenSigWeave", verified)
        github_api.assert_any_call(
            "PATCH", "repos/VeiglbergTech/OpenSigWeave/git/refs/heads/beta",
            {"sha": verified, "force": False}
        )

    def test_ref_read_lag_after_patch_does_not_report_false_failure(self):
        verified, old = "a" * 40, "b" * 40
        with patch("scripts.auto_promote_beta.branch_sha",
                   side_effect=[verified, old, verified, old, old, verified]), \
             patch("scripts.auto_promote_beta.api", side_effect=[
                 {"ahead_by": 1, "behind_by": 0},
                 {"object": {"sha": verified}},
             ]), \
             patch("scripts.auto_promote_beta.time.sleep") as sleep:
            promote("VeiglbergTech/OpenSigWeave", verified)
        sleep.assert_called_once_with(2)

    def test_post_patch_unrelated_ref_change_still_blocks(self):
        verified, old, unexpected = "a" * 40, "b" * 40, "c" * 40
        with patch("scripts.auto_promote_beta.branch_sha",
                   side_effect=[verified, old, verified, old, unexpected]), \
             patch("scripts.auto_promote_beta.api", side_effect=[
                 {"ahead_by": 1, "behind_by": 0},
                 {"object": {"sha": verified}},
             ]), \
             patch("scripts.auto_promote_beta.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "unexpected commit"):
                promote("VeiglbergTech/OpenSigWeave", verified)
        sleep.assert_not_called()

    def test_post_patch_unconfirmed_ref_still_blocks(self):
        verified, old = "a" * 40, "b" * 40
        with patch("scripts.auto_promote_beta.branch_sha",
                   side_effect=[verified, old, verified, old] + [old] * 8), \
             patch("scripts.auto_promote_beta.api", side_effect=[
                 {"ahead_by": 1, "behind_by": 0},
                 {"object": {"sha": verified}},
             ]), \
             patch("scripts.auto_promote_beta.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                promote("VeiglbergTech/OpenSigWeave", verified)
        self.assertEqual(sleep.call_count, 7)

    def test_diverged_beta_is_never_rewritten_or_merged(self):
        verified, old = "a" * 40, "b" * 40
        with patch("scripts.auto_promote_beta.branch_sha", side_effect=[verified, old]), \
             patch("scripts.auto_promote_beta.api", return_value={"ahead_by": 7, "behind_by": 19}) as github_api:
            with self.assertRaisesRegex(RuntimeError, "ancestor"):
                promote("VeiglbergTech/OpenSigWeave", verified)
        self.assertEqual(github_api.call_count, 1)

    def test_noop_when_beta_is_exact_dev_head(self):
        verified = "a" * 40
        with patch("scripts.auto_promote_beta.branch_sha", side_effect=[verified, verified]), \
             patch("scripts.auto_promote_beta.api", return_value={"ahead_by": 0, "behind_by": 0}) as github_api:
            promote("VeiglbergTech/OpenSigWeave", verified)
        self.assertEqual(github_api.call_count, 1)

    def test_api_formats_explicit_false_as_json_boolean(self):
        response = subprocess.CompletedProcess(args=["gh", "api"], returncode=0,
                                               stdout='{"object":{"sha":"abc"}}', stderr="")
        with patch("scripts.auto_promote_beta.subprocess.run", return_value=response) as call:
            api("PATCH", "repos/owner/project/git/refs/heads/beta", {"sha": "a" * 40, "force": False})
        self.assertIn("-F", call.call_args.args[0])
        self.assertIn("force=false", call.call_args.args[0])

    def test_commit_sha_validation(self):
        sha = "a" * 40
        self.assertEqual(expected_sha(sha), sha)
        for invalid in ("dev", "abc", "A" * 40, "a" * 39, "a" * 40 + "x"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    expected_sha(invalid)


if __name__ == "__main__":
    unittest.main()
