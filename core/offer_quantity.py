from typing import TypeVar


QuantityValue = TypeVar("QuantityValue")


def coalesce_offer_remaining_quantity(
    remaining_quantity: QuantityValue | None,
    quantity: QuantityValue,
) -> QuantityValue:
    """Fall back to the initial quantity only when the remaining value is absent."""
    return quantity if remaining_quantity is None else remaining_quantity
