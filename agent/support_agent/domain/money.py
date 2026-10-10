"""Auditable finite-number calculator; match the published float/round contract."""
import math
from support_agent.domain.identity import ReadError


def calculate_values(operation, values):
    if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ReadError("invalid_calculation_input")
    if operation == "sum":
        result = sum(values)
    elif operation == "difference" and len(values) == 2:
        result = values[0] - values[1]
    elif operation == "price_difference" and len(values) % 2 == 0:
        result = 0.0
        for new, old in zip(values[::2], values[1::2]):
            result += new - old
        result = round(result, 2)
    elif operation == "round" and len(values) == 1:
        result = round(values[0], 2)
    else:
        raise ReadError("unsupported_calculation")
    if not math.isfinite(result):
        raise ReadError("invalid_calculation_result")
    return {"operation": operation, "inputs": list(values), "result": result}
