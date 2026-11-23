"""Deterministic rule evaluation for sender and identity attributes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RuleContext:
    sender: str
    domain: str
    groups: frozenset[str] = frozenset()
    claims: Mapping[str, Any] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)
    now: datetime | None = None


@dataclass(frozen=True)
class RuleCondition:
    kind: str
    value: str
    key: str = ""


@dataclass(frozen=True)
class SignatureRule:
    id: str
    priority: int
    conditions: tuple[RuleCondition, ...]
    action: str
    enabled: bool = True
    signature_id: str | None = None
    group_id: str | None = None


def _matches_value(actual: Any, expected: str) -> bool:
    values = actual if isinstance(actual, (list, tuple, set, frozenset)) else (actual,)
    return any(str(value).casefold() == expected.casefold() for value in values if value is not None)


def _not_matches_value(actual: Any, expected: str) -> bool:
    """Missing or entirely empty identity values never satisfy a negative test."""
    values = actual if isinstance(actual, (list, tuple, set, frozenset)) else (actual,)
    present = [
        value for value in values
        if value is not None and (not isinstance(value, str) or value.strip())
    ]
    return bool(present) and not _matches_value(present, expected)


def condition_matches(condition: RuleCondition, context: RuleContext) -> bool:
    if condition.kind == "time_window":
        try:
            start = datetime.fromisoformat(condition.key.replace("Z", "+00:00"))
            end = datetime.fromisoformat(condition.value.replace("Z", "+00:00"))
            now = context.now or datetime.now(timezone.utc)
            if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, now)):
                return False
            return start <= now < end
        except (ValueError, TypeError, AttributeError):
            return False
    if condition.kind in ("sender_equals", "sender_not_equals"):
        equal = context.sender.casefold() == condition.value.casefold()
        return equal if condition.kind == "sender_equals" else not equal
    if condition.kind in ("domain_equals", "domain_not_equals"):
        equal = context.domain.casefold() == condition.value.casefold()
        return equal if condition.kind == "domain_equals" else not equal
    if condition.kind == "group_contains":
        return condition.value.casefold() in {group.casefold() for group in context.groups}
    if condition.kind in ("claim_equals", "claim_not_equals"):
        actual = context.claims.get(condition.key)
        return (_matches_value(actual, condition.value) if condition.kind == "claim_equals"
                else _not_matches_value(actual, condition.value))
    if condition.kind in ("attribute_equals", "attribute_not_equals"):
        actual = context.attributes.get(condition.key)
        return (_matches_value(actual, condition.value) if condition.kind == "attribute_equals"
                else _not_matches_value(actual, condition.value))
    raise ValueError(f"Unsupported rule condition: {condition.kind}")


def rule_matches(rule: SignatureRule, context: RuleContext) -> bool:
    return rule.enabled and all(condition_matches(condition, context) for condition in rule.conditions)


def evaluate_rules(rules: Sequence[SignatureRule], context: RuleContext) -> SignatureRule | None:
    """Return the first matching rule by priority, with a stable ID tie-breaker."""
    ordered = sorted(rules, key=lambda rule: (
        -int(any(item.kind == "time_window" and condition_matches(item, context)
                 for item in rule.conditions)),
        -rule.priority, rule.id
    ))
    return next((rule for rule in ordered if rule_matches(rule, context)), None)
