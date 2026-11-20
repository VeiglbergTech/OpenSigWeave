"""Generic weighted selection with no immediate repetition."""
from dataclasses import dataclass
import secrets
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class WeightedCandidate(Generic[T]):
    id: str
    payload: T
    enabled: bool = True
    weight: int = 1


def select_weighted_no_repeat(
    candidates: Sequence[WeightedCandidate[T]],
    previous_id: str | None = None,
) -> WeightedCandidate[T] | None:
    """Select one enabled weighted candidate, avoiding an immediate repeat."""
    eligible = [
        candidate
        for candidate in candidates
        if candidate.enabled and candidate.weight > 0
    ]
    if not eligible:
        return None

    alternatives = [
        candidate for candidate in eligible if candidate.id != previous_id
    ]
    selectable = alternatives or eligible
    total_weight = sum(candidate.weight for candidate in selectable)
    selected_weight = secrets.randbelow(total_weight)
    for candidate in selectable:
        if selected_weight < candidate.weight:
            return candidate
        selected_weight -= candidate.weight
    raise RuntimeError("Weighted selection failed unexpectedly")
