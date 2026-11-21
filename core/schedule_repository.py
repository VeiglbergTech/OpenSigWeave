"""Selection of currently active signature schedules."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from models import SignatureScheduleDB


def load_active_schedule(
    session: Session,
    domain_id: int,
    *,
    now: datetime | None = None,
) -> SignatureScheduleDB | None:
    """Return the highest-precedence active schedule for one domain.

    Windows are half-open: starts_at is inclusive and ends_at is exclusive.
    Domain-specific schedules win a tie against all-domain schedules.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Schedule evaluation time must be timezone-aware")
    current = current.astimezone(timezone.utc)
    return (
        session.query(SignatureScheduleDB)
        .filter(
            SignatureScheduleDB.is_enabled.is_(True),
            SignatureScheduleDB.starts_at <= current,
            SignatureScheduleDB.ends_at > current,
            or_(
                SignatureScheduleDB.domain_id.is_(None),
                SignatureScheduleDB.domain_id == domain_id,
            ),
        )
        .order_by(
            SignatureScheduleDB.priority.desc(),
            case((SignatureScheduleDB.domain_id == domain_id, 0), else_=1),
            SignatureScheduleDB.starts_at.desc(),
            SignatureScheduleDB.id.asc(),
        )
        .first()
    )
