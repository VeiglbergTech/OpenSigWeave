"""Delete catalog signatures with explicit reference cleanup and staged filesystem removal.

Relational references are cleaned in one DB transaction. Signature HTML snapshots
are atomically moved out of the active tree before commit and restored on failure.
Shared CID/image assets are deliberately not deleted: other signatures may use them.
"""
import hashlib
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    DomainDB, GroupSelectionStateDB, SelectionStateDB, SignatureDB,
    SignatureDomainDB, SignatureGroupDB, SignatureGroupMemberDB,
    SignatureRuleDB, SignatureScheduleDB, SignatureVariantDB,
    SignatureVersionDB, UserOverrideDB,
)
from signature_catalog import SignatureNotFoundError
from signature_storage import SignatureFileStore, SignatureStorageError

logger = logging.getLogger(__name__)


def _marker_pattern(name: str) -> re.Pattern:
    code = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:4]
    return re.compile(re.escape(f"<---[{name}]--[{code}]--->"), re.IGNORECASE)


def inspect_signature_deletion(db: Session, signature_id: str) -> dict:
    """Expose the actual effects of deleting a signature before confirmation."""
    signature = db.get(SignatureDB, signature_id)
    if signature is None:
        raise SignatureNotFoundError(signature_id)

    memberships = db.query(SignatureGroupMemberDB).filter(
        SignatureGroupMemberDB.signature_id == signature_id
    ).all()
    groups = []
    disabled_group_ids = set()
    for member in memberships:
        group = db.get(SignatureGroupDB, member.group_id)
        if group is None:
            continue
        remaining = db.query(SignatureGroupMemberDB).filter(
            SignatureGroupMemberDB.group_id == group.id,
            SignatureGroupMemberDB.signature_id != signature_id,
        ).all()
        enabled_remaining = any(
            row.is_enabled and (candidate := db.get(SignatureDB, row.signature_id))
            and candidate.is_active
            for row in remaining
        )
        if not enabled_remaining:
            disabled_group_ids.add(group.id)
        groups.append({
            "id": group.id, "name": group.name,
            "will_be_empty": not remaining,
            "will_disable": not enabled_remaining,
        })

    rule_rows = db.query(SignatureRuleDB).all()
    rules = [
        {"id": rule.id, "name": rule.name,
         "reason": "direct" if rule.signature_id == signature_id else "empty_group"}
        for rule in rule_rows
        if rule.signature_id == signature_id or rule.group_id in disabled_group_ids
    ]
    schedule_rows = db.query(SignatureScheduleDB).all()
    schedules = [
        {"id": schedule.id, "name": schedule.name,
         "effect": "removed" if schedule.signature_id == signature_id else "disabled"}
        for schedule in schedule_rows
        if schedule.signature_id == signature_id or schedule.group_id in disabled_group_ids
    ]

    marker = _marker_pattern(signature.name)
    overrides = [
        row for row in db.query(UserOverrideDB).all()
        if marker.search(row.html_content or "") or marker.search(row.html_content_source or "")
    ]

    # These are potentially impacted, not guaranteed to match every rule's
    # conditions. Never claim that a signature is still configured for a domain.
    assigned_ids = {
        row.domain_id for row in db.query(SignatureDomainDB).filter(
            SignatureDomainDB.signature_id == signature_id
        ).all()
    }
    relevant = bool(rules or schedules or groups)
    mandatory = (
        db.query(DomainDB).filter(
            DomainDB.signature_required.is_(True),
            DomainDB.is_active.is_(True),
        ).order_by(DomainDB.domain_name).all()
        if relevant else []
    )
    mandatory_domains = [
        {"id": domain.id, "name": domain.domain_name}
        for domain in mandatory
        if not assigned_ids or domain.id in assigned_ids or groups or rules
    ]

    return {
        "id": signature.id, "name": signature.name,
        "groups": groups, "rules": rules, "schedules": schedules,
        "personal_override_count": len(overrides),
        "mandatory_domains": mandatory_domains,
        "version_count": db.query(SignatureVersionDB).filter(
            SignatureVersionDB.signature_id == signature_id
        ).count(),
    }


