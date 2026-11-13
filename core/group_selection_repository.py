"""Transactional signature selection from named groups."""
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.variant_selector import SignatureVariant, select_variant
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
    dialect = session.get_bind().dialect.name
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

    eligible_rows = session.execute(candidates_query.with_for_update()).all()
    candidates = [
        SignatureVariant(id=signature.id, payload=signature, enabled=True, weight=weight)
        for signature, weight in eligible_rows
    ]
    selected = select_variant(candidates, state.last_signature_id)
    if selected is None:
        return None
    state.last_signature_id = selected.id
    session.flush()
    return selected.payload
