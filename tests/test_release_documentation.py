"""Protect release-documentation invariants that have previously drifted."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseDocumentationTests(unittest.TestCase):
    def test_candidate_documents_are_present_and_linked(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("UPGRADE.md", "RELEASE_NOTES_v2026.11.md", "RELEASE_ACCEPTANCE_v2026.11.md"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / "docs" / name).is_file())
                self.assertIn(f"docs/{name}", readme)

    def test_no_obsolete_multipart_claim_in_api_reference(self):
        reference = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
        self.assertNotIn("deliberately leaves every top-level", reference)
        self.assertIn("multipart/alternative", reference)
        self.assertIn("signature-assets", reference)

    def test_no_legacy_mode_engine_contract(self):
        contract = (ROOT / "docs" / "ENGINE_SERVICE.md").read_text(encoding="utf-8")
        self.assertNotIn("authenticated explicit mode selection", contract)
        self.assertNotIn("explicit/default mode", contract)
        self.assertIn("legacy mode selectors have no effect", contract)

    def test_release_notes_are_not_marked_published(self):
        notes = (ROOT / "docs" / "RELEASE_NOTES_v2026.11.md").read_text(encoding="utf-8")
        for title in ("Highlights", "Breaking changes", "Upgrade", "Security-related", "Known limitations"):
            self.assertIn(title, notes)
        self.assertIn("NOT RELEASED", notes)
        acceptance = (ROOT / "docs" / "RELEASE_ACCEPTANCE_v2026.11.md").read_text(encoding="utf-8")
        self.assertIn("NOT APPROVED", acceptance)
        self.assertIn("DKIM", acceptance)


if __name__ == "__main__":
    unittest.main()
