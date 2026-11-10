"""Transactional catalog operations for signature metadata and HTML assets."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.modes import SignatureMode
from models import (
    DomainDB,
    SignatureDB,
    SignatureVariantDB,
    SignatureVersionDB,
)
from signature_storage import SignatureFileStore, StoredSignature


class SignatureNotFoundError(LookupError):
    pass


def _persist_version(
    session: Session,
    store: SignatureFileStore,
    signature_id: str,
    html_content: str,
    created_by: str | None,
    version_number: int,
) -> tuple[SignatureVersionDB, StoredSignature]:
    version_uuid = uuid.uuid4()
    stored = store.write(version_uuid, html_content)
    version = SignatureVersionDB(
        id=version_uuid.hex,
        signature_id=signature_id,
        version_number=version_number,
        relative_path=stored.relative_path,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        created_by=created_by,
    )
    session.add(version)
    return version, stored


def create_signature(
    session: Session,
    store: SignatureFileStore,
    *,
    name: str,
    domain_id: int | None,
    mode: SignatureMode,
    html_content: str,
    weight: int = 1,
    created_by: str | None = None,
) -> tuple[SignatureDB, SignatureVersionDB, SignatureVariantDB]:
    if weight < 1:
        raise ValueError("weight must be positive")
    if domain_id is not None and session.get(DomainDB, domain_id) is None:
        raise ValueError("domain_id does not exist")

    signature_id = uuid.uuid4().hex
    signature = SignatureDB(
        id=signature_id,
        name=name,
        domain_id=domain_id,
        scope="DOMAIN" if domain_id is not None else "GLOBAL",
        mode=mode.value,
        is_active=True,
    )

    stored = None
    try:
        session.add(signature)
        version, stored = _persist_version(
            session, store, signature_id, html_content, created_by, 1
        )
        variant = SignatureVariantDB(
            id=uuid.uuid4().hex,
            signature_id=signature_id,
            pool_name=mode.value,
            weight=weight,
            is_enabled=True,
        )
        session.add(variant)
        session.commit()
        return signature, version, variant
    except Exception:
        session.rollback()
        if stored is not None:
            try:
                store.delete(uuid.UUID(hex=version.id))
            except Exception:
                pass
        raise


def add_signature_version(
    session: Session,
    store: SignatureFileStore,
    signature_id: str,
    html_content: str,
    created_by: str | None = None,
) -> SignatureVersionDB:
    signature = session.execute(
        select(SignatureDB)
        .where(SignatureDB.id == signature_id)
        .with_for_update()
    ).scalar_one_or_none()
    if signature is None:
        raise SignatureNotFoundError(signature_id)

    latest_version = session.scalar(
        select(func.max(SignatureVersionDB.version_number)).where(
            SignatureVersionDB.signature_id == signature_id
        )
    )
    version_number = (latest_version or 0) + 1
    version_uuid = uuid.uuid4()
    stored = None
    try:
        stored = store.write(version_uuid, html_content)
        version = SignatureVersionDB(
            id=version_uuid.hex,
            signature_id=signature_id,
            version_number=version_number,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            created_by=created_by,
        )
        session.add(version)
        session.commit()
        return version
    except Exception:
        session.rollback()
        if stored is not None:
            try:
                store.delete(version_uuid)
            except Exception:
                pass
        raise


def add_signature_variant(
    session: Session,
    signature_id: str,
    *,
    pool_name: SignatureMode,
    weight: int = 1,
) -> SignatureVariantDB:
    if weight < 1:
        raise ValueError("weight must be positive")
    signature = session.get(SignatureDB, signature_id)
    if signature is None:
        raise SignatureNotFoundError(signature_id)
    if signature.mode != pool_name.value:
        raise ValueError("variant pool must match the signature mode")

    variant = SignatureVariantDB(
        id=uuid.uuid4().hex,
        signature_id=signature_id,
        pool_name=pool_name.value,
        weight=weight,
        is_enabled=True,
    )
    session.add(variant)
    session.commit()
    return variant
