"""SQLAlchemy persistence models for OpenSigWeave."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, JSON

from database import Base


class DomainDB(Base):
    __tablename__ = "domains"
    id = Column(Integer, primary_key=True, index=True)
    domain_name = Column(String, unique=True, index=True)
    is_active = Column(Boolean, default=True)
    allow_overrides = Column(Boolean, default=True)
    inject_on_replies = Column(Boolean, default=False)
    trim_whitespace = Column(Boolean, default=True)
    strip_device_signatures = Column(Boolean, default=False)
    template_html = Column(Text, default="<p>Best Regards,</p><p><br></p><p><strong>{{ first_name }} {{ last_name }}</strong></p><p>{{ title }} | {{ domain_name }}</p>")

class UserOverrideDB(Base):
    __tablename__ = "user_overrides"
    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String, unique=True, index=True)
    html_content = Column(Text, default="")
    is_disabled = Column(Boolean, default=False)

class SignatureDB(Base):
    __tablename__ = "signatures"
    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    domain_id = Column(Integer, ForeignKey("domains.id", ondelete="CASCADE"), nullable=True, index=True)
    scope = Column(String(32), nullable=False, default="DOMAIN")
    mode = Column(String(32), nullable=False, default="AUTO")
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


class SignatureVariantDB(Base):
    __tablename__ = "signature_variants"
    id = Column(String(32), primary_key=True)
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="CASCADE"), nullable=False, index=True)
    pool_name = Column(String(32), nullable=False, default="AUTO")
    weight = Column(Integer, nullable=False, default=1)
    is_enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (
        CheckConstraint("weight > 0", name="ck_signature_variant_weight_positive"),
    )


class SelectionStateDB(Base):
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
    action_mode = Column(String(32), nullable=False, default="AUTO")
    signature_id = Column(String(32), ForeignKey("signatures.id", ondelete="SET NULL"), nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


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
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
