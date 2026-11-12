"""Transactional per-sender variant selection state repository."""
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.variant_selector import SignatureVariant, select_variant
from models import SelectionStateDB, SignatureDB, SignatureDomainDB, SignatureVariantDB


def select_and_record_variant(
    session: Session,
    sender_email: str,
    pool_name: str,
    domain_id: int | None = None,
    signature_id: str | None = None,
) -> SignatureVariantDB | None:
    """Select and record a variant in the caller's transaction.

    PostgreSQL row locking serializes concurrent messages for the same sender
    and pool. SQLite uses its write transaction as a development/test fallback.
    The caller must commit or roll back the session after this function returns.
    """
    normalized_sender = sender_email.strip().casefold()
    normalized_pool = pool_name.strip().upper()
    if not normalized_sender or not normalized_pool:
        raise ValueError("sender_email and pool_name are required")

    domain_scope = (
        ~select(SignatureDomainDB.signature_id).where(SignatureDomainDB.signature_id == SignatureDB.id).exists()
        if domain_id is None
        else or_(
            ~select(SignatureDomainDB.signature_id).where(SignatureDomainDB.signature_id == SignatureDB.id).exists(),
            select(SignatureDomainDB.signature_id).where(
                SignatureDomainDB.signature_id == SignatureDB.id,
                SignatureDomainDB.domain_id == domain_id,
            ).exists(),
        )
    )
    candidate_query = (
        select(SignatureVariantDB)
        .join(SignatureDB, SignatureVariantDB.signature_id == SignatureDB.id)
        .where(
            SignatureVariantDB.pool_name == normalized_pool,
            SignatureVariantDB.is_enabled.is_(True),
            SignatureVariantDB.weight > 0,
            SignatureDB.is_active.is_(True),
            SignatureDB.mode == normalized_pool,
            domain_scope,
            *([SignatureDB.id == signature_id] if signature_id is not None else []),
        )
    )
    if session.execute(candidate_query.limit(1)).first() is None:
        return None

    dialect = session.get_bind().dialect.name
    values = {
        "sender_email": normalized_sender,
        "pool_name": normalized_pool,
        "last_variant_id": None,
    }
    if dialect == "postgresql":
        statement = postgresql_insert(SelectionStateDB).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(SelectionStateDB).values(**values)
    else:
        raise RuntimeError(f"Unsupported selection-state database dialect: {dialect}")

    statement = statement.on_conflict_do_nothing(
        index_elements=[SelectionStateDB.sender_email, SelectionStateDB.pool_name]
    )
    session.execute(statement)

    state = session.execute(
        select(SelectionStateDB)
        .where(
            SelectionStateDB.sender_email == normalized_sender,
            SelectionStateDB.pool_name == normalized_pool,
        )
        .with_for_update()
    ).scalar_one()

    eligible = session.execute(candidate_query.order_by(SignatureVariantDB.id).with_for_update()).scalars().all()
    if not eligible:
        return None

    candidates = [
        SignatureVariant(
            id=variant.id,
            payload=variant,
            enabled=True,
            weight=variant.weight,
        )
        for variant in eligible
    ]
    selected = select_variant(candidates, state.last_variant_id)
    if selected is None:
        return None

    state.last_variant_id = selected.id
    session.flush()
    return selected.payload
