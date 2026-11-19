"""Tests for persistent branding validation."""
import base64
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.branding import DEFAULT_APP_COLOR, DEFAULT_APP_NAME, branding_context, decode_branding_data_url
from database import Base
from models import BrandingSettingsDB


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

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_defaults_are_available_without_database_row(self):
        result = branding_context(self.db)
        self.assertEqual(result["app_name"], DEFAULT_APP_NAME)
        self.assertEqual(result["app_color"], DEFAULT_APP_COLOR)
        self.assertFalse(result["has_custom_logo"])
        self.assertFalse(result["has_custom_favicon"])

    def test_database_branding_overrides_name_color_and_assets(self):
        self.db.add(BrandingSettingsDB(
            id=1,
            app_name="Example Signatures",
            app_color="#123456",
            logo_png=b"png",
            favicon_data=b"ico",
            favicon_mime="image/x-icon",
        ))
        self.db.commit()
        result = branding_context(self.db)
        self.assertEqual(result["app_name"], "Example Signatures")
        self.assertEqual(result["app_color"], "#123456")
        self.assertTrue(result["has_custom_logo"])
        self.assertTrue(result["has_custom_favicon"])

    def test_png_data_url_is_validated(self):
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        decoded, mime = decode_branding_data_url(
            data_url, allowed_mimes={"image/png"}, max_bytes=1024
        )
        self.assertEqual(decoded, png)
        self.assertEqual(mime, "image/png")

    def test_invalid_png_signature_is_rejected(self):
        data_url = "data:image/png;base64," + base64.b64encode(b"not png").decode("ascii")
        with self.assertRaises(ValueError):
            decode_branding_data_url(
                data_url, allowed_mimes={"image/png"}, max_bytes=1024
            )


if __name__ == "__main__":
    unittest.main()
