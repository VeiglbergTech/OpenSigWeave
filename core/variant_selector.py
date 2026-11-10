"""Random signature variant selection with no immediate local repetition."""
from dataclasses import dataclass
import secrets
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SignatureVariant(Generic[T]):
    id: str
    payload: T
    enabled: bool = True
    weight: int = 1


def select_variant(
    variants: Sequence[SignatureVariant[T]],
    previous_variant_id: str | None = None,
) -> SignatureVariant[T] | None:
    """Select an enabled variant, avoiding the previous one when alternatives exist.

    Persistence and concurrency control for previous_variant_id belong to the
    repository/service layer. This function is deterministic in its eligibility
    rules and uses a cryptographically secure random choice for selection.
    """
    eligible = [variant for variant in variants if variant.enabled and variant.weight > 0]
    if not eligible:
        return None

    alternatives = [
        variant for variant in eligible
        if variant.id != previous_variant_id
    ]
    candidates = alternatives or eligible
    total_weight = sum(variant.weight for variant in candidates)
    selected_weight = secrets.randbelow(total_weight)
    for variant in candidates:
        if selected_weight < variant.weight:
            return variant
        selected_weight -= variant.weight
    raise RuntimeError("Variant selection failed unexpectedly")
