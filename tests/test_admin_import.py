"""Regression tests for safe, additive administrator configuration imports."""
from io import BytesIO
import hashlib
import tempfile
import unittest
import zipfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from admin_export import build_admin_export
from admin_import import (
    AdminImportError,
    apply_admin_import,
    inspect_admin_import,
    read_admin_import,
)
from database import Base
from models import DomainDB, SignatureDB, SignatureVersionDB
from signature_catalog import create_signature
from signature_storage import SignatureFileStore, SignatureStorageError
import uuid


class AdminImportTests(unittest.TestCase):
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

    def make_export(self, components=("domains", "signatures")):
        domain = DomainDB(
            domain_name="source.example.test",
            is_active=True,
            allow_overrides=True,
            inject_on_replies=True,
            trim_whitespace=True,
            strip_device_signatures=False,
            template_html="<p>Source template</p>",
            template_html_source="<p>Source template</p>",
        )
        self.db.add(domain)
        self.db.commit()
        create_signature(
            self.db,
            self.store,
            name="Source signature",
            domain_ids=[domain.id],
            html_content="<p>Original HTML</p>",
            created_by="admin@example.test",
        )
        return build_admin_export(self.db, self.store, list(components))[0]

    def test_preview_and_apply_remap_domain_ids_and_restore_html_versions(self):
        archive = self.make_export()
        target_dir = tempfile.TemporaryDirectory()
        target_store = SignatureFileStore(target_dir.name)
        target_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target = TargetSession()
        try:
            target.add(DomainDB(domain_name="unrelated.example.test"))
            target.commit()
            plan = inspect_admin_import(target, target_store, archive)
            self.assertTrue(plan["can_apply"], plan["conflicts"])
            self.assertEqual(plan["additions"], {"domains": 1, "signatures": 1})
            result = apply_admin_import(target, target_store, archive)
            self.assertEqual(result["status"], "imported")
            domain = target.query(DomainDB).filter_by(domain_name="source.example.test").one()
            signature = target.query(SignatureDB).filter_by(name="Source signature").one()
            self.assertEqual(signature.mode, "UNUSED")
            self.assertEqual(signature.domain_id, domain.id)
            self.assertNotEqual(signature.domain_id, 1)
            version = target.query(SignatureVersionDB).filter_by(signature_id=signature.id).one()
            html = target_store.read(signature.name, uuid.UUID(hex=version.id), version.sha256)
            self.assertEqual(html, "<p>Original HTML</p>")
        finally:
            target.close()
            target_engine.dispose()
            target_dir.cleanup()

    def test_exact_existing_domain_is_reused_but_not_overwritten(self):
        archive = self.make_export()
        target_dir = tempfile.TemporaryDirectory()
        target_store = SignatureFileStore(target_dir.name)
        target_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target = TargetSession()
        try:
            target.add(DomainDB(
                domain_name="source.example.test",
                is_active=True,
                allow_overrides=True,
                inject_on_replies=True,
                trim_whitespace=True,
                strip_device_signatures=False,
                template_html="<p>Source template</p>",
                template_html_source="<p>Source template</p>",
            ))
            target.commit()
            domain_before = target.query(DomainDB).one()
            plan = inspect_admin_import(target, target_store, archive)
            self.assertTrue(plan["can_apply"], plan["conflicts"])
            self.assertEqual(plan["reusable"]["domains"], 1)
            self.assertEqual(plan["additions"]["domains"], 0)
            apply_admin_import(target, target_store, archive)
            self.assertEqual(target.query(DomainDB).count(), 1)
            self.assertEqual(target.query(DomainDB).one().id, domain_before.id)
        finally:
            target.close()
            target_engine.dispose()
            target_dir.cleanup()

    def test_existing_signature_is_reported_as_conflict_and_never_overwritten(self):
        archive = self.make_export()
        target_dir = tempfile.TemporaryDirectory()
        target_store = SignatureFileStore(target_dir.name)
        target_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target = TargetSession()
        try:
            plan = inspect_admin_import(target, target_store, archive)
            self.assertTrue(plan["can_apply"], plan["conflicts"])
            signature_id = "a" * 32
            target.add(SignatureDB(id=signature_id, name="Source signature", scope="GLOBAL", mode="UNUSED", is_active=False))
            target.commit()
            plan = inspect_admin_import(target, target_store, archive)
            self.assertFalse(plan["can_apply"])
            self.assertTrue(any("Source signature" in item for item in plan["conflicts"]))
            with self.assertRaises(AdminImportError):
                apply_admin_import(target, target_store, archive)
            existing = target.get(SignatureDB, signature_id)
            self.assertFalse(existing.is_active)
        finally:
            target.close()
            target_engine.dispose()
            target_dir.cleanup()

    def test_asset_checksum_tampering_is_rejected(self):
        archive = self.make_export()
        with zipfile.ZipFile(BytesIO(archive), "r") as source:
            items = [(entry.filename, source.read(entry)) for entry in source.infolist()]
        output = BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, content in items:
                if name.endswith(".html"):
                    content = b"<p>tampered</p>"
                target.writestr(name, content)
        with self.assertRaises(AdminImportError):
            read_admin_import(output.getvalue())

    def test_archive_path_traversal_is_rejected(self):
        manifest = {
            "format": "opensigweave-component-export",
            "format_version": 1,
            "components": ["domains"],
            "data": {"domains": []},
            "assets": [],
        }
        output = BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", __import__("json").dumps(manifest))
            archive.writestr("../outside.txt", "unsafe")
        with self.assertRaises(AdminImportError):
            read_admin_import(output.getvalue())

    def test_failed_filesystem_write_rolls_back_database_changes(self):
        archive = self.make_export()
        target_dir = tempfile.TemporaryDirectory()
        target_store = SignatureFileStore(target_dir.name)
        (target_store.signatures_root / "Source signature").mkdir()
        target_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target = TargetSession()
        try:
            with self.assertRaises(AdminImportError):
                apply_admin_import(target, target_store, archive)
            self.assertEqual(target.query(DomainDB).count(), 0)
            self.assertEqual(target.query(SignatureDB).count(), 0)
        finally:
            target.close()
            target_engine.dispose()
            target_dir.cleanup()

    def test_partial_filesystem_write_is_cleaned_up_when_import_rolls_back(self):
        self.make_export()
        domain = self.db.query(DomainDB).filter_by(domain_name="source.example.test").one()
        create_signature(
            self.db,
            self.store,
            name="Second signature",
            domain_ids=[domain.id],
            html_content="<p>Second HTML</p>",
            created_by="admin@example.test",
        )
        archive = build_admin_export(self.db, self.store, ["domains", "signatures"])[0]

        class FailingStore(SignatureFileStore):
            def __init__(self, root):
                super().__init__(root)
                self.write_count = 0

            def write(self, version_id, signature_name, html):
                self.write_count += 1
                if self.write_count == 2:
                    raise SignatureStorageError("simulated write failure")
                return super().write(version_id, signature_name, html)

        target_dir = tempfile.TemporaryDirectory()
        target_store = FailingStore(target_dir.name)
        target_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(target_engine)
        TargetSession = sessionmaker(bind=target_engine, expire_on_commit=False)
        target = TargetSession()
        try:
            with self.assertRaises(AdminImportError):
                apply_admin_import(target, target_store, archive)
            self.assertEqual(target.query(DomainDB).count(), 0)
            self.assertEqual(target.query(SignatureDB).count(), 0)
            self.assertEqual(list(target_store.signatures_root.iterdir()), [])
        finally:
            target.close()
            target_engine.dispose()
            target_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
