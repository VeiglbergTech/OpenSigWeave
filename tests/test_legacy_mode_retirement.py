"""Regression guards for retirement of the hard-coded signature mode model."""
from pathlib import Path
import inspect
import unittest

import main


ROOT = Path(__file__).resolve().parents[1]
RETIRED = tuple("".join(parts) for parts in (
    ("AU", "TO"),
    ("PROF", "ESSIONAL"),
    ("INTER", "NAL"),
))
COMPATIBILITY_FILES = {
    "admin_import.py",
    "migrations/versions/0009_signature_groups.py",
    "migrations/versions/0011_legacy_signature_groups.py",
    "migrations/versions/0014_retire_legacy_signature_modes.py",
    "tests/test_migration_compatibility.py",
}
TEXT_SUFFIXES = {
    ".py", ".lua", ".js", ".cjs", ".html", ".md", ".yml", ".yaml",
    ".toml", ".example", ".sh",
}


class LegacyModeRetirementTests(unittest.TestCase):
    def test_regular_admin_and_engine_api_have_no_mode_contract(self):
        self.assertNotIn("mode", main.SignatureCreateRequest.model_fields)
        self.assertNotIn("weight", main.SignatureCreateRequest.model_fields)
        self.assertFalse(hasattr(main, "SignatureVariantCreateRequest"))
        self.assertFalse(hasattr(main, "SignatureVariantUpdateRequest"))
        self.assertNotIn("x_signature_mode", inspect.signature(main.get_engine_signature).parameters)

        paths = {
            route.path
            for route in main.app.routes
            if getattr(route, "path", None)
        }
        self.assertFalse(any("/variants" in path or "/signature-variants" in path for path in paths))

    def test_frontend_does_not_expose_retired_modes(self):
        frontend = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "templates/index.html",
                "static/js/portal.js",
                "static/js/rich-editor.js",
            )
        )
        for token in RETIRED:
            self.assertNotIn(token, frontend)

    def test_runtime_selection_does_not_read_legacy_mode_storage(self):
        runtime = "\n".join(
            (ROOT / path).read_text(encoding="utf-8")
            for path in (
                "main.py",
                "signature_catalog.py",
                "core/engine_contract.py",
                "core/engine_service.py",
            )
        )
        for token in RETIRED:
            self.assertNotIn(token, runtime)
        self.assertNotIn("select_and_record_variant", runtime)
        self.assertNotIn("SignatureMode", runtime)

    def test_retired_mode_tokens_only_remain_in_explicit_migration_compatibility(self):
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in COMPATIBILITY_FILES or relative == "tests/test_legacy_mode_retirement.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            found = [token for token in RETIRED if token in text]
            if found:
                offenders.append((relative, found))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
