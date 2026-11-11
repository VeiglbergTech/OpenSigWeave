"""Load persisted signature rules into the provider-neutral rule engine."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.rules import RuleCondition, SignatureRule
from models import SignatureRuleConditionDB, SignatureRuleDB


def load_signature_rules(session: Session) -> tuple[SignatureRule, ...]:
    """Return persisted rules with their conditions in deterministic order.

    Conditions belonging to one rule are ANDed by the rule engine. Rule
    precedence is resolved by priority and stable rule ID, never by database
    row order. Disabled rules are retained as disabled domain objects so the
    evaluator remains the single authority for enabled-state semantics.
    """
    rows = session.execute(
        select(SignatureRuleDB).order_by(
            SignatureRuleDB.priority.desc(),
            SignatureRuleDB.id.asc(),
        )
    ).scalars().all()
    if not rows:
        return ()

    rule_ids = [row.id for row in rows]
    condition_rows = session.execute(
        select(SignatureRuleConditionDB)
        .where(SignatureRuleConditionDB.rule_id.in_(rule_ids))
        .order_by(SignatureRuleConditionDB.rule_id, SignatureRuleConditionDB.id)
    ).scalars().all()

    conditions_by_rule: dict[str, list[RuleCondition]] = {
        rule_id: [] for rule_id in rule_ids
    }
    for condition in condition_rows:
        conditions_by_rule[condition.rule_id].append(
            RuleCondition(
                kind=condition.kind,
                key=condition.key,
                value=condition.value,
            )
        )

    return tuple(
        SignatureRule(
            id=row.id,
            priority=row.priority,
            conditions=tuple(conditions_by_rule[row.id]),
            action=row.action_mode,
            enabled=row.is_enabled,
            signature_id=row.signature_id,
        )
        for row in rows
    )
