#!/usr/bin/env python3
"""Create actual signature/schedule/rotation fixtures in an EMPTY disposable DB."""
import argparse
from datetime import datetime, timedelta, timezone
import os
import uuid
from database import SessionLocal
from models import (DomainDB, IdentityCacheDB, IdentityProviderDB, SignatureDB,
                    SignatureGroupDB, SignatureGroupMemberDB, SignatureScheduleDB,
                    SignatureVersionDB)
from signature_storage import SignatureFileStore


def seed(senders):
    if os.environ.get("BENCHMARK_DISPOSABLE_DB") != "1":
        raise RuntimeError("BENCHMARK_DISPOSABLE_DB=1 required on a disposable instance")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise RuntimeError("Only disposable PostgreSQL test databases are supported")
    if not 1 <= senders <= 2000:
        raise ValueError("Invalid sender count")
    store = SignatureFileStore(os.environ["SIGNATURE_STORAGE_PATH"])
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if (db.query(DomainDB).first() or db.query(SignatureDB).first()
                or db.query(IdentityCacheDB).first()):
            raise RuntimeError("Refusing to seed a database containing real data")
        provider = IdentityProviderDB(
            id="benchmark-oidc", display_name="Benchmark OIDC cache",
            provider_type="oidc", is_enabled=True)
        db.add(provider)
        db.flush()
        for mode in ("static", "rotation", "large"):
            domain_name = "bench-" + mode + ".example.test"
            domain = DomainDB(domain_name=domain_name, is_active=True,
                              identity_provider_id=provider.id)
            db.add(domain)
            db.flush()
            group = None
            if mode == "rotation":
                group = SignatureGroupDB(id=uuid.uuid4().hex, slug="benchmark-rotation",
                                         name="Benchmark rotation", is_active=True)
                db.add(group)
                db.flush()
            signature_ids = []
            for number in range(3 if group else 1):
                signature_id = uuid.uuid4().hex
                version_id = uuid.uuid4()
                name = "Benchmark-" + mode + "-" + str(number)
                template = ("<p>BENCHMARK_SIGNATURE " + mode + " " + str(number)
                            + " {{ first_name }} {{ last_name }}</p>")
                if mode == "large":
                    template += "<p>" + "X" * 32000 + "</p>
                db.add(SignatureDB(id=signature_id, name=name, domain_id=domain.id,
                                   scope="DOMAIN", mode="UNUSED", is_active=True))
                db.flush()
                stored = store.write(version_id, name, template)
                db.add(SignatureVersionDB(
                    id=version_id.hex, signature_id=signature_id, version_number=1,
                    relative_path=stored.relative_path, sha256=stored.sha256,
                    size_bytes=stored.size_bytes))
                signature_ids.append(signature_id)
                if group:
                    db.add(SignatureGroupMemberDB(
                        group_id=group.id, signature_id=signature_id,
                        weight=1, is_enabled=True))
            db.add(SignatureScheduleDB(
                id=uuid.uuid4().hex, name="Benchmark " + mode, domain_id=domain.id,
                target_type="GROUP" if group else "SIGNATURE",
                group_id=group.id if group else None,
                signature_id=None if group else signature_ids[0],
                starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=2),
                priority=1000, is_enabled=True))
            for index in range(senders):
                ident = "user" + str(index).zfill(4)
                db.add(IdentityCacheDB(
                    email=provider.id + ":" + ident + "@" + domain_name,
                    principal_id=provider.id + ":" + ident,
                    first_name="Benchmark", last_name="User", title="Test",
                    phone="", groups=[], claims={}, attributes={}, updated_at=now))
        db.commit()
    print("Seeded fixed, rotating and large fixtures; senders per domain:", senders)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-disposable", action="store_true", required=True)
    parser.add_argument("--senders", type=int, default=512)
    seed(parser.parse_args().senders)
