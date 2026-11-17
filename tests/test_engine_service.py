"""Direct tests for the framework-independent signature engine service."""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.engine_contract import normalize_engine_request
from core.engine_service import SignatureEngineService
from database import Base
from identity.attributes import IdentityAttributes
from models import DomainDB


class SignatureEngineServiceTests(unittest.TestCase):
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

    def test_service_renders_default_domain_template_with_injected_dependencies(self):
        domain = DomainDB(
            domain_name="example.org",
            is_active=True,
            inject_on_replies=True,
            trim_whitespace=False,
            strip_device_signatures=True,
            template_html="<p>Hello {{ first_name }} {{ last_name }}</p>",
        )
        self.db.add(domain)
        self.db.commit()

        identity = IdentityAttributes(
            principal_id="principal-1",
            first_name="Ada",
            last_name="Lovelace",
        )
        identity_resolver = AsyncMock(return_value=identity)
        file_store = Mock()
        service = SignatureEngineService(identity_resolver, file_store)

        result = asyncio.run(
            service.resolve(
                self.db,
                normalize_engine_request("Ada@Example.org"),
            )
        )

        self.assertEqual(result.html, "<p>Hello Ada Lovelace</p>")
        self.assertTrue(result.inject_on_replies)
        self.assertFalse(result.trim_whitespace)
        self.assertTrue(result.strip_device_signatures)
        identity_resolver.assert_awaited_once_with(self.db, "ada@example.org", None)
        file_store.read.assert_not_called()

    def test_service_module_has_no_http_transport_dependency(self):
        source = (
            Path(__file__).resolve().parents[1] / "core" / "engine_service.py"
        ).read_text(encoding="utf-8")

        self.assertNotRegex(source, r"(?m)^\s*(?:from|import)\s+fastapi")
        self.assertNotIn("HTTPException", source)
        self.assertNotIn("Response", source)
        # Match the standalone HTTP Request symbol, not SignatureEngineRequest.
        self.assertNotRegex(source, r"\bRequest\b")
        self.assertNotIn("os.environ", source)
        self.assertNotIn("os.getenv", source)


if __name__ == "__main__":
    unittest.main()
