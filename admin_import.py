"""Validated, additive imports for secret-free administrator component exports.

Imports never overwrite existing catalog records. Existing domains and identity
providers may be reused only when their exported metadata matches exactly.
"""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import re
import uuid
import zipfile

from sqlalchemy import func
from sqlalchemy.orm import Session

from admin_export import (
    ALLOWED_COMPONENTS,
    EXPORT_FORMAT,
    EXPORT_VERSION,
    MAX_EXPORT_BYTES,
)
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
from signature_sanitizer import sanitize_signature_html
from signature_storage import MAX_SIGNATURE_BYTES, SignatureFileStore, SignatureStorageError


MAX_IMPORT_ENTRIES = 512
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODE_VALUES = frozenset({"AUTO", "PROFESSIONAL", "INTERNAL", "NONE"})
SCOPE_VALUES = frozenset({"DOMAIN", "GLOBAL"})
RULE_ACTIONS = frozenset({"AUTO", "PROFESSIONAL", "INTERNAL", "NONE", "GROUP"})


class AdminImportError(Exception):
    """Raised when an administrator import is invalid or conflicts with local data."""


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise AdminImportError(f"{label} must be an object")
    return value


def _string(value, label: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise AdminImportError(f"{label} is invalid")
    return value


def _integer(value, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AdminImportError(f"{label} is invalid")
    return value


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise AdminImportError(f"{label} is invalid")
    return value


def _identifier(value, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise AdminImportError(f"{label} is invalid")
    return value


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdminImportError("The manifest contains duplicate JSON keys")
        result[key] = value
    return result


def read_admin_import(payload: bytes) -> tuple[dict, dict[str, bytes]]:
    """Validate ZIP structure, manifest, asset bounds, and every asset digest."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_EXPORT_BYTES:
        raise AdminImportError("Import archive is empty or exceeds the 20 MiB limit")
    try:
        with zipfile.ZipFile(BytesIO(payload), "r") as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_IMPORT_ENTRIES:
                raise AdminImportError("Import archive contains an invalid number of files")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise AdminImportError("Import archive contains duplicate files or no manifest")
            for entry in entries:
                name = entry.filename
                if (
                    not name or name.startswith(("/", "\\\\"))
                    or "\\\\" in name or "\x00" in name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or ":" in name
                    or entry.is_dir()
                    or entry.flag_bits & 0x1
                    or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise AdminImportError("Import archive contains an unsafe file entry")
                if entry.file_size > MAX_EXPORT_BYTES:
                    raise AdminImportError("An import archive entry exceeds the size limit")
            manifest_info = next(item for item in entries if item.filename == "manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise AdminImportError("Import manifest exceeds the size limit")
            manifest_bytes = archive.read(manifest_info)
            manifest = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_json_object)
            manifest = _object(manifest, "manifest")
            if manifest.get("format") != EXPORT_FORMAT or manifest.get("format_version") != EXPORT_VERSION:
                raise AdminImportError("Unsupported OpenSigWeave export format or version")
            components = manifest.get("components")
            if (
                not isinstance(components, list) or not components
                or any(not isinstance(item, str) or item not in ALLOWED_COMPONENTS for item in components)
                or len(components) != len(set(components))
            ):
                raise AdminImportError("Import component selection is invalid")
            data = _object(manifest.get("data"), "manifest data")
            if set(data) != set(components):
                raise AdminImportError("Manifest data does not match its component selection")
            declared_assets = manifest.get("assets")
            if not isinstance(declared_assets, list) or len(declared_assets) != len(entries) - 1:
                raise AdminImportError("Manifest asset list does not match the archive")
            assets: dict[str, bytes] = {}
            declared_paths = set()
            total_uncompressed = len(manifest_bytes)
            for item in declared_assets:
                item = _object(item, "asset metadata")
                path = _string(item.get("path"), "asset path", 512)
                if path == "manifest.json" or path in declared_paths:
                    raise AdminImportError("Manifest contains duplicate or reserved asset paths")
                declared_paths.add(path)
                if path not in names:
                    raise AdminImportError("A declared asset is missing from the archive")
                info = archive.getinfo(path)
                if info.file_size != _integer(item.get("size_bytes"), "asset size"):
                    raise AdminImportError("An asset size does not match its manifest")
                expected_hash = item.get("sha256")
                if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
                    raise AdminImportError("An asset checksum is invalid")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_EXPORT_BYTES:
                    raise AdminImportError("Import archive exceeds the 20 MiB uncompressed size limit")
                content = archive.read(info)
                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise AdminImportError("An import asset failed its SHA-256 integrity check")
                assets[path] = content
            if declared_paths != set(names) - {"manifest.json"}:
                raise AdminImportError("Archive contains undeclared files")
            return manifest, assets
    except AdminImportError:
        raise
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        raise AdminImportError("Import archive is malformed or cannot be read") from exc


def _domain_signature(domain: DomainDB) -> tuple:
    source = domain.template_html_source if domain.template_html_source is not None else domain.template_html
    return (
        domain.is_active, domain.allow_overrides, domain.inject_on_replies,
        domain.trim_whitespace, domain.strip_device_signatures, source,
        domain.identity_provider_id, domain.authentication_provider_id,
    )


def _provider_signature(provider: IdentityProviderDB) -> tuple:
    return (provider.display_name, provider.provider_type, provider.is_enabled)


def inspect_admin_import(
    db: Session,
    store: SignatureFileStore,
    payload: bytes,
) -> dict:
    """Build a deterministic additive-import plan without modifying the database."""
    manifest, assets = read_admin_import(payload)
    data = manifest["data"]
    components = set(manifest["components"])
    conflicts: list[str] = []
    reusable: dict[str, int] = {name: 0 for name in components}
    additions: dict[str, int] = {name: 0 for name in components}
    domain_map: dict[int, int] = {}
    provider_ids: set[str] = set()
    signature_ids: set[str] = set()
    group_ids: set[str] = set()

    imported_providers = {}
    if "identity_providers" in components:
        for row in data["identity_providers"]:
            row = _object(row, "identity provider")
            provider_id = _string(row.get("id"), "identity provider id", 64)
            if provider_id in imported_providers:
                conflicts.append(f"Duplicate identity provider id: {provider_id}")
                continue
            if row.get("credentials_included") is not False:
                conflicts.append(f"Identity provider {provider_id} has invalid credential metadata")
            expected = (
                _string(row.get("display_name"), "provider display name", 120),
                _string(row.get("provider_type"), "provider type", 32),
                _boolean(row.get("is_enabled"), "provider enabled state"),
            )
            existing = db.get(IdentityProviderDB, provider_id)
            if existing:
                if _provider_signature(existing) != expected:
                    conflicts.append(f"Identity provider id already exists with different metadata: {provider_id}")
                else:
                    reusable["identity_providers"] += 1
            else:
                additions["identity_providers"] += 1
            imported_providers[provider_id] = expected
            provider_ids.add(provider_id)

    if "domains" in components:
        for row in data["domains"]:
                row = _object(row, "domain")
                for key in ("identity_provider_id", "authentication_provider_id"):
                    provider_id = row.get(key)
                    if provider_id is not None:
                        _string(provider_id, key, 64)
                        if provider_id not in imported_providers and db.get(IdentityProviderDB, provider_id) is None:
                            conflicts.append(f"Domain references unavailable identity provider: {provider_id}")

    if "domains" in components:
        imported_domain_ids = set()
        for row in data["domains"]:
            row = _object(row, "domain")
            source_id = _integer(row.get("id"), "domain id", 1)
            if source_id in imported_domain_ids:
                conflicts.append(f"Duplicate domain id: {source_id}")
                continue
            imported_domain_ids.add(source_id)
            name = _string(row.get("domain_name"), "domain name", 253).strip().casefold().rstrip(".")
            expected = (
                _boolean(row.get("is_active"), "domain active state"),
                _boolean(row.get("allow_overrides"), "domain override policy"),
                _boolean(row.get("inject_on_replies"), "domain reply policy"),
                _boolean(row.get("trim_whitespace"), "domain whitespace policy"),
                _boolean(row.get("strip_device_signatures"), "domain device-signature policy"),
                _string(row.get("template_html"), "domain template", 65536, empty=True),
                row.get("identity_provider_id"), row.get("authentication_provider_id"),
            )
            existing = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == name).first()
            if existing:
                if _domain_signature(existing) != expected:
                    conflicts.append(f"Domain already exists with different settings: {name}")
                    continue
                domain_map[source_id] = existing.id
                reusable["domains"] += 1
            else:
                additions["domains"] += 1
                domain_map[source_id] = -source_id

    if "signatures" in components:
        seen_names = set()
        for row in data["signatures"]:
            row = _object(row, "signature")
            signature_id = _identifier(row.get("id"), "signature id")
            name = _string(row.get("name"), "signature name", 255).strip()
            folded = name.casefold()
            if folded in seen_names:
                conflicts.append(f"Duplicate signature name: {name}")
            seen_names.add(folded)
            if db.get(SignatureDB, signature_id) or db.query(SignatureDB).filter(func.lower(SignatureDB.name) == folded).first():
                conflicts.append(f"Signature id or name already exists: {name}")
            else:
                additions["signatures"] += 1
                signature_ids.add(signature_id)
            if row.get("scope") not in SCOPE_VALUES or row.get("mode") not in MODE_VALUES:
                conflicts.append(f"Signature has an unsupported scope or mode: {name}")
            if not isinstance(row.get("is_active"), bool):
                conflicts.append(f"Signature active state is invalid: {name}")
            domain_ids = row.get("domain_ids", [])
            if not isinstance(domain_ids, list) or any(isinstance(value, bool) or not isinstance(value, int) for value in domain_ids):
                conflicts.append(f"Signature domain assignments are invalid: {name}")
            else:
                for source_domain_id in domain_ids:
                    if source_domain_id not in domain_map:
                        target = db.get(DomainDB, source_domain_id)
                        if target is None:
                            conflicts.append(f"Signature {name} references a domain not included in the import")
            versions = row.get("versions")
            if not isinstance(versions, list) or not versions:
                conflicts.append(f"Signature has no HTML versions: {name}")
                continue
            numbers = set()
            for version in versions:
                version = _object(version, "signature version")
                number = _integer(version.get("version_number"), "version number", 1)
                if number in numbers:
                    conflicts.append(f"Signature contains duplicate version numbers: {name}")
                numbers.add(number)
                _identifier(version.get("id"), "version id")
                path = _string(version.get("asset"), "version asset path", 512)
                if path not in assets:
                    conflicts.append(f"Signature version asset is missing: {name}")
                elif hashlib.sha256(assets[path]).hexdigest() != version.get("sha256") or len(assets[path]) != version.get("size_bytes"):
                    conflicts.append(f"Signature version metadata does not match its asset: {name}")
                size = _integer(version.get("size_bytes"), "version size")
                if size > MAX_SIGNATURE_BYTES:
                    conflicts.append(f"Signature version exceeds the HTML size limit: {name}")
                if not isinstance(version.get("sha256"), str) or not SHA256_PATTERN.fullmatch(version["sha256"]):
                    conflicts.append(f"Signature version checksum is invalid: {name}")
            variants = row.get("variants", [])
            if not isinstance(variants, list):
                conflicts.append(f"Signature variants are invalid: {name}")
            else:
                for variant in variants:
                    variant = _object(variant, "signature variant")
                    if _integer(variant.get("weight"), "variant weight", 1) < 1:
                        conflicts.append(f"Signature variant weight is invalid: {name}")
                    if variant.get("pool_name") != row.get("mode"):
                        conflicts.append(f"Signature variant pool does not match its signature mode: {name}")
                    _boolean(variant.get("is_enabled"), "variant enabled state")

    if "groups" in components:
        for row in data["groups"]:
            row = _object(row, "signature group")
            group_id = _identifier(row.get("id"), "group id")
            slug = _string(row.get("slug"), "group slug", 64)
            if db.get(SignatureGroupDB, group_id) or db.query(SignatureGroupDB).filter(SignatureGroupDB.slug == slug).first():
                conflicts.append(f"Group id or slug already exists: {slug}")
            else:
                additions["groups"] += 1
                group_ids.add(group_id)
            if not isinstance(row.get("domain_ids", []), list) or not isinstance(row.get("members", []), list):
                conflicts.append(f"Group assignments are invalid: {slug}")
            for source_domain_id in row.get("domain_ids", []):
                if source_domain_id not in domain_map and db.get(DomainDB, source_domain_id) is None:
                    conflicts.append(f"Group {slug} references a domain not included in the import")
            for member in row.get("members", []):
                member = _object(member, "group member")
                signature_id = _identifier(member.get("signature_id"), "group member signature id")
                if signature_id not in signature_ids and db.get(SignatureDB, signature_id) is None:
                    conflicts.append(f"Group {slug} references a signature not included in the import")
                _integer(member.get("weight"), "group member weight", 1)
                _boolean(member.get("is_enabled"), "group member enabled state")

    if "rules" in components:
        for row in data["rules"]:
            row = _object(row, "signature rule")
            rule_id = _identifier(row.get("id"), "rule id")
            name = _string(row.get("name"), "rule name", 255)
            if db.get(SignatureRuleDB, rule_id):
                conflicts.append(f"Rule id already exists: {name}")
            else:
                additions["rules"] += 1
            if row.get("action_mode") not in RULE_ACTIONS:
                conflicts.append(f"Rule action is unsupported: {name}")
            signature_id = row.get("signature_id")
            if signature_id is not None and signature_id not in signature_ids and db.get(SignatureDB, signature_id) is None:
                conflicts.append(f"Rule {name} references a signature not included in the import")
            group_id = row.get("group_id")
            if group_id is not None and group_id not in group_ids and db.get(SignatureGroupDB, group_id) is None:
                conflicts.append(f"Rule {name} references a group not included in the import")
            if not isinstance(row.get("conditions"), list):
                conflicts.append(f"Rule conditions are invalid: {name}")
            else:
                for condition in row["conditions"]:
                    condition = _object(condition, "rule condition")
                    _string(condition.get("kind"), "condition kind", 64)
                    _string(condition.get("key"), "condition key", 255, empty=True)
                    _string(condition.get("value"), "condition value", 4096, empty=True)

    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_VERSION,
        "components": sorted(components),
        "additions": additions,
        "reusable": reusable,
        "conflicts": list(dict.fromkeys(conflicts)),
        "can_apply": not conflicts,
        "excluded": [
            "Provider credentials are not imported.",
            "Personal user overrides and identity caches are not imported.",
            "Existing records are never overwritten.",
        ],
    }


def apply_admin_import(
    db: Session,
    store: SignatureFileStore,
    payload: bytes,
) -> dict:
    """Apply a validated additive import atomically, cleaning up written files on failure."""
    manifest, assets = read_admin_import(payload)
    plan = inspect_admin_import(db, store, payload)
    if not plan["can_apply"]:
        raise AdminImportError("Import has conflicts; resolve them before applying")
    data = manifest["data"]
    components = set(manifest["components"])
    domain_map: dict[int, int] = {}
    written_assets: list[tuple[str, uuid.UUID]] = []
    try:
        if "identity_providers" in components:
            for row in data["identity_providers"]:
                if db.get(IdentityProviderDB, row["id"]) is None:
                    db.add(IdentityProviderDB(
                        id=row["id"], display_name=row["display_name"],
                        provider_type=row["provider_type"], is_enabled=row["is_enabled"],
                    ))
            db.flush()

        if "domains" in components:
            for row in data["domains"]:
                source_id = row["id"]
                existing = db.query(DomainDB).filter(func.lower(DomainDB.domain_name) == row["domain_name"].strip().casefold().rstrip(".")).first()
                if existing:
                    domain_map[source_id] = existing.id
                    continue
                template = row["template_html"]
                domain = DomainDB(
                    domain_name=row["domain_name"].strip().casefold().rstrip("."),
                    is_active=row["is_active"],
                    allow_overrides=row["allow_overrides"],
                    inject_on_replies=row["inject_on_replies"],
                    trim_whitespace=row["trim_whitespace"],
                    strip_device_signatures=row["strip_device_signatures"],
                    template_html=sanitize_signature_html(template),
                    template_html_source=template,
                    identity_provider_id=row["identity_provider_id"],
                    authentication_provider_id=row["authentication_provider_id"],
                )
                db.add(domain)
                db.flush()
                domain_map[source_id] = domain.id

        if "signatures" in components:
            for row in data["signatures"]:
                domain_ids = row.get("domain_ids", [])
                resolved_domains = [
                    domain_map.get(item, item) for item in domain_ids
                ]
                signature = SignatureDB(
                    id=row["id"], name=row["name"], domain_id=resolved_domains[0] if len(resolved_domains) == 1 else None,
                    scope=row["scope"], mode=row["mode"], is_active=row["is_active"],
                )
                store.ensure_name_available(row["name"])
                db.add(signature)
                db.flush()
                for domain_id in resolved_domains:
                    db.add(SignatureDomainDB(signature_id=signature.id, domain_id=domain_id))
                versions = sorted(row["versions"], key=lambda item: item["version_number"])
                for version in versions:
                    html = assets[version["asset"]].decode("utf-8")
                    version_uuid = uuid.uuid4()
                    stored = store.write(version_uuid, signature.name, html)
                    written_assets.append((signature.name, version_uuid))
                    db.add(SignatureVersionDB(
                        id=version_uuid.hex, signature_id=signature.id,
                        version_number=version["version_number"],
                        relative_path=stored.relative_path, sha256=stored.sha256,
                        size_bytes=stored.size_bytes, created_by=version.get("created_by"),
                    ))
                for variant in row.get("variants", []):
                    db.add(SignatureVariantDB(
                        id=uuid.uuid4().hex, signature_id=signature.id,
                        pool_name=variant["pool_name"], weight=variant["weight"],
                        is_enabled=variant["is_enabled"],
                    ))

        if "groups" in components:
            for row in data["groups"]:
                group = SignatureGroupDB(
                    id=row["id"], name=row["name"], slug=row["slug"],
                    selection_policy=row["selection_policy"], is_active=row["is_active"],
                )
                db.add(group)
                db.flush()
                for source_domain_id in row.get("domain_ids", []):
                    db.add(SignatureGroupDomainDB(
                        group_id=group.id, domain_id=domain_map.get(source_domain_id, source_domain_id),
                    ))
                for member in row.get("members", []):
                    db.add(SignatureGroupMemberDB(
                        group_id=group.id, signature_id=member["signature_id"],
                        weight=member["weight"], is_enabled=member["is_enabled"],
                    ))

        if "rules" in components:
            for row in data["rules"]:
                rule = SignatureRuleDB(
                    id=row["id"], name=row["name"], priority=row["priority"],
                    action_mode=row["action_mode"], signature_id=row["signature_id"],
                    group_id=row["group_id"], is_enabled=row["is_enabled"],
                )
                db.add(rule)
                db.flush()
                for condition in row.get("conditions", []):
                    db.add(SignatureRuleConditionDB(
                        rule_id=rule.id, kind=condition["kind"],
                        key=condition["key"], value=condition["value"],
                    ))

        db.commit()
        return {
            "status": "imported",
            "components": sorted(components),
            "additions": plan["additions"],
            "reusable": plan["reusable"],
        }
    except Exception as exc:
        db.rollback()
        for name, version_id in reversed(written_assets):
            try:
                store.delete(name, version_id)
            except (SignatureStorageError, OSError):
                pass
        if isinstance(exc, AdminImportError):
            raise
        raise AdminImportError("Import could not be committed; no database changes were retained") from exc
