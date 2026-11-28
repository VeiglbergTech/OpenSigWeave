"""SQLAlchemy persistence models for OpenSigWeave."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func, JSON

from database import Base


class BackupLocationDB(Base):
    """Named storage destinations, independent of the privileged host operator."""
    __tablename__ = "backup_locations"
    id = Column(String(32), primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    type = Column(String(8), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    server = Column(String(253), nullable=True)
    share = Column(String(80), nullable=True)
    directory = Column(String(512), nullable=True)
    username = Column(String(255), nullable=True)
    domain = Column(String(128), nullable=True)
    password_ciphertext = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())



class BackupRuleDB(Base):
    """Scheduled complete encrypted backups using a named destination."""
    __tablename__ = "backup_rules"
    id = Column(String(32), primary_key=True)
    name = Column(String(120), nullable=False, unique=True)
    description = Column(String(500), nullable=False, default="")
    location_id = Column(String(32), ForeignKey("backup_locations.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    content = Column(String(16), nullable=False, default="full")
    schedule_kind = Column(String(16), nullable=False)
    hour = Column(Integer, nullable=False, default=2)
    minute = Column(Integer, nullable=False, default=0)
    weekday = Column(Integer, nullable=False, default=0)
    monthday = Column(Integer, nullable=False, default=1)
    interval_minutes = Column(Integer, nullable=False, default=1440)
    timezone = Column(String(16), nullable=False, default="UTC")
    retention_count = Column(Integer, nullable=True)
    retention_days = Column(Integer, nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BackupRunDB(Base):
    """Append-only run history; records survive deletion of the parent rule."""
    __tablename__ = "backup_runs"
    id = Column(String(32), primary_key=True)
    rule_id = Column(String(32), ForeignKey("backup_rules.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_name = Column(String(120), nullable=False)
    location_id = Column(String(32), nullable=False, index=True)
    location_name = Column(String(120), nullable=False)
    trigger = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    content = Column(String(16), nullable=False, default="full")
    requested_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    transfer_started_at = Column(DateTime(timezone=True), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    archive_sha256 = Column(String(64), nullable=True)
    file_key = Column(String(100), nullable=True)
    is_retained = Column(Boolean, nullable=False, default=True)
    error_detail = Column(String(300), nullable=True)


class BackupRuntimeDB(Base):
    """A singleton atomic lease; at most one backup can be dispatched globally."""
    __tablename__ = "backup_runtime"
    id = Column(Integer, primary_key=True)
    active_run_id = Column(String(32), nullable=True)

class DomainDB(Base):
    __tablename__ = "domains"
    id = Column(Integer, primary_key=True, index=True)
    domain_name = Column(String, unique=True, index=True)
    is_active = Column(Boolean, default=True)
    allow_overrides = Column(Boolean, default=True)
    inject_on_replies = Column(Boolean, default=False)
    trim_whitespace = Column(Boolean, default=True)
    strip_device_signatures = Column(Boolean, default=False)
    signature_required = Column(Boolean, nullable=False, default=False, server_default="false")
    template_html = Column(Text, default="<p>Best Regards,</p><p><br></p><p><strong>{{ first_name }} {{ last_name }}</strong></p><p>{{ title }} | {{ domain_name }}</p>")
    template_html_source = Column(Text, nullable=True)
    identity_provider_id = Column(String(64), ForeignKey("identity_providers.id", ondelete="SET NULL"), nullable=True, index=True)
    authentication_provider_id = Column(String(64), ForeignKey("identity_providers.id", ondelete="SET NULL"), nullable=True, index=True)
    fallback_values = Column(JSON, nullable=False, default=dict, server_default="{}")

class UserOverrideDB(Base):
    __tablename__ = "user_overrides"
    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, unique=True, index=True)
    html_content = Column(Text, default="")
    html_content_source = Column(Text, nullable=True)
    is_disabled = Column(Boolean, default=False)

class SignatureDB(Base):
    __tablename__ = "signatures"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), nullable=True, index=True)
    scope = Column(String(32), nullable=False, default="DOMAIN")
    # Legacy compatibility column retained for upgrades/rollback only; runtime/API selection does not read it.
    mode = Column(String(32), nullable=False, default="UNUSED")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureDomainDB(Base):
    """Many-to-many assignment between reusable signatures and domains."""
    __tablename__ = "signature_domains"
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="CASCADE"), primary_key=True)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True)


class SignatureVersionDB(Base):
    __tablename__ = "signature_versions"
    id = Column(String(32), primary_key=True)
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    relative_path = Column(String(255), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("signature_id", "version_number", name="uq_signature_version_number"),
    )


class SignatureGroupDB(Base):
    """Named reusable selection pool for signature templates."""
    __tablename__ = "signature_groups"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(64), nullable=False, unique=True, index=True)
    selection_policy = Column(String(40), nullable=False, default="WEIGHTED_RANDOM_NO_REPEAT")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureGroupDomainDB(Base):
    """Optional many-to-many domain scope for signature groups."""
    __tablename__ = "signature_group_domains"
    group_id = Column(String(32), ForeignKey("signature_groups.id", ondelete="CASCADE"), primary_key=True)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True)


class SignatureGroupMemberDB(Base):
    """Signature membership and independent selection weight within a group."""
    __tablename__ = "signature_group_members"
    group_id = Column(String(32), ForeignKey("signature_groups.id", ondelete="CASCADE"), primary_key=True)
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="CASCADE"), primary_key=True)
    weight = Column(Integer, nullable=False, default=1)
    is_enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_signature_group_member_weight_positive"),
    )


class GroupSelectionStateDB(Base):
    """Last selected signature per immutable identity principal and group."""
    __tablename__ = "group_selection_state"
    principal_id = Column(String(320), primary_key=True)
    group_id = Column(String(32), ForeignKey("signature_groups.id", ondelete="CASCADE"), primary_key=True)
    last_signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SignatureVariantDB(Base):
    """Legacy compatibility table; runtime selection no longer reads variants."""
    __tablename__ = "signature_variants"
    id = Column(String(32), primary_key=True)
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="CASCADE"), nullable=False, index=True)
    pool_name = Column(String(32), nullable=False, default="UNUSED")
    weight = Column(Integer, nullable=False, default=1)
    is_enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_signature_variant_weight_positive"),
    )


class SelectionStateDB(Base):
    """Legacy compatibility table retained for existing database upgrades only."""
    __tablename__ = "selection_state"
    id = Column(Integer, primary_key=True)
    sender_email = Column(String(320), nullable=False)
    pool_name = Column(String(32), nullable=False)
    last_variant_id = Column(String(32), ForeignKey("signature_variants.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("sender_email", "pool_name", name="uq_selection_sender_pool"),
    )

class SignatureRuleDB(Base):
    __tablename__ = "signature_rules"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    action_mode = Column(String(32), nullable=False, default="SIGNATURE")
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="SET NULL"), nullable=True)
    group_id = Column(String(32), ForeignKey("signature_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignatureScheduleDB(Base):
    """Time-bounded override selecting one signature or signature group."""
    __tablename__ = "signature_schedules"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), nullable=True, index=True)
    target_type = Column(String(16), nullable=False)
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="SET NULL"), nullable=True, index=True)
    group_id = Column(String(32), ForeignKey("signature_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    starts_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ends_at = Column(DateTime(timezone=True), nullable=False, index=True)
    priority = Column(Integer, nullable=False, default=0)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="ck_signature_schedule_window"),
        CheckConstraint(
            "(target_type = 'SIGNATURE' AND signature_id IS NOT NULL AND group_id IS NULL) "
            "OR (target_type = 'GROUP' AND group_id IS NOT NULL AND signature_id IS NULL)",
            name="ck_signature_schedule_target",
        ),
    )



class SystemSetupDB(Base):
    """Administrator-managed mail adapter and monitoring settings."""
    __tablename__ = "system_setup"
    id = Column(Integer, primary_key=True)
    mail_server = Column(String(32), nullable=False, default="unconfigured")
    monitoring_enabled = Column(Boolean, nullable=False, default=False)
    monitoring_interval = Column(Integer, nullable=False, default=300)
    controller_url = Column(String(255), nullable=False, default="")
    setup_completed = Column(Boolean, nullable=False, default=False)
    last_authenticated_at = Column(DateTime(timezone=True), nullable=True)
    engine_url = Column(String(512), nullable=False, default="")
    last_monitor_status = Column(String(32), nullable=False, default="not_checked")
    last_monitor_detail = Column(String(255), nullable=False, default="")
    last_monitored_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now(), onupdate=func.now())


class LocalAdminDB(Base):
    """Web-provisioned recovery administrator; one active account."""
    __tablename__ = "local_admin"
    id = Column(Integer, primary_key=True)
    email = Column(String(254), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BrandingSettingsDB(Base):
    """Single-row administrator-managed application branding."""
    __tablename__ = "branding_settings"
    id = Column(Integer, primary_key=True)
    app_name = Column(String(120), nullable=False, default="OpenSigWeave")
    app_color = Column(String(7), nullable=False, default="#ff7043")
    large_image_uploads = Column(Boolean, nullable=False, default=False)
    logo_png = Column(LargeBinary, nullable=True)
    dark_logo_png = Column(LargeBinary, nullable=True)
    favicon_data = Column(LargeBinary, nullable=True)
    favicon_mime = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SignatureRuleConditionDB(Base):
    __tablename__ = "signature_rule_conditions"
    id = Column(Integer, primary_key=True)
    rule_id = Column(String(32), ForeignKey("signature_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(64), nullable=False)
    key = Column(String(255), nullable=False, default="")
    value = Column(Text, nullable=False)


class RateLimitBucketDB(Base):
    """One hashed fixed-window counter per rate-limit identity and policy."""
    __tablename__ = "rate_limit_buckets"
    bucket_key = Column(String(64), primary_key=True)
    window_start = Column(Integer, nullable=False)
    request_count = Column(Integer, nullable=False)


class IdentityCacheDB(Base):
    """Last successfully retrieved Authentik attributes per normalized sender."""
    __tablename__ = "identity_attribute_cache"
    email = Column(String(320), primary_key=True)
    first_name = Column(String(256), nullable=False, default="")
    last_name = Column(String(256), nullable=False, default="")
    title = Column(String(256), nullable=False, default="")
    phone = Column(String(256), nullable=False, default="")
    groups = Column(JSON, nullable=False, default=list)
    claims = Column(JSON, nullable=False, default=dict)
    attributes = Column(JSON, nullable=False, default=dict)
    principal_id = Column(String(256), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class IdentityProviderDB(Base):
    """Non-secret metadata for filesystem-backed identity providers."""
    __tablename__ = "identity_providers"
    id = Column(String(64), primary_key=True)
    display_name = Column(String(120), nullable=False)
    provider_type = Column(String(32), nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
