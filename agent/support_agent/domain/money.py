"""Auditable finite-number calculator; match the published float/round contract."""
import math
import ast
import operator
from typing import Literal
from support_agent.domain.identity import ReadError

ArithmeticOperation = Literal["add", "subtract", "multiply", "divide", "round"]


def calculate_expression(expression, decimal_places=None):
    """Evaluate a bounded arithmetic AST, never Python code or identifiers.

    Parentheses preserve the model's complete formula in one auditable call.
    Quoted replacement settlement still uses calculate_values' backend algorithm.
    """
    if (not isinstance(expression, str) or not expression.strip() or len(expression) > 1024 or
            (decimal_places is not None and (type(decimal_places) is not int or abs(decimal_places) > 12))):
        raise ReadError("invalid_calculation_input")
    binary = {ast.Add: operator.add, ast.Sub: operator.sub,
              ast.Mult: operator.mul, ast.Div: operator.truediv}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            value = binary[type(node.op)](visit(node.left), visit(node.right))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                node.func.id == "round" and not node.keywords and len(node.args) in (1, 2)):
            digits = visit(node.args[1]) if len(node.args) == 2 else 0.0
            if not digits.is_integer() or abs(digits) > 12:
                raise ReadError("invalid_calculation_input")
            value = float(round(visit(node.args[0]), int(digits)))
        else:
            raise ReadError("unsupported_calculation_expression")
        if not math.isfinite(value):
            raise ReadError("invalid_calculation_result")
        return value
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 128:
            raise ReadError("invalid_calculation_input")
        result = visit(tree.body)
        if decimal_places is not None:
            result = round(result, decimal_places)
    except ReadError:
        raise
    except ZeroDivisionError:
        raise ReadError("division_by_zero") from None
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError):
        raise ReadError("invalid_calculation_expression") from None
    return {"operation": "expression", "expression": expression, "result": result,
            **({"decimal_places": decimal_places} if decimal_places is not None else {})}


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
