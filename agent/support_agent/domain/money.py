"""Auditable finite-number calculator; match the published float/round contract."""
import math
from typing import Literal
from support_agent.domain.identity import ReadError

ArithmeticOperation = Literal["add", "subtract", "multiply", "divide", "round"]


def calculate_values(operation, values, decimal_places=None):
    """Plain arithmetic; operand meaning belongs to the model and business rules.

    Legacy trace labels remain readable for earlier tool histories and the
    backend quote validator. They are not advertised in the live calculator.
    """
    try:
        valid = isinstance(values, list) and bool(values) and all(
            type(v) in (int, float) and math.isfinite(v) for v in values)
    except (OverflowError, ValueError):
        valid = False
    if not valid or (decimal_places is not None and type(decimal_places) is not int):
        raise ReadError("invalid_calculation_input")
    precision = 2 if operation == "round" and decimal_places is None else decimal_places
    try:
        if operation in {"add", "sum"}:
            result = sum(values)
        elif operation == "subtract" and len(values) >= 2:
            result = values[0]
            for value in values[1:]:
                result -= value
        elif operation == "difference" and len(values) == 2:
            result = values[0] - values[1]
        elif operation == "multiply":
            result = math.prod(values)
        elif operation == "divide" and len(values) >= 2:
            if any(value == 0 for value in values[1:]):
                raise ReadError("division_by_zero")
            result = values[0]
            for value in values[1:]:
                result /= value
        elif operation == "price_difference" and len(values) % 2 == 0:
            result = 0.0
            for new, old in zip(values[::2], values[1::2]):
                result += new - old
            result = round(result, 2)
        elif operation == "round" and len(values) == 1:
            result = values[0]
        else:
            raise ReadError("unsupported_calculation")
        if precision is not None:
            result = round(result, precision)
        if not math.isfinite(result):
            raise ArithmeticError("Non-finite result")
    except ReadError:
        raise
    except (OverflowError, ValueError, ArithmeticError):
        raise ReadError("invalid_calculation_result")
    outcome = {"operation": operation, "inputs": list(values), "result": result}
    if precision is not None:
        outcome["decimal_places"] = precision
    return outcome
