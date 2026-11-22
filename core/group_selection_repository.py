"""Transactional signature selection from named groups."""
import os
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.weighted_selector import WeightedCandidate, select_weighted_no_repeat
from models import (
    DomainDB, GroupSelectionStateDB, SignatureDB, SignatureDomainDB,
    SignatureGroupDB, SignatureGroupDomainDB, SignatureGroupMemberDB,
)


def select_and_record_group_signature(
    session: Session, principal_id: str, group_id: str, domain_id: int
) -> SignatureDB | None:
    """Select one active group member and persist no-repeat state transactionally.

    The caller owns commit/rollback. Principal IDs must be immutable and
    provider-namespaced; email addresses are deliberately rejected.
    """
    principal = (principal_id or "").strip()
    if not principal or len(principal) > 320 or "@" in principal:
        raise ValueError("A provider-namespaced immutable principal_id is required")

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        # Keep a single stalled principal from holding DB sessions forever.
        # SET LOCAL resets automatically when the caller commits/rolls back.
        raw = os.environ.get("DB_SELECTION_LOCK_TIMEOUT_MS", "8000")
        try:
            lock_timeout_ms = int(raw)
        except ValueError as exc:
            raise RuntimeError("DB_SELECTION_LOCK_TIMEOUT_MS must be an integer") from exc
        if not 100 <= lock_timeout_ms <= 60000:
            raise RuntimeError("DB_SELECTION_LOCK_TIMEOUT_MS must be 100..60000")
        session.execute(
            text("SELECT set_config('lock_timeout', :limit, true)"),
            {"limit": f"{lock_timeout_ms}ms"},
        )

    group = session.get(SignatureGroupDB, group_id)
    domain = session.get(DomainDB, domain_id)
    if group is None or not group.is_active or domain is None or not domain.is_active:
        return None

    group_domains = select(SignatureGroupDomainDB.group_id).where(
        SignatureGroupDomainDB.group_id == group_id
    )
    group_scope = or_(
        ~group_domains.exists(),
        select(SignatureGroupDomainDB.group_id).where(
            SignatureGroupDomainDB.group_id == group_id,
            SignatureGroupDomainDB.domain_id == domain_id,
        ).exists(),
    )
    signature_scope = or_(
        ~select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.signature_id == SignatureDB.id
        ).exists(),
        select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.signature_id == SignatureDB.id,
            SignatureDomainDB.domain_id == domain_id,
        ).exists(),
    )
    candidates_query = (
        select(SignatureDB, SignatureGroupMemberDB.weight)
        .join(SignatureGroupMemberDB, SignatureGroupMemberDB.signature_id == SignatureDB.id)
        .where(
            SignatureGroupMemberDB.group_id == group_id,
            SignatureGroupMemberDB.is_enabled.is_(True),
            SignatureGroupMemberDB.weight > 0,
            SignatureDB.is_active.is_(True),
            group_scope,
            signature_scope,
        )
        .order_by(SignatureDB.id)
    )
    if session.execute(candidates_query.limit(1)).first() is None:
        return None

    values = {
        "principal_id": principal,
        "group_id": group_id,
        "last_signature_id": None,
    }
    if dialect == "postgresql":
        statement = postgresql_insert(GroupSelectionStateDB).values(**values)
    elif dialect == "sqlite":
        statement = sqlite_insert(GroupSelectionStateDB).values(**values)
    else:
        raise RuntimeError(f"Unsupported selection-state database dialect: {dialect}")
    session.execute(statement.on_conflict_do_nothing(
        index_elements=[GroupSelectionStateDB.principal_id, GroupSelectionStateDB.group_id]
    ))
    state = session.execute(
        select(GroupSelectionStateDB).where(
            GroupSelectionStateDB.principal_id == principal,
            GroupSelectionStateDB.group_id == group_id,
        ).with_for_update()
    ).scalar_one()

    # Only the (principal_id, group_id) rotation state needs an exclusive lock.
    # Locking catalog/member rows here serializes every sender in the same
    # group, including unrelated principals, and causes head-of-line blocking.
    # Read eligible members AFTER locking this principal's state so the
    # candidate set is current within PostgreSQL READ COMMITTED isolation.
    eligible_rows = session.execute(candidates_query).all()
    candidates = [
        WeightedCandidate(id=signature.id, payload=signature, enabled=True, weight=weight)
        for signature, weight in eligible_rows
    ]
    selected = select_weighted_no_repeat(candidates, state.last_signature_id)
    if selected is None:
        return None
    state.last_signature_id = selected.id
    session.flush()
    return selected.payload


def get_group_preview_signature(
    session: Session, group_id: str, domain_id: int
) -> SignatureDB | None:
    """Return a stable preview member without reading or writing rotation state.

    Preview deliberately uses the first eligible signature ID. It is not a
    production selection and never creates/updates GroupSelectionStateDB rows.
    """
    group = session.get(SignatureGroupDB, group_id)
    domain = session.get(DomainDB, domain_id)
    if group is None or not group.is_active or domain is None or not domain.is_active:
        return None

    group_has_domains = select(SignatureGroupDomainDB.group_id).where(
        SignatureGroupDomainDB.group_id == group_id
    )
    group_scope = or_(
        ~group_has_domains.exists(),
        select(SignatureGroupDomainDB.group_id).where(
            SignatureGroupDomainDB.group_id == group_id,
            SignatureGroupDomainDB.domain_id == domain_id,
        ).exists(),
    )
    signature_scope = or_(
        ~select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.signature_id == SignatureDB.id
        ).exists(),
        select(SignatureDomainDB.signature_id).where(
            SignatureDomainDB.signature_id == SignatureDB.id,
            SignatureDomainDB.domain_id == domain_id,
        ).exists(),
    )
    return session.execute(
        select(SignatureDB)
        .join(SignatureGroupMemberDB, SignatureGroupMemberDB.signature_id == SignatureDB.id)
        .where(
            SignatureGroupMemberDB.group_id == group_id,
            SignatureGroupMemberDB.is_enabled.is_(True),
            SignatureGroupMemberDB.weight > 0,
            SignatureDB.is_active.is_(True),
            group_scope,
            signature_scope,
        )
        .order_by(SignatureDB.id)
        .limit(1)
    ).scalar_one_or_none()
