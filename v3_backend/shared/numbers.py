"""Numeric serialization helpers shared across domain boundaries."""

from decimal import Decimal


def decimal_to_json_value(value: Decimal) -> int | str:
    """Keep exact decimals in JSON without introducing binary float rounding."""

    integral = value.to_integral_value()
    return int(integral) if value == integral else format(value, "f")


__all__ = ["decimal_to_json_value"]
