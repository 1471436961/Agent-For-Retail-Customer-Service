"""Decode SDK scalar representations only at documented response field paths.

No identifiers, customer language, enum values, option strings or shapes are
rewritten. Normal strict OpenAPI models still validate the resulting record.
"""
from copy import deepcopy
from decimal import Decimal
import json
import math


class ResponseValueError(ValueError):
    def __init__(self, path):
        self.path = path
        super().__init__("Invalid serialized response scalar")


def _number(raw, path):
    if not raw or raw != raw.strip():
        raise ResponseValueError(path)
    try:
        number = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
        if not isinstance(number, Decimal) or not number.is_finite():
            raise ValueError("Not a finite JSON number")
        value = float(number)
        if not math.isfinite(value) or (number != 0 and value == 0):
            raise ValueError("Unrepresentable number")
    except (ValueError, OverflowError, ArithmeticError):
        raise ResponseValueError(path) from None
    return value


def normalize_response(body):
    """Return a copy with exact SDK strings decoded at schema-defined leaves.

    A field may occur in several read/write response schemas. Shapes, required
    keys, operation-specific fields and resource matching are checked separately
    by the original strict model; this function never removes unknown fields.
    """
    result = deepcopy(body)
    warnings = []

    def scalar(record, key, path, boolean=False, balance=False):
        if not isinstance(record, dict) or not isinstance(record.get(key), str):
            return
        raw = record[key]
        if boolean:
            if raw not in {"True", "False", "true", "false"}:
                raise ResponseValueError(path)
            value = raw in {"True", "true"}
            code = "boolean_string_value"
        else:
            value = _number(raw, path)
            code = "numeric_string_balance" if balance else "numeric_string_value"
        record[key] = value
        warnings.append({"code": code, "path": path, "original_value": raw})

    if isinstance(result, dict):
        methods = result.get("payment_methods")
        if isinstance(methods, list):
            for i, method in enumerate(methods):
                if isinstance(method, dict) and method.get("source") == "gift_card":
                    scalar(method, "balance", f"payment_methods[{i}].balance", balance=True)
        items = result.get("items")
        if isinstance(items, list):
            for i, item in enumerate(items):
                scalar(item, "price", f"items[{i}].price")
                scalar(item, "available", f"items[{i}].available", boolean=True)
        payments = result.get("payments")
        if isinstance(payments, list):
            for i, payment in enumerate(payments):
                scalar(payment, "amount", f"payments[{i}].amount")
        scalar(result.get("exchange"), "price_difference", "exchange.price_difference")
        scalar(result, "price", "price")
        scalar(result, "available", "available", boolean=True)
    return result, warnings


def validation_issues(error, model):
    """Expose schema locations/types, never input values or unknown key names."""
    known = set()

    def fields(node):
        if isinstance(node, dict):
            for key in ("properties", "$defs"):
                if isinstance(node.get(key), dict):
                    known.update(node[key])
            for value in node.values():
                fields(value)
        elif isinstance(node, list):
            for value in node:
                fields(value)

    fields(model.model_json_schema())
    return [{"path": ".".join(str(part) if isinstance(part, int) or part in known else "*"
                              for part in row["loc"][:12]), "type": row["type"]}
            for row in error.errors(include_url=False, include_context=False, include_input=False)[:8]]
