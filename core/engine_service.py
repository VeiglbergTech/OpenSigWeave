"""Framework-independent signature selection and rendering service."""

import hashlib
import hmac
import uuid

from jinja2 import TemplateError
from markupsafe import Markup
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.engine_contract import (
    SignatureEngineError,
    SignatureEngineRequest,
    SignatureEngineResult,
)
from core.group_selection_repository import select_and_record_group_signature
from core.rule_repository import load_signature_rules
from core.rules import RuleContext, evaluate_rules
from core.schedule_repository import load_active_schedule
from models import (
    DomainDB,
    SignatureDB,
    SignatureDomainDB,
    SignatureGroupDB,
    SignatureVersionDB,
    UserOverrideDB,
)
from signature_renderer import (
    prepare_group_placeholders,
    render_signature_template,
    strip_group_placeholders,
)
from signature_sanitizer import sanitize_signature_html
from signature_storage import SignatureStorageError


class SignatureEngineService:
    """Resolve one sender's signature without depending on an HTTP framework.

    The database session is supplied per call. Identity resolution and signature
    storage are injected so transports share the same business logic while tests
    can replace external collaborators.
    """

    def __init__(self, identity_resolver, file_store):
        self.identity_resolver = identity_resolver
        self.file_store = file_store

    async def resolve(
        self,
        db: Session,
        request: SignatureEngineRequest,
        *,
        commit_selection: bool = True,
    ) -> SignatureEngineResult:
        normalized_email = request.sender_email
        domain_part = normalized_email.rsplit("@", 1)[-1]
        domain_obj = db.query(DomainDB).filter(
            func.lower(DomainDB.domain_name) == domain_part
        ).first()

        empty_result = SignatureEngineResult(
            html="",
            inject_on_replies=bool(domain_obj and domain_obj.inject_on_replies),
            trim_whitespace=bool(domain_obj.trim_whitespace) if domain_obj else True,
            strip_device_signatures=bool(domain_obj.strip_device_signatures) if domain_obj else False,
        )
        if not domain_obj or not domain_obj.is_active:
            return empty_result

        override = db.query(UserOverrideDB).filter(
            func.lower(UserOverrideDB.user_email) == normalized_email
        ).first()
        if override and override.is_disabled:
            return empty_result

        selected_from_group = False
        selected_group_id = None
        selected_group_signature = None
        marker_requested = request.marker_requested
        signature_name = request.signature_name
        signature_code = request.signature_code

        active_schedule = None
        if not marker_requested:
            active_schedule = load_active_schedule(db, domain_obj.id)

        if marker_requested:
            # A text marker is a selector, not an authentication mechanism. It is
            # accepted only when its four-character SHA-256 check matches exactly.
            marker_name = (signature_name or "").strip()
            marker_code = (signature_code or "").strip().casefold()
            expected_code = hashlib.sha256(marker_name.casefold().encode("utf-8")).hexdigest()[:4]
            if (
                not marker_name
                or len(marker_name) > 255
                or len(marker_code) != 4
                or not hmac.compare_digest(marker_code, expected_code)
            ):
                return empty_result

            identity_attributes = await self.identity_resolver(
                db, normalized_email, domain_obj.identity_provider_id
            )
            matching_signatures = (
                db.query(SignatureDB)
                .filter(
                    SignatureDB.is_active.is_(True),
                    (
                        SignatureDB.id.in_(
                            db.query(SignatureDomainDB.signature_id).filter(
                                SignatureDomainDB.domain_id == domain_obj.id
                            )
                        )
                        | ~SignatureDB.id.in_(
                            db.query(SignatureDomainDB.signature_id)
                        )
                    ),
                )
                .all()
            )
            matching_signatures = [
                item for item in matching_signatures
                if item.name.casefold() == marker_name.casefold()
            ]
            assigned_ids = {
                row.signature_id
                for row in db.query(SignatureDomainDB).filter(
                    SignatureDomainDB.domain_id == domain_obj.id
                ).all()
            }
            any_assigned_ids = {
                row.signature_id for row in db.query(SignatureDomainDB).all()
            }
            domain_matches = [
                item for item in matching_signatures if item.id in assigned_ids
            ]
            global_matches = [
                item for item in matching_signatures if item.id not in any_assigned_ids
            ]
            # Prefer one exact-domain signature; otherwise accept exactly one global
            # signature. Duplicate names are ambiguous and must not select arbitrarily.
            candidates = domain_matches if domain_matches else global_matches
            if len(candidates) != 1:
                return empty_result
            selected_signature = candidates[0]
            latest_version = (
                db.query(SignatureVersionDB)
                .filter(SignatureVersionDB.signature_id == selected_signature.id)
                .order_by(SignatureVersionDB.version_number.desc())
                .first()
            )
            if latest_version is None:
                raise SignatureEngineError(
                    "SIGNATURE_VERSION_METADATA_MISSING",
                    "Signature version metadata is missing",
                )
            try:
                raw_template = self.file_store.read(
                    selected_signature.name,
                    uuid.UUID(hex=latest_version.id),
                    latest_version.sha256,
                )
            except (SignatureStorageError, ValueError, TypeError):
                raise SignatureEngineError(
                    "SIGNATURE_ASSET_UNAVAILABLE", "Signature asset unavailable"
                )

        elif active_schedule is not None:
            identity_attributes = await self.identity_resolver(
                db, normalized_email, domain_obj.identity_provider_id
            )
            if active_schedule.target_type == "GROUP":
                if not active_schedule.group_id or not identity_attributes.principal_id:
                    return empty_result
                selected_signature = select_and_record_group_signature(
                    db,
                    principal_id=identity_attributes.principal_id,
                    group_id=active_schedule.group_id,
                    domain_id=domain_obj.id,
                )
                if selected_signature is None:
                    return empty_result
                selected_group_id = active_schedule.group_id
                selected_group_signature = selected_signature
                selected_from_group = True
            elif active_schedule.target_type == "SIGNATURE":
                if not active_schedule.signature_id:
                    return empty_result
                selected_signature = db.get(SignatureDB, active_schedule.signature_id)
                if selected_signature is None or not selected_signature.is_active:
                    return empty_result
                assigned_domains = {
                    row.domain_id for row in db.query(SignatureDomainDB).filter(
                        SignatureDomainDB.signature_id == selected_signature.id
                    ).all()
                }
                if assigned_domains and domain_obj.id not in assigned_domains:
                    return empty_result
            else:
                return empty_result

            latest_version = (
                db.query(SignatureVersionDB)
                .filter(SignatureVersionDB.signature_id == selected_signature.id)
                .order_by(SignatureVersionDB.version_number.desc())
                .first()
            )
            if latest_version is None:
                raise SignatureEngineError(
                    "SIGNATURE_VERSION_METADATA_MISSING",
                    "Signature version metadata is missing",
                )
            try:
                raw_template = self.file_store.read(
                    selected_signature.name,
                    uuid.UUID(hex=latest_version.id),
                    latest_version.sha256,
                )
            except (SignatureStorageError, ValueError, TypeError):
                raise SignatureEngineError(
                    "SIGNATURE_ASSET_UNAVAILABLE", "Signature asset unavailable"
                )

        elif override and domain_obj.allow_overrides:
            identity_attributes = await self.identity_resolver(
                db, normalized_email, domain_obj.identity_provider_id
            )
            raw_template = override.html_content

        else:
            identity_attributes = await self.identity_resolver(
                db, normalized_email, domain_obj.identity_provider_id
            )
            context = RuleContext(
                sender=normalized_email,
                domain=domain_part,
                groups=identity_attributes.groups,
                claims=identity_attributes.claims,
                attributes=identity_attributes.attributes,
            )
            matched_rule = evaluate_rules(load_signature_rules(db), context)
            if matched_rule and matched_rule.action == "GROUP":
                if not matched_rule.group_id or not identity_attributes.principal_id:
                    return empty_result
                selected_signature = select_and_record_group_signature(
                    db,
                    principal_id=identity_attributes.principal_id,
                    group_id=matched_rule.group_id,
                    domain_id=domain_obj.id,
                )
                if selected_signature is None:
                    return empty_result
                selected_group_id = matched_rule.group_id
                selected_group_signature = selected_signature
                latest_version = (
                    db.query(SignatureVersionDB)
                    .filter(SignatureVersionDB.signature_id == selected_signature.id)
                    .order_by(SignatureVersionDB.version_number.desc())
                    .first()
                )
                if latest_version is None:
                    raise SignatureEngineError(
                        "SIGNATURE_VERSION_METADATA_MISSING",
                        "Signature version metadata is missing",
                    )
                try:
                    raw_template = self.file_store.read(
                        selected_signature.name,
                        uuid.UUID(hex=latest_version.id),
                        latest_version.sha256,
                    )
                    selected_from_group = True
                except (SignatureStorageError, ValueError, TypeError):
                    raise SignatureEngineError(
                        "SIGNATURE_ASSET_UNAVAILABLE", "Signature asset unavailable"
                    )
            elif matched_rule and matched_rule.action == "SIGNATURE":
                if not matched_rule.signature_id:
                    return empty_result
                selected_signature = db.get(SignatureDB, matched_rule.signature_id)
                if selected_signature is None or not selected_signature.is_active:
                    return empty_result
                assigned_domains = {
                    row.domain_id
                    for row in db.query(SignatureDomainDB)
                    .filter(SignatureDomainDB.signature_id == selected_signature.id)
                    .all()
                }
                if assigned_domains and domain_obj.id not in assigned_domains:
                    return empty_result
                latest_version = (
                    db.query(SignatureVersionDB)
                    .filter(SignatureVersionDB.signature_id == selected_signature.id)
                    .order_by(SignatureVersionDB.version_number.desc())
                    .first()
                )
                if latest_version is None:
                    raise SignatureEngineError(
                        "SIGNATURE_VERSION_METADATA_MISSING",
                        "Signature version metadata is missing",
                    )
                try:
                    raw_template = self.file_store.read(
                        selected_signature.name,
                        uuid.UUID(hex=latest_version.id),
                        latest_version.sha256,
                    )
                except (SignatureStorageError, ValueError, TypeError):
                    raise SignatureEngineError(
                        "SIGNATURE_ASSET_UNAVAILABLE", "Signature asset unavailable"
                    )
            elif matched_rule and matched_rule.action == "NONE":
                return empty_result
            elif matched_rule:
                raise SignatureEngineError(
                    "INVALID_STORED_RULE",
                    "A stored signature rule has an invalid action",
                )
            else:
                # No matching rule means no signature. Retired domain master templates
                # remain stored for backwards-compatible upgrades but are never selected.
                return empty_result

        if not raw_template:
            return empty_result

        first_name = (
            identity_attributes.first_name
            or normalized_email.split("@")[0].capitalize()
        )
        profile_values = {
            "first_name": first_name,
            "last_name": identity_attributes.last_name,
            "title": identity_attributes.title,
            "phone": identity_attributes.phone,
            "email": normalized_email,
            "domain_name": domain_part,
        }
        try:
            raw_template = sanitize_signature_html(raw_template)
            raw_template, group_slots = prepare_group_placeholders(raw_template)
            group_values = {}
            for slug, template_key in group_slots.items():
                group = db.query(SignatureGroupDB).filter(
                    SignatureGroupDB.slug == slug,
                    SignatureGroupDB.is_active.is_(True),
                ).first()
                selected = None
                if group is not None and identity_attributes.principal_id:
                    if group.id == selected_group_id:
                        selected = selected_group_signature
                    else:
                        selected = select_and_record_group_signature(
                            db,
                            principal_id=identity_attributes.principal_id,
                            group_id=group.id,
                            domain_id=domain_obj.id,
                        )
                        if selected is not None:
                            selected_from_group = True
                if selected is None:
                    group_values[template_key] = Markup("")
                    continue
                nested_version = (
                    db.query(SignatureVersionDB)
                    .filter(SignatureVersionDB.signature_id == selected.id)
                    .order_by(SignatureVersionDB.version_number.desc())
                    .first()
                )
                if nested_version is None:
                    raise SignatureEngineError(
                        "GROUP_SIGNATURE_VERSION_METADATA_MISSING",
                        "Group signature version metadata is missing",
                    )
                try:
                    nested_html = self.file_store.read(
                        selected.name,
                        uuid.UUID(hex=nested_version.id),
                        nested_version.sha256,
                    )
                    nested_html = sanitize_signature_html(nested_html)
                    # Group nesting is intentionally one level deep to prevent recursive templates.
                    nested_html = strip_group_placeholders(nested_html)
                    rendered_group = render_signature_template(
                        nested_html, profile_values
                    )
                    # The nested fragment is allowlist-sanitized above; profile values remain autoescaped.
                    group_values[template_key] = Markup(rendered_group)  # nosec B704
                except (SignatureStorageError, ValueError, TypeError):
                    raise SignatureEngineError(
                        "GROUP_SIGNATURE_ASSET_UNAVAILABLE",
                        "Group signature asset unavailable",
                    )
            parsed = render_signature_template(
                raw_template, {**profile_values, **group_values}
            )
        except TemplateError:
            raise SignatureEngineError(
                "INVALID_TEMPLATE", "Invalid signature template"
            )

        if commit_selection and selected_from_group:
            db.commit()

        return SignatureEngineResult(
            html=parsed,
            inject_on_replies=domain_obj.inject_on_replies,
            trim_whitespace=domain_obj.trim_whitespace,
            strip_device_signatures=domain_obj.strip_device_signatures,
        )
