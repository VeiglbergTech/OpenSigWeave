"""Regression checks for branch promotion proofs; no live GitHub token."""
import unittest
from unittest.mock import patch
from scripts.verify_promotion import workflow_result, beta_dev_proof, source_ci_proof, verify
from scripts.auto_promote_beta import TESTS_WORKFLOW, SECURITY_WORKFLOW

D = "a" * 40
B = "b" * 40
T = "c" * 40

def run(path, sha=D, conclusion="success", when="2026-11-22T00:00:00Z"):
    return dict(path=path, head_sha=sha, head_branch="dev", event="push",
                status="completed", conclusion=conclusion, created_at=when, id=1)

class PromotionProofTests(unittest.TestCase):
    def test_exact_ci_result(self):
        self.assertEqual(workflow_result([run(TESTS_WORKFLOW)], TESTS_WORKFLOW, D), "success")
        self.assertEqual(workflow_result([run(TESTS_WORKFLOW, B)], TESTS_WORKFLOW, D), "missing")
    def test_failure_and_pending_block(self):
        for outcome in ("failure", "skipped", "timed_out"):
            self.assertEqual(workflow_result([run(SECURITY_WORKFLOW, conclusion=outcome)], SECURITY_WORKFLOW, D), "failed")
        pending = run(TESTS_WORKFLOW)
        pending["status"] = "in_progress"
        self.assertEqual(workflow_result([pending], TESTS_WORKFLOW, D), "failed")
    def test_cancelled_rerun_does_not_erase_valid_success(self):
        self.assertEqual(workflow_result([run(TESTS_WORKFLOW), run(TESTS_WORKFLOW, conclusion="cancelled", when="2026-11-23T00:00:00Z")], TESTS_WORKFLOW, D), "success")
    def test_new_failure_not_ignored(self):
        self.assertEqual(workflow_result([run(TESTS_WORKFLOW), run(TESTS_WORKFLOW, conclusion="failure", when="2026-11-23T00:00:00Z")], TESTS_WORKFLOW, D), "failed")
    def test_verified_merge_and_matching_tree(self):
        def fake(method, endpoint):
            if endpoint.endswith(B):
                return {"parents": [{"sha": "d" * 40}, {"sha": D}], "commit": {
                    "message": "Promote CI-verified dev to beta\nCI-verified dev commit: " + D, "tree": {"sha": T}}}
            return {"commit": {"tree": {"sha": T}}}
        with patch("scripts.verify_promotion.api", side_effect=fake):
            self.assertEqual(beta_dev_proof("VeiglbergTech/OpenSigWeave", B), D)
    def test_unproven_merge_blocks(self):
        with patch("scripts.verify_promotion.api", return_value={"parents": []}):
            with self.assertRaisesRegex(ValueError, "two-parent"):
                beta_dev_proof("VeiglbergTech/OpenSigWeave", B)
    def test_missing_security_scan_blocks(self):
        with patch("scripts.verify_promotion.api", side_effect=lambda method, path: (
            {"workflow_runs": [run(TESTS_WORKFLOW)]} if "/actions/runs?" in path else
            {"parents": []})):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                source_ci_proof("VeiglbergTech/OpenSigWeave", D)
    def test_invalid_branches_and_forks_block(self):
        for target, source, org in (("main", "dev", "VeiglbergTech/OpenSigWeave"),
                                    ("beta", "beta", "VeiglbergTech/OpenSigWeave"),
                                    ("beta", "dev", "untrusted/fork")):
            with self.assertRaises(ValueError):
                verify("VeiglbergTech/OpenSigWeave", target, source, org, D, 2)
    def test_stale_sha_blocks(self):
        with patch("scripts.verify_promotion.api", return_value={"commit": {"sha": B}}):
            with self.assertRaisesRegex(ValueError, "Stale"):
                verify("VeiglbergTech/OpenSigWeave", "beta", "dev", "VeiglbergTech/OpenSigWeave", D, 2)
    def test_api_403_blocks(self):
        with patch("scripts.verify_promotion.api", side_effect=RuntimeError("HTTP 403")):
            with self.assertRaisesRegex(RuntimeError, "403"):
                verify("VeiglbergTech/OpenSigWeave", "beta", "dev", "VeiglbergTech/OpenSigWeave", D, 2)

if __name__ == "__main__":
    unittest.main()
