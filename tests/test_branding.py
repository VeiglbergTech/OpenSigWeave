"""Branding validation, persistence, safe public assets and reset regressions."""
import base64
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("APP_ALLOWED_HOSTS", "signature-test.example.org,app.example.org")
from auth_config import hash_local_admin_password
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@example.org")
os.environ.setdefault("LOCAL_ADMIN_PASSWORD_HASH", hash_local_admin_password("correct horse battery staple"))

import core.branding as branding
import main
from core.branding import DEFAULT_APP_COLOR, DEFAULT_APP_NAME, branding_context, decode_branding_data_url
from database import Base, get_db
from fastapi import HTTPException
from fastapi.testclient import TestClient
from models import BrandingSettingsDB


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (len(data).to_bytes(4, "big") + kind + data
            + zlib.crc32(kind + data).to_bytes(4, "big"))


def valid_png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00\xff")
    return (branding.PNG_SIGNATURE + png_chunk(b"IHDR", ihdr)
            + png_chunk(b"IDAT", pixels) + png_chunk(b"IEND", b""))


def valid_ico() -> bytes:
    image = valid_png()
    entry = bytes([1, 1, 0, 0]) + (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
    entry += len(image).to_bytes(4, "little") + (22).to_bytes(4, "little")
    return b"\x00\x00\x01\x00\x01\x00" + entry + image


def data_url(payload: bytes, mime: str = "image/png") -> str:
    return "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii")


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.admin = SimpleNamespace(session={"user": {"groups": ["signature-admins"]}})
        self.normal = SimpleNamespace(session={"user": {"groups": ["signature-users"]}})

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def update(self, **changes):
        payload = {"app_name": "Example Signatures", "app_color": "#123456"}
        payload.update(changes)
        return main.update_admin_branding(main.BrandingUpdateRequest(**payload), self.admin, self.db)

    def test_defaults_are_available_without_database_row(self):
        result = branding_context(self.db)
        self.assertEqual(result["app_name"], DEFAULT_APP_NAME)
        self.assertEqual(result["app_color"], DEFAULT_APP_COLOR)
        self.assertFalse(result["has_custom_logo"])
        self.assertFalse(result["has_custom_favicon"])

    def test_valid_png_and_ico_files_are_accepted(self):
        png = valid_png()
        ico = valid_ico()
        self.assertEqual(decode_branding_data_url(data_url(png), allowed_mimes={"image/png"}, max_bytes=512 * 1024), (png, "image/png"))
        self.assertEqual(decode_branding_data_url(data_url(ico, "image/x-icon"), allowed_mimes={"image/x-icon"}, max_bytes=256 * 1024), (ico, "image/x-icon"))
        self.assertEqual(decode_branding_data_url(data_url(ico, "image/vnd.microsoft.icon"), allowed_mimes={"image/vnd.microsoft.icon"}, max_bytes=256 * 1024)[0], ico)

    def test_bad_images_are_rejected_not_just_signature_checked(self):
        png = valid_png()
        invalid = [
            b"not a PNG", branding.PNG_SIGNATURE + b"payload",
            png[:-1], png + b"trailing-data", png[:40], png[:48] + b"evil" + png[52:],
            png[:20] + b"\x00" + png[21:],  # corrupt IHDR dimensions/CRC
        ]
        for image in invalid:
            with self.subTest(size=len(image)):
                with self.assertRaises(ValueError):
                    decode_branding_data_url(data_url(image), allowed_mimes={"image/png"}, max_bytes=512 * 1024)
        for bad_ico in (b"\x00\x00\x01\x00", b"\x00\x00\x01\x00\x01\x00" + b"\x00" * 16, valid_ico()[:-10]):
            with self.subTest(ico=len(bad_ico)):
                with self.assertRaises(ValueError):
                    decode_branding_data_url(data_url(bad_ico, "image/x-icon"), allowed_mimes={"image/x-icon"}, max_bytes=1024)

    def test_mime_base64_and_size_validation(self):
        png = valid_png()
        cases = [
            (data_url(png, "image/svg+xml"), {"image/png"}, 1024),
            (data_url(png, "image/jpeg"), {"image/png"}, 1024),
            ("data:image/png;base64,AAAA!", {"image/png"}, 1024),
            ("data:image/png;charset=utf8;base64," + base64.b64encode(png).decode("ascii"), {"image/png"}, 1024),
            (data_url(png), {"image/png"}, 10),
            ("data:image/png;base64,", {"image/png"}, 1024),
            (data_url(b""), {"image/png"}, 1024),
        ]
        for input_url, types, limit in cases:
            with self.subTest(url=input_url[:55]):
                with self.assertRaises(ValueError):
                    decode_branding_data_url(input_url, allowed_mimes=types, max_bytes=limit)

    def test_branding_update_persists_reloads_and_resets_each_asset(self):
        logo, icon = valid_png(), valid_ico()
        result = self.update(logo_data_url=data_url(logo), favicon_data_url=data_url(icon, "image/x-icon"))
        self.assertEqual(result["app_name"], "Example Signatures")
        self.assertEqual(result["app_color"], "#123456")
        self.assertTrue(result["has_custom_logo"])
        self.assertTrue(result["has_custom_favicon"])
        self.db.close()
        self.db = self.Session()  # new session as after a new worker/reload
        saved = self.db.get(BrandingSettingsDB, 1)
        self.assertEqual(saved.logo_png, logo)
        self.assertEqual(saved.favicon_data, icon)
        self.assertEqual(saved.favicon_mime, "image/x-icon")
        self.assertEqual(branding_context(self.db)["app_name"], "Example Signatures")
        result = self.update(reset_logo=True)
        self.assertFalse(result["has_custom_logo"])
        self.assertTrue(result["has_custom_favicon"])
        result = self.update(app_name="OpenSigWeave", app_color="#ff7043", reset_favicon=True)
        self.assertFalse(result["has_custom_logo"])
        self.assertFalse(result["has_custom_favicon"])
        self.assertIsNone(self.db.get(BrandingSettingsDB, 1).favicon_mime)

    def test_invalid_updates_are_atomic(self):
        self.update(logo_data_url=data_url(valid_png()))
        original = self.db.get(BrandingSettingsDB, 1).logo_png
        for opts in (
            {"logo_data_url": data_url(branding.PNG_SIGNATURE + b"wrong")},
            {"favicon_data_url": data_url(valid_png(), "image/svg+xml")},
            {"logo_data_url": data_url(valid_png()), "reset_logo": True},
            {"favicon_data_url": data_url(valid_ico(), "image/x-icon"), "reset_favicon": True},
            {"app_color": "#xxxzzz"},
            {"app_name": "   "},
            {"app_name": "unsafe\nname"},
        ):
            with self.subTest(opts=str(opts)[:100]):
                with self.assertRaises(HTTPException) as problem:
                    self.update(**opts)
                self.assertEqual(problem.exception.status_code, 422)
                self.db.expire_all()
                self.assertEqual(self.db.get(BrandingSettingsDB, 1).logo_png, original)

    def test_non_admin_cannot_read_or_modify_branding(self):
        with self.assertRaises(HTTPException) as get_denied:
            main.get_admin_branding(self.normal, self.db)
        self.assertEqual(get_denied.exception.status_code, 403)
        with self.assertRaises(HTTPException) as put_denied:
            main.update_admin_branding(main.BrandingUpdateRequest(app_name="X", app_color="#112233"), self.normal, self.db)
        self.assertEqual(put_denied.exception.status_code, 403)

    def test_public_asset_routes_content_types_cache_and_default_fallback(self):
        def db_override():
            yield self.db
        previous = main.app.dependency_overrides.get(get_db)
        main.app.dependency_overrides[get_db] = db_override
        try:
            with TestClient(main.app, base_url="http://signature-test.example.org") as client:
                bundled = client.get("/branding/builtin/logo.png")
                self.assertEqual(bundled.status_code, 200)
                self.assertTrue(bundled.content.startswith(branding.PNG_SIGNATURE))
                self.assertEqual(bundled.headers["content-type"], "image/png")
                self.assertIn("no-cache", bundled.headers["cache-control"])
                self.assertEqual(bundled.headers["x-content-type-options"], "nosniff")
                self.assertEqual(client.get("/branding/logo.png").content, bundled.content)
                self.assertEqual(client.get("/branding/favicon").content, bundled.content)
                logo, ico = valid_png(), valid_ico()
                self.update(logo_data_url=data_url(logo), favicon_data_url=data_url(ico, "image/x-icon"))
                custom_logo = client.get("/branding/logo.png")
                custom_icon = client.get("/branding/favicon.ico")
                self.assertEqual(custom_logo.content, logo)
                self.assertEqual(custom_logo.headers["content-type"], "image/png")
                self.assertEqual(custom_icon.content, ico)
                self.assertEqual(custom_icon.headers["content-type"], "image/x-icon")
                self.assertIn("no-cache", custom_icon.headers["cache-control"])
                self.assertEqual(client.get("/branding/builtin/logo.png").content, bundled.content)
                self.assertEqual(client.get("/branding/builtin/favicon").content, bundled.content)
                self.update(reset_logo=True, reset_favicon=True)
                self.assertEqual(client.get("/branding/logo.png").content, bundled.content)
                self.assertEqual(client.get("/branding/favicon").content, bundled.content)
                self.assertEqual(client.get("/api/admin/branding").status_code, 403)
                self.assertEqual(client.get("/branding/../../etc/passwd").status_code, 404)
        finally:
            if previous is None:
                main.app.dependency_overrides.pop(get_db, None)
            else:
                main.app.dependency_overrides[get_db] = previous

    def test_missing_bundled_assets_return_404_not_an_internal_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent.png"
            with (patch.object(main, "BUILTIN_LOGO_PATH", missing),
                  patch.object(main, "BUILTIN_FAVICON_PATH", missing),
                  patch.object(branding, "BUILTIN_LOGO_PATH", missing),
                  patch.object(branding, "BUILTIN_FAVICON_PATH", missing)):
                self.assertFalse(branding_context(self.db)["has_logo"])
                self.assertFalse(branding_context(self.db)["has_favicon"])
                with self.assertRaises(HTTPException) as logo_error:
                    main.branding_logo(self.db)
                self.assertEqual(logo_error.exception.status_code, 404)
                with self.assertRaises(HTTPException) as icon_error:
                    main.branding_favicon(self.db)
                self.assertEqual(icon_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
