"""Regression guards for retirement of the hard-coded signature mode model."""
from pathlib import Path
import inspect
import re
import unittest

import main
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
RETIRED = tuple("".join(parts) for parts in (
    ("AU", "TO"),
    ("PROF", "ESSIONAL"),
    ("INTER", "NAL"),
))
COMPATIBILITY_FILES = {
    "admin_import.py",
    "migrations/versions/0011_legacy_signature_groups.py",
    "migrations/versions/0014_retire_signature_modes.py",
    "tests/test_migration_compatibility.py",
    # Adversarial Postfix tests intentionally inject retired values to ensure
    # client-controlled legacy selector headers are ignored and stripped.
    ".github/workflows/postfix-milter-compat.yml",
    # Historical migration/release documentation must name retired modes so
    # operators can identify and safely upgrade legacy installations.
    "docs/UPGRADE.md",
    "docs/RELEASE_NOTES_v2026.11.md",
    "docs/ENGINE_SERVICE.md",
}
TEXT_SUFFIXES = {
    ".py", ".lua", ".js", ".cjs", ".html", ".md", ".yml", ".yaml",
    ".toml", ".example", ".sh",
}


class LegacyModeRetirementTests(unittest.TestCase):
    def test_legacy_mode_column_is_retained_only_for_database_compatibility(self):
        self.assertIn("mode", main.SignatureDB.__table__.columns)
        self.assertEqual(main.SignatureDB.__table__.columns["mode"].default.arg, "UNUSED")

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

    def test_regular_api_explicitly_rejects_retired_selection_payloads(self):
        # Ensure old clients receive a clear validation error instead of a
        # successful response that silently ignores the removed fields.
        valid_requests = (
            (main.SignatureCreateRequest, {"name": "New", "html_content": "<p>New</p>"}),
            (main.SignatureUpdateRequest, {"name": "Existing"}),
            (main.SignatureVersionCreateRequest, {"html_content": "<p>Version</p>"}),
            (main.SignatureRuleCreateRequest, {"name": "Rule"}),
            (main.SignatureScheduleRequest, {
                "name": "Schedule", "target_type": "SIGNATURE",
                "signature_id": "a" * 32,
                "starts_at": "2027-01-01T00:00:00Z",
                "ends_at": "2027-01-02T00:00:00Z",
            }),
        )
        retired_fields = (
            ("mode", RETIRED[0]),
            ("pool_name", RETIRED[1]),
            ("variants", []),
            ("variant_id", "legacy"),
            ("weight", 2),
        )
        for model, valid in valid_requests:
            model.model_validate(valid)  # Ensure each baseline really is valid.
            for field, value in retired_fields:
                with self.subTest(model=model.__name__, field=field):
                    with self.assertRaises(ValidationError):
                        model.model_validate({**valid, field: value})

        for mode in RETIRED:
            with self.subTest(action_mode=mode):
                with self.assertRaises(ValidationError):
                    main.SignatureRuleCreateRequest.model_validate(
                        {"name": "Rule", "action_mode": mode}
                    )
                with self.assertRaises(ValidationError):
                    main.SignatureScheduleRequest.model_validate({
                        "name": "Schedule", "target_type": mode,
                        "starts_at": "2027-01-01T00:00:00Z",
                        "ends_at": "2027-01-02T00:00:00Z",
                    })

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
            if not path.is_file() or any(part in {".git", "node_modules", ".venv", "venv"} for part in path.parts) or path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in COMPATIBILITY_FILES or relative == "tests/test_legacy_mode_retirement.py":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            # Match standalone mode tokens, not ordinary SQL terms like AUTOINCREMENT.
            found = [token for token in RETIRED if re.search(
                r"(?<![A-Z])" + re.escape(token) + r"(?![A-Z])", text
            )]
            if found:
                offenders.append((relative, found))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
