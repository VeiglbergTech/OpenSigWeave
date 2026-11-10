"""Deterministic rule evaluation for sender and identity attributes."""
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RuleContext:
    sender: str
    domain: str
    groups: frozenset[str] = frozenset()
    claims: Mapping[str, Any] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)


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


def condition_matches(condition: RuleCondition, context: RuleContext) -> bool:
    if condition.kind == "sender_equals":
        return context.sender.casefold() == condition.value.casefold()
    if condition.kind == "domain_equals":
        return context.domain.casefold() == condition.value.casefold()
    if condition.kind == "group_contains":
        return condition.value.casefold() in {group.casefold() for group in context.groups}
    if condition.kind == "claim_equals":
        return str(context.claims.get(condition.key, "")) == condition.value
    if condition.kind == "attribute_equals":
        return str(context.attributes.get(condition.key, "")) == condition.value
    raise ValueError(f"Unsupported rule condition: {condition.kind}")


def rule_matches(rule: SignatureRule, context: RuleContext) -> bool:
    return rule.enabled and all(condition_matches(condition, context) for condition in rule.conditions)


def evaluate_rules(rules: Sequence[SignatureRule], context: RuleContext) -> SignatureRule | None:
    """Return the first matching rule by priority, with a stable ID tie-breaker."""
    ordered = sorted(rules, key=lambda rule: (-rule.priority, rule.id))
    return next((rule for rule in ordered if rule_matches(rule, context)), None)
