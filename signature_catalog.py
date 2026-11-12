"""Transactional catalog operations for signature metadata and HTML assets."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.modes import SignatureMode
from models import (
    DomainDB,
    SignatureDB,
    SignatureDomainDB,
    SignatureVariantDB,
    SignatureVersionDB,
)
from signature_storage import SignatureFileStore, SignatureStorageError, StoredSignature
from signature_sanitizer import sanitize_signature_html


class SignatureNotFoundError(LookupError):
    pass


def _persist_version(
    session: Session,
    store: SignatureFileStore,
    signature_id: str,
    signature_name: str,
    html_content: str,
    created_by: str | None,
    version_number: int,
) -> tuple[SignatureVersionDB, StoredSignature]:
    version_uuid = uuid.uuid4()
    sanitized_html = sanitize_signature_html(html_content)
    stored = store.write(version_uuid, signature_name, sanitized_html)
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
    domain_id: int | None = None,
    domain_ids: list[int] | None = None,
    mode: SignatureMode,
    html_content: str,
    weight: int = 1,
    is_active: bool = True,
    created_by: str | None = None,
) -> tuple[SignatureDB, SignatureVersionDB, SignatureVariantDB]:
    if not name.strip():
        raise ValueError("name must not be empty")
    if weight < 1:
        raise ValueError("weight must be positive")
    assigned_domain_ids = sorted(set(domain_ids if domain_ids is not None else ([domain_id] if domain_id is not None else [])))
    if assigned_domain_ids and any(session.get(DomainDB, item) is None for item in assigned_domain_ids):
        raise ValueError("One or more domain_ids do not exist")

    existing_names = session.execute(select(SignatureDB.name)).scalars().all()
    if any(existing.casefold() == name.strip().casefold() for existing in existing_names):
        raise ValueError("A signature with this name already exists")

    signature_id = uuid.uuid4().hex
    signature = SignatureDB(
        id=signature_id,
        name=name.strip(),
        domain_id=assigned_domain_ids[0] if len(assigned_domain_ids) == 1 else None,
        scope="DOMAIN" if assigned_domain_ids else "GLOBAL",
        mode=mode.value,
        is_active=is_active,
    )

    stored = None
    try:
        store.ensure_name_available(name.strip())
    except SignatureStorageError as exc:
        raise ValueError(str(exc)) from exc
    try:
        session.add(signature)
        session.flush()
        for assigned_domain_id in assigned_domain_ids:
            session.add(SignatureDomainDB(signature_id=signature_id, domain_id=assigned_domain_id))
        version, stored = _persist_version(
            session, store, signature_id, name.strip(), html_content, created_by, 1
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
                store.delete(name, uuid.UUID(hex=version.id))
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

    latest_version_number = session.scalar(
        select(func.max(SignatureVersionDB.version_number)).where(
            SignatureVersionDB.signature_id == signature_id
        )
    )
    latest_version = session.execute(
        select(SignatureVersionDB)
        .where(SignatureVersionDB.signature_id == signature_id)
        .order_by(SignatureVersionDB.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    version_number = (latest_version_number or 0) + 1
    version_uuid = uuid.uuid4()
    stored = None
    try:
        sanitized_html = sanitize_signature_html(html_content)
        stored = store.write(version_uuid, signature.name, sanitized_html)
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
                store.delete(signature.name, version_uuid)
                if latest_version is not None:
                    store.restore_current(signature.name, uuid.UUID(hex=latest_version.id), latest_version.sha256)
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
    try:
        session.add(variant)
        session.commit()
        return variant
    except Exception:
        session.rollback()
        raise


def update_signature(
    session: Session,
    store: SignatureFileStore,
    signature_id: str,
    *,
    name: str,
    domain_id: int | None = None,
    domain_ids: list[int] | None = None,
    is_active: bool,
) -> SignatureDB:
    """Update mutable catalog metadata without changing a signature's mode."""
    signature = session.get(SignatureDB, signature_id)
    if signature is None:
        raise SignatureNotFoundError(signature_id)
    if not name.strip():
        raise ValueError("name must not be empty")
    assigned_domain_ids = sorted(set(domain_ids if domain_ids is not None else ([domain_id] if domain_id is not None else [])))
    if assigned_domain_ids and any(session.get(DomainDB, item) is None for item in assigned_domain_ids):
        raise ValueError("One or more domain_ids do not exist")
    old_name = signature.name
    new_name = name.strip()
    existing_names = session.execute(
        select(SignatureDB.name).where(SignatureDB.id != signature_id)
    ).scalars().all()
    if any(existing.casefold() == new_name.casefold() for existing in existing_names):
        raise ValueError("A signature with this name already exists")
    renamed = old_name != new_name
    if renamed:
        try:
            store.rename_signature(old_name, new_name)
        except SignatureStorageError as exc:
            raise ValueError(str(exc)) from exc
        versions = session.execute(
            select(SignatureVersionDB).where(SignatureVersionDB.signature_id == signature_id)
        ).scalars().all()
        for version in versions:
            version.relative_path = version.relative_path.replace(
                f"signatur/{old_name}/", f"signatur/{new_name}/", 1
            )
    signature.name = new_name
    signature.domain_id = assigned_domain_ids[0] if len(assigned_domain_ids) == 1 else None
    signature.scope = "DOMAIN" if assigned_domain_ids else "GLOBAL"
    session.query(SignatureDomainDB).filter(SignatureDomainDB.signature_id == signature_id).delete(synchronize_session=False)
    for assigned_domain_id in assigned_domain_ids:
        session.add(SignatureDomainDB(signature_id=signature_id, domain_id=assigned_domain_id))
    signature.is_active = bool(is_active)
    try:
        session.commit()
        return signature
    except Exception:
        session.rollback()
        if renamed:
            try:
                store.rename_signature(new_name, old_name)
            except Exception:
                pass
        raise


def update_signature_variant(
    session: Session,
    variant_id: str,
    *,
    weight: int,
    is_enabled: bool,
) -> SignatureVariantDB:
    """Update a variant's weight and availability."""
    if weight < 1:
        raise ValueError("weight must be positive")
    variant = session.get(SignatureVariantDB, variant_id)
    if variant is None:
        raise SignatureNotFoundError(variant_id)
    variant.weight = weight
    variant.is_enabled = bool(is_enabled)
    try:
        session.commit()
        return variant
    except Exception:
        session.rollback()
        raise
