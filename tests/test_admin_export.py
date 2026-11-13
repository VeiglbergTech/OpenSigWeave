import hashlib
import json
import tempfile
import unittest
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from admin_export import AdminExportError, build_admin_export
from core.modes import SignatureMode
from database import Base
from models import (
    DomainDB,
    IdentityProviderDB,
    SignatureGroupDB,
    SignatureGroupDomainDB,
    SignatureGroupMemberDB,
    SignatureRuleConditionDB,
    SignatureRuleDB,
)
from signature_catalog import add_signature_version, create_signature
from signature_storage import SignatureFileStore



class AdminExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SignatureFileStore(self.tempdir.name)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def _populate(self):
        domain = DomainDB(
            domain_name="example.org",
            template_html="<p>Sanitized fallback</p>",
            template_html_source="<!--[if mso]><p>Original source</p><![endif]-->",
        )
        provider = IdentityProviderDB(
            id="auth-main",
            display_name="Company Auth",
            provider_type="authentik",
            is_enabled=True,
        )
        self.db.add_all([domain, provider])
        self.db.commit()

        signature, version, variant = create_signature(
            self.db,
            self.store,
            name="Corporate",
            domain_ids=[domain.id],
            mode=SignatureMode.AUTO,
            html_content="<!--[if mso]><p>Signature source</p><![endif]-->",
            created_by="admin@example.org",
        )
        add_signature_version(
            self.db,
            self.store,
            signature.id,
            "<p>Second version</p>",
            created_by="admin@example.org",
        )
        group = SignatureGroupDB(
            id=uuid.uuid4().hex,
            name="Corporate pool",
            slug="corporate-pool",
            is_active=True,
        )
        self.db.add(group)
        self.db.flush()
        self.db.add(SignatureGroupDomainDB(group_id=group.id, domain_id=domain.id))
        self.db.add(SignatureGroupMemberDB(
            group_id=group.id, signature_id=signature.id, weight=3, is_enabled=True
        ))
        rule = SignatureRuleDB(
            id=uuid.uuid4().hex,
            name="Corporate rule",
            priority=10,
            action_mode="GROUP",
            group_id=group.id,
            is_enabled=True,
        )
        self.db.add(rule)
        self.db.flush()
        self.db.add(SignatureRuleConditionDB(
            rule_id=rule.id, kind="domain_equals", key="", value="example.org"
        ))
        self.db.commit()
        return domain, provider, signature, group, rule

    def test_export_contains_selected_components_and_original_html_assets(self):
        domain, provider, signature, group, rule = self._populate()
        payload, filename = build_admin_export(
            self.db, self.store,
            ["signatures", "domains", "rules", "groups", "identity_providers"],
        )
        self.assertTrue(filename.startswith("opensigweave-export-"))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], "opensigweave-component-export")
            self.assertEqual(
                set(manifest["components"]),
                {"signatures", "domains", "rules", "groups", "identity_providers"},
            )
            signature_item = manifest["data"]["signatures"][0]
            self.assertEqual(signature_item["id"], signature.id)
            self.assertEqual(len(signature_item["versions"]), 2)
            original_bytes = archive.read(signature_item["versions"][0]["asset"])
            original = original_bytes.decode()
            self.assertIn("<!--[if mso]>", original)
            asset = next(item for item in manifest["assets"] if item["path"] == signature_item["versions"][0]["asset"])
            self.assertEqual(asset["sha256"], hashlib.sha256(original_bytes).hexdigest())
            self.assertEqual(
                manifest["data"]["domains"][0]["template_html"],
                domain.template_html_source,
            )
            self.assertEqual(manifest["data"]["groups"][0]["id"], group.id)
            self.assertEqual(manifest["data"]["rules"][0]["id"], rule.id)
            provider_item = manifest["data"]["identity_providers"][0]
            self.assertEqual(provider_item["id"], provider.id)
            self.assertFalse(provider_item["credentials_included"])
            self.assertTrue(any("credential files" in item for item in manifest["exclusions"]))
            self.assertFalse(any(".env" in name for name in archive.namelist()))

    def test_export_rejects_empty_and_unknown_component_selection(self):
        self._populate()
        for selected in ([], ["all"], ["signatures", "../secrets"]):
            with self.subTest(selected=selected):
                with self.assertRaises(AdminExportError):
                    build_admin_export(self.db, self.store, selected)

    def test_export_fails_closed_when_signature_version_is_missing(self):
        _, _, signature, _, _ = self._populate()
        version = self.db.query(__import__("models").SignatureVersionDB).filter_by(
            signature_id=signature.id
        ).first()
        Path(self.tempdir.name, version.relative_path).unlink()
        with self.assertRaises(AdminExportError):
            build_admin_export(self.db, self.store, ["signatures"])


if __name__ == "__main__":
    unittest.main()
