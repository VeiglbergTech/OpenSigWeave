"""Calendar scheduling API, database, real engine, and Rspamd HTTP-path regressions."""
import asyncio
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-with-more-than-thirty-two-characters")
os.environ.setdefault("ENGINE_API_KEY", "test-engine-api-key-with-more-than-thirty-two-characters")
os.environ.setdefault("APP_ALLOWED_HOSTS", "signature-test.example.org,app.example.org")
from auth_config import hash_local_admin_password
os.environ.setdefault("LOCAL_ADMIN_EMAIL", "admin@example.org")
os.environ.setdefault("LOCAL_ADMIN_PASSWORD_HASH", hash_local_admin_password("correct horse battery staple"))

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.responses import Response

import main
from core.engine_contract import normalize_engine_request
from core.engine_service import SignatureEngineService
from core.schedule_repository import load_active_schedule
from database import Base
from identity.attributes import IdentityAttributes
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureDomainDB,
    SignatureGroupDB, SignatureGroupMemberDB, SignatureScheduleDB,
    SignatureVersionDB, UserOverrideDB,
)


UTC = timezone.utc
START = datetime(2026, 11, 30, 23, 0, tzinfo=UTC)  # Dec 1, 00:00 Europe/Berlin
END = datetime(2026, 12, 26, 23, 0, tzinfo=UTC)  # Dec 27, 00:00 Europe/Berlin


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opensigweave-scheduling-")
        self.url = "sqlite:///" + str(Path(self.tmp.name) / "schedules.db")
        self.engine = create_engine(self.url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.admin = SimpleNamespace(session={"user": {"groups": ["signature-admins"]}})
        self.viewer = SimpleNamespace(session={"user": {"groups": ["signature-users"]}})
        self.domain = DomainDB(
            domain_name="example.org", is_active=True,
            allow_overrides=True, template_html="<p>Normal signature</p>",
        )
        self.db.add(self.domain)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def add_signature(self, name="Christmas", html=None):
        sig = SignatureDB(
            id=uuid.uuid4().hex, name=name,
            domain_id=None, scope="GLOBAL", mode="UNUSED", is_active=True,
        )
        self.db.add(sig)
        self.db.add(SignatureVersionDB(
            id=uuid.uuid4().hex, signature_id=sig.id, version_number=1,
            relative_path="versions/" + sig.id + "/signature.html",
            sha256="a" * 64, size_bytes=len(html or name),
        ))
        self.db.commit()
        return sig

    def payload(self, sig, **changes):
        data = dict(
            name="Christmas 2026", target_type="SIGNATURE",
            signature_id=sig.id, group_id=None, domain_id=self.domain.id,
            starts_at="2026-12-01T00:00:00+01:00",
            ends_at="2026-12-27T00:00:00+01:00",
            priority=10, is_enabled=True,
        )
        data.update(changes)
        return main.SignatureScheduleRequest(**data)

    def insert_schedule(self, *, priority=10, domain_id="current",
                        starts=START, ends=END, name="Christmas",
                        signature_id=None, group_id=None, enabled=True, sid=None):
        item = SignatureScheduleDB(
            id=sid or uuid.uuid4().hex, name=name,
            domain_id=self.domain.id if domain_id == "current" else domain_id,
            target_type="GROUP" if group_id else "SIGNATURE",
            signature_id=signature_id, group_id=group_id, starts_at=starts,
            ends_at=ends, priority=priority, is_enabled=enabled,
        )
        self.db.add(item)
        self.db.commit()
        return item

    def test_api_timezone_validation_create_update_delete_and_reconnect(self):
        sig = self.add_signature()
        created = main.create_signature_schedule(self.payload(sig), self.admin, self.db)
        self.assertEqual(created["starts_at"], START.isoformat())
        self.assertEqual(created["ends_at"], END.isoformat())
        self.assertEqual(created["priority"], 10)
        self.assertEqual(len(main.list_signature_schedules(self.admin, self.db)), 1)

        updated = main.update_signature_schedule(
            created["id"], self.payload(sig, priority=42, name="December Update"),
            self.admin, self.db,
        )
        self.assertEqual(updated["priority"], 42)
        self.assertEqual(updated["name"], "December Update")

        # A fresh SQLAlchemy connection/engine on the same disk database models a restart.
        self.db.close()
        self.engine.dispose()
        self.engine = create_engine(self.url)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        persisted = main.list_signature_schedules(self.admin, self.db)
        self.assertEqual([item["id"] for item in persisted], [created["id"]])
        self.assertEqual(persisted[0]["priority"], 42)
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, created["id"])

        self.assertEqual(main.delete_signature_schedule(created["id"], self.admin, self.db),
                         {"status": "deleted"})
        self.assertEqual(main.list_signature_schedules(self.admin, self.db), [])
        self.assertIsNone(load_active_schedule(self.db, self.domain.id, now=START))
        with self.assertRaises(HTTPException) as missing:
            main.delete_signature_schedule(created["id"], self.admin, self.db)
        self.assertEqual(missing.exception.status_code, 404)

    def test_invalid_windows_targets_names_and_permissions(self):
        sig = self.add_signature()
        second_domain = DomainDB(domain_name="different.org", is_active=True)
        self.db.add(second_domain)
        self.db.commit()
        self.db.add(SignatureDomainDB(signature_id=sig.id, domain_id=self.domain.id))
        self.db.commit()

        invalid = (
            {"starts_at": "2026-12-01T00:00:00", "ends_at": "2026-12-27T00:00:00+01:00"},
            {"ends_at": "2026-12-27T00:00:00"},
            {"ends_at": "2026-12-01T00:00:00+01:00"},
            {"ends_at": "2026-11-30T23:59:00+01:00"},
            {"signature_id": None},
            {"group_id": "invalid-group"},
            {"domain_id": second_domain.id},
            {"domain_id": None},
            {"name": " \t  "},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as caught:
                main.create_signature_schedule(self.payload(sig, **kwargs), self.admin, self.db)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(main.list_signature_schedules(self.admin, self.db), [])

        with self.assertRaises(HTTPException) as denied:
            main.create_signature_schedule(self.payload(sig), self.viewer, self.db)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HTTPException) as denied:
            main.list_signature_schedules(self.viewer, self.db)
        self.assertEqual(denied.exception.status_code, 403)

        created = main.create_signature_schedule(self.payload(sig), self.admin, self.db)
        with self.assertRaises(HTTPException) as denied:
            main.update_signature_schedule(created["id"], self.payload(sig), self.viewer, self.db)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HTTPException) as denied:
            main.delete_signature_schedule(created["id"], self.viewer, self.db)
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HTTPException) as blank:
            main.update_signature_schedule(
                created["id"], self.payload(sig, name="  "), self.admin, self.db
            )
        self.assertEqual(blank.exception.status_code, 422)
        self.assertEqual(main.list_signature_schedules(self.admin, self.db)[0]["name"], "Christmas 2026")

    def test_half_open_window_priority_domain_specific_and_stable_ties(self):
        sig = self.add_signature()
        global_entry = self.insert_schedule(
            name="all domains", signature_id=sig.id, priority=20, domain_id=None, sid="a" * 32,
        )
        local = self.insert_schedule(
            name="local", signature_id=sig.id, priority=20, sid="z" * 32,
        )
        other = self.insert_schedule(
            name="other", signature_id=sig.id, priority=100, sid="b" * 32,
            domain_id=999,
        )
        self.assertIsNone(load_active_schedule(self.db, self.domain.id, now=START-timedelta(microseconds=1)))
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, local.id)
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=END-timedelta(microseconds=1)).id, local.id)
        self.assertIsNone(load_active_schedule(self.db, self.domain.id, now=END))
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=END+timedelta(days=1)), None)
        local.is_enabled = False
        self.db.commit()
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, global_entry.id)
        later = self.insert_schedule(
            name="later", signature_id=sig.id, priority=20, domain_id=None,
            starts=START+timedelta(minutes=1), sid="f" * 32,
        )
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, global_entry.id)
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START+timedelta(minutes=1)).id, later.id)
        dominant = self.insert_schedule(
            name="dominant", signature_id=sig.id, priority=21, sid="c" * 32,
        )
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, dominant.id)
        dominant.is_enabled = False
        later.is_enabled = False
        self.db.commit()
        same = self.insert_schedule(
            name="same", signature_id=sig.id, priority=20, domain_id=None, sid="0" * 32,
        )
        self.assertEqual(load_active_schedule(self.db, self.domain.id, now=START).id, same.id)
        with self.assertRaises(ValueError):
            load_active_schedule(self.db, self.domain.id, now=datetime(2026, 12, 1))

    def test_real_engine_rspamd_alias_uses_schedule_only_during_window(self):
        sig = self.add_signature("Christmas")
        self.insert_schedule(signature_id=sig.id, priority=10)
        self.db.add(UserOverrideDB(
            user_email="ada@example.org", html_content="<p>Personal default</p>", is_disabled=False,
        ))
        self.db.commit()
        attributes = IdentityAttributes(principal_id="idp:ada", first_name="Ada")
        store = Mock()
        store.read.return_value = "<p>Christmas signature</p>"
        moments = [
            (START-timedelta(seconds=1), "<p>Personal default</p>"),
            (START, "<p>Christmas signature</p>"),
            (END-timedelta(seconds=1), "<p>Christmas signature</p>"),
            (END, "<p>Personal default</p>"),
        ]
        with (
            patch("core.schedule_repository.datetime") as clock,
            patch.object(main, "get_identity_attributes", new=AsyncMock(return_value=attributes)),
            patch.object(main, "signature_file_store", store),
            patch.object(main, "ENGINE_API_KEY", "valid-engine-key"),
        ):
            for instant, expected in moments:
                with self.subTest(instant=instant):
                    clock.now.return_value = instant
                    response = Response()
                    result = asyncio.run(main.get_engine_signature(
                        "ada@example.org", response, "valid-engine-key", None, None, self.db
                    ))
                    self.assertEqual(result["html"], expected)
                    self.assertEqual(response.headers["cache-control"], "no-store, private")
            store.read.assert_called()
        # Rspamd's legacy endpoint is an alias of the identical live service, not
        # a separately cached implementation.
        routes = {route.path: route.endpoint for route in main.app.routes
                  if getattr(route, "path", None)}
        self.assertIs(routes["/api/signature/{email}"],
                      routes["/api/v1/engine/signatures/{email}"])
        rspamd_lua = (Path(__file__).resolve().parents[1] /
                      "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn("API_URL_BASE .. url_encode(sender_email)", rspamd_lua)
        self.assertIn("rspamd_http.request({", rspamd_lua)

    def test_scheduled_group_uses_rotation_and_end_restores_normal(self):
        sig = self.add_signature("Group Christmas")
        group = SignatureGroupDB(
            id=uuid.uuid4().hex, name="Christmas group",
            slug="christmas-group", is_active=True,
        )
        self.db.add(group)
        self.db.add(SignatureGroupMemberDB(
            group_id=group.id, signature_id=sig.id, weight=1, is_enabled=True,
        ))
        self.db.commit()
        main.create_signature_schedule(
            self.payload(
                sig, target_type="GROUP", signature_id=None, group_id=group.id
            ), self.admin, self.db,
        )
        resolver = AsyncMock(return_value=IdentityAttributes(
            principal_id="idp:ada", first_name="Ada",
        ))
        store = Mock()
        store.read.return_value = "<p>Group Christmas</p>"
        service = SignatureEngineService(resolver, store)
        with patch("core.schedule_repository.datetime") as clock:
            clock.now.return_value = START
            current = asyncio.run(service.resolve(
                self.db, normalize_engine_request("ada@example.org")
            ))
            self.assertEqual(current.html, "<p>Group Christmas</p>")
            self.assertEqual(self.db.query(GroupSelectionStateDB).one().group_id, group.id)
            clock.now.return_value = END
            expired = asyncio.run(service.resolve(
                self.db, normalize_engine_request("ada@example.org")
            ))
            self.assertEqual(expired.html, "<p>Normal signature</p>")

if __name__ == "__main__":
    unittest.main()