def delete_signature_with_cleanup(db: Session, store: SignatureFileStore, signature_id: str) -> dict:
    """Remove dependencies safely, preserving rules/groups and unrelated assets.

    Old SIGNATURE schedules must be removed: their existing CHECK constraint
    prohibits a schedule without an explicit valid target. No replacement
    signature is chosen implicitly.
    """
    signature = db.execute(
        select(SignatureDB).where(SignatureDB.id == signature_id).with_for_update()
    ).scalar_one_or_none()
    if signature is None:
        raise SignatureNotFoundError(signature_id)

    impact = inspect_signature_deletion(db, signature_id)
    marker = _marker_pattern(signature.name)
    signature_name = signature.name
    staged = store.stage_signature_delete(signature_name, signature_id)
    try:
        # Keep rule definitions and conditions for later administrator repair.
        for rule in db.query(SignatureRuleDB).filter(
            SignatureRuleDB.signature_id == signature_id
        ).all():
            rule.signature_id = None
            rule.is_enabled = False

        # A legacy schedule cannot exist with a null target under the
        # ck_signature_schedule_target constraint. Explicitly remove it.
        for schedule in db.query(SignatureScheduleDB).filter(
            SignatureScheduleDB.signature_id == signature_id
        ).all():
            db.delete(schedule)

        for group_impact in impact["groups"]:
            if not group_impact["will_disable"]:
                continue
            group = db.get(SignatureGroupDB, group_impact["id"])
            if group is not None:
                group.is_active = False
            for rule in db.query(SignatureRuleDB).filter(
                SignatureRuleDB.group_id == group_impact["id"]
            ).all():
                rule.is_enabled = False
            for schedule in db.query(SignatureScheduleDB).filter(
                SignatureScheduleDB.group_id == group_impact["id"]
            ).all():
                schedule.is_enabled = False

        # Preserve override HTML; remove only the precise complete signature
        # marker. Personal overrides are free-form, not signature foreign keys.
        for override in db.query(UserOverrideDB).all():
            for field in ("html_content", "html_content_source"):
                value = getattr(override, field)
                if value and marker.search(value):
                    setattr(override, field, marker.sub("", value))

        db.query(GroupSelectionStateDB).filter(
            GroupSelectionStateDB.last_signature_id == signature_id
        ).update({"last_signature_id": None}, synchronize_session=False)

        # The retired variant tables still exist in upgraded databases.
        variants = db.query(SignatureVariantDB).filter(
            SignatureVariantDB.signature_id == signature_id
        ).all()
        variant_ids = [variant.id for variant in variants]
        if variant_ids:
            db.query(SelectionStateDB).filter(
                SelectionStateDB.last_variant_id.in_(variant_ids)
            ).update({"last_variant_id": None}, synchronize_session=False)
            db.query(SignatureVariantDB).filter(
                SignatureVariantDB.signature_id == signature_id
            ).delete(synchronize_session=False)

        db.query(SignatureGroupMemberDB).filter(
            SignatureGroupMemberDB.signature_id == signature_id
        ).delete(synchronize_session=False)
        db.query(SignatureDomainDB).filter(
            SignatureDomainDB.signature_id == signature_id
        ).delete(synchronize_session=False)
        db.query(SignatureVersionDB).filter(
            SignatureVersionDB.signature_id == signature_id
        ).delete(synchronize_session=False)
        db.delete(signature)
        db.commit()
    except Exception:
        db.rollback()
        try:
            store.restore_staged_signature(signature_name, staged)
        except Exception as restore_exc:
            logger.critical("Could not restore signature directory after database rollback", exc_info=True)
            raise SignatureStorageError(
                "Signature deletion rolled back, but its storage needs administrator recovery"
            ) from restore_exc
        raise

    # Post-commit unlink is not atomic with the SQL transaction. A failure is
    # reported as pending cleanup; recovery checks the database before unlinking.
    cleanup_pending = False
    try:
        store.purge_staged_signature(staged)
    except SignatureStorageError:
        cleanup_pending = True
        logger.exception("Signature deleted from database; staged files need cleanup")

    return {"status": "deleted", "impact": impact, "files_cleanup_pending": cleanup_pending}


def reconcile_staged_signature_deletes(db: Session, store: SignatureFileStore) -> None:
    """Recover crash-interrupted deletions after the database becomes available.

    A row means the deletion transaction rolled back and its directory must be
    restored. No row means the commit succeeded and quarantine may be purged.
    A nowait row lock prevents touching a live deletion in another worker.
    This must never run at SignatureFileStore initialization without a DB.
    """
    for signature_id, token in store.iter_staged_signature_deletes():
        try:
            signature = db.execute(
                select(SignatureDB).where(SignatureDB.id == signature_id)
                .with_for_update(nowait=True)
            ).scalar_one_or_none()
            if signature is not None:
                store.restore_staged_signature(signature.name, token)
                logger.warning("Recovered HTML files for interrupted signature deletion")
            else:
                store.purge_staged_signature(token)
            db.commit()
        except Exception:
            db.rollback()
            # A locked row, a storage collision or a temporary DB failure must
            # never trigger blind deletion of the quarantined HTML directory.
            logger.exception("Could not reconcile staged signature deletion")
