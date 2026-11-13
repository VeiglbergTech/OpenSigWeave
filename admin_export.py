"""Bounded, secret-free administrator component exports.

Exports are portable ZIP archives containing a versioned JSON manifest and
original signature HTML version assets. Identity-provider metadata is included
without provider credential files; user overrides and identity caches are
intentionally excluded because they contain personal data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import hashlib
import json
import uuid
import zipfile

from sqlalchemy.orm import Session

from models import (
    DomainDB,
    IdentityProviderDB,
    SignatureDB,
    SignatureDomainDB,
    SignatureGroupDB,
    SignatureGroupDomainDB,
    SignatureGroupMemberDB,
    SignatureRuleDB,
    SignatureRuleConditionDB,
    SignatureVariantDB,
    SignatureVersionDB,
)
from signature_storage import SignatureFileStore, SignatureStorageError


EXPORT_FORMAT = "opensigweave-component-export"
EXPORT_VERSION = 1
MAX_EXPORT_BYTES = 20 * 1024 * 1024
ALLOWED_COMPONENTS = frozenset({
    "signatures", "domains", "rules", "groups", "identity_providers",
})


class AdminExportError(Exception):
    """Raised when a component export cannot be safely generated."""


def _timestamp(value) -> str | None:
    return value.isoformat() if value is not None else None


def build_admin_export(
    db: Session,
    store: SignatureFileStore,
    components: list[str],
) -> tuple[bytes, str]:
    """Build a bounded ZIP export for the explicitly selected components."""
    if not isinstance(components, list) or not components:
        raise AdminExportError("Select at least one export component")
    if any(not isinstance(item, str) or item not in ALLOWED_COMPONENTS for item in components):
        raise AdminExportError("The export contains an unsupported component")
    selected = sorted(set(components))
    manifest = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "components": selected,
        "data": {},
        "assets": [],
        "exclusions": [
            "Identity-provider credential files and secrets are never exported.",
            "Personal user overrides and identity attribute caches are excluded.",
            "A component export is not a complete disaster-recovery backup.",
        ],
    }
    assets: dict[str, bytes] = {}
    total_bytes = 0

    def add_asset(name: str, content: str) -> str:
        nonlocal total_bytes
        payload = content.encode("utf-8")
        total_bytes += len(payload)
        if total_bytes > MAX_EXPORT_BYTES:
            raise AdminExportError("Export exceeds the 20 MiB uncompressed size limit")
        assets[name] = payload
        manifest["assets"].append({
            "path": name,
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        return name

    try:
        if "signatures" in selected:
            exported = []
            signatures = db.query(SignatureDB).order_by(SignatureDB.name.asc()).all()
            for signature in signatures:
                domain_ids = [
                    row.domain_id for row in db.query(SignatureDomainDB)
                    .filter(SignatureDomainDB.signature_id == signature.id)
                    .order_by(SignatureDomainDB.domain_id.asc()).all()
                ]
                versions = db.query(SignatureVersionDB).filter(
                    SignatureVersionDB.signature_id == signature.id
                ).order_by(SignatureVersionDB.version_number.asc()).all()
                version_items = []
                for version in versions:
                    try:
                        html = store.read(
                            signature.name, uuid.UUID(hex=version.id), version.sha256
                        )
                    except (SignatureStorageError, ValueError, TypeError) as exc:
                        raise AdminExportError(
                            f"Signature asset integrity check failed for {signature.name}"
                        ) from exc
                    asset = add_asset(
                        f"assets/signatures/{signature.id}/versions/{version.version_number}.html",
                        html,
                    )
                    version_items.append({
                        "id": version.id,
                        "version_number": version.version_number,
                        "relative_path": version.relative_path,
                        "sha256": version.sha256,
                        "size_bytes": version.size_bytes,
                        "created_by": version.created_by,
                        "created_at": _timestamp(version.created_at),
                        "asset": asset,
                    })
                variants = db.query(SignatureVariantDB).filter(
                    SignatureVariantDB.signature_id == signature.id
                ).order_by(SignatureVariantDB.pool_name.asc(), SignatureVariantDB.id.asc()).all()
                exported.append({
                    "id": signature.id,
                    "name": signature.name,
                    "domain_id": signature.domain_id,
                    "domain_ids": domain_ids,
                    "scope": signature.scope,
                    "mode": signature.mode,
                    "is_active": signature.is_active,
                    "created_at": _timestamp(signature.created_at),
                    "versions": version_items,
                    "variants": [{
                        "id": item.id,
                        "pool_name": item.pool_name,
                        "weight": item.weight,
                        "is_enabled": item.is_enabled,
                    } for item in variants],
                })
            manifest["data"]["signatures"] = exported

        if "domains" in selected:
            domains = db.query(DomainDB).order_by(DomainDB.domain_name.asc()).all()
            manifest["data"]["domains"] = [{
                "id": item.id,
                "domain_name": item.domain_name,
                "is_active": item.is_active,
                "allow_overrides": item.allow_overrides,
                "inject_on_replies": item.inject_on_replies,
                "trim_whitespace": item.trim_whitespace,
                "strip_device_signatures": item.strip_device_signatures,
                "template_html": item.template_html_source if item.template_html_source is not None else item.template_html,
                "identity_provider_id": item.identity_provider_id,
                "authentication_provider_id": item.authentication_provider_id,
            } for item in domains]

        if "groups" in selected:
            groups = db.query(SignatureGroupDB).order_by(SignatureGroupDB.name.asc()).all()
            manifest["data"]["groups"] = [{
                "id": group.id,
                "name": group.name,
                "slug": group.slug,
                "selection_policy": group.selection_policy,
                "is_active": group.is_active,
                "domain_ids": [
                    row.domain_id for row in db.query(SignatureGroupDomainDB)
                    .filter(SignatureGroupDomainDB.group_id == group.id)
                    .order_by(SignatureGroupDomainDB.domain_id.asc()).all()
                ],
                "members": [{
                    "signature_id": row.signature_id,
                    "weight": row.weight,
                    "is_enabled": row.is_enabled,
                } for row in db.query(SignatureGroupMemberDB)
                    .filter(SignatureGroupMemberDB.group_id == group.id)
                    .order_by(SignatureGroupMemberDB.signature_id.asc()).all()],
            } for group in groups]

        if "rules" in selected:
            rules = db.query(SignatureRuleDB).order_by(
                SignatureRuleDB.priority.desc(), SignatureRuleDB.name.asc()
            ).all()
            manifest["data"]["rules"] = [{
                "id": rule.id,
                "name": rule.name,
                "priority": rule.priority,
                "action_mode": rule.action_mode,
                "signature_id": rule.signature_id,
                "group_id": rule.group_id,
                "is_enabled": rule.is_enabled,
                "conditions": [{
                    "kind": condition.kind,
                    "key": condition.key,
                    "value": condition.value,
                } for condition in db.query(SignatureRuleConditionDB)
                    .filter(SignatureRuleConditionDB.rule_id == rule.id)
                    .order_by(SignatureRuleConditionDB.id.asc()).all()],
            } for rule in rules]

        if "identity_providers" in selected:
            providers = db.query(IdentityProviderDB).order_by(
                IdentityProviderDB.display_name.asc()
            ).all()
            manifest["data"]["identity_providers"] = [{
                "id": item.id,
                "display_name": item.display_name,
                "provider_type": item.provider_type,
                "is_enabled": item.is_enabled,
                "created_at": _timestamp(item.created_at),
                "credentials_included": False,
            } for item in providers]

        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        total_bytes += len(manifest_bytes)
        if total_bytes > MAX_EXPORT_BYTES:
            raise AdminExportError("Export exceeds the 20 MiB uncompressed size limit")

        buffer = BytesIO()
        with zipfile.ZipFile(
            buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
            allowZip64=False,
        ) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            for name, payload in sorted(assets.items()):
                archive.writestr(name, payload)
        payload = buffer.getvalue()
        if len(payload) > MAX_EXPORT_BYTES:
            raise AdminExportError("Compressed export exceeds the 20 MiB size limit")
        filename = "opensigweave-export-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".zip"
        return payload, filename
    except AdminExportError:
        raise
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        raise AdminExportError("Unable to generate the requested component export") from exc
