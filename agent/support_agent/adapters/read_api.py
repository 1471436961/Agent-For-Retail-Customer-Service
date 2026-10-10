"""Six explicit read operations through the injected ClientAPI only."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import math
from urllib.parse import quote

from pydantic import ValidationError
from support_agent.adapters.read_models import READ_MODELS
from support_agent.domain.identity import ReadError, resource_id

_ERROR_CODES = {
    "invalid_body", "resource_not_found", "resource_conflict", "business_rule_violation",
    "request_too_large", "response_too_large", "invalid_response_body", "method_not_allowed",
}


def _customer_balance_compatibility(body):
    """Normalize only the gift-card balance discrepancy observed in a t1 trace.

    Other field types still use the exact normal OpenAPI. Accepted strings are
    finite JSON numbers; invalid values never become zero. Keep a bounded warning
    on the derived tool record, without modifying the backend response.
    """
    result = deepcopy(body)
    warnings = []
    methods = result.get("payment_methods")
    if not isinstance(methods, list):
        return result, warnings
    for index, method in enumerate(methods):
        if not isinstance(method, dict) or method.get("source") != "gift_card":
            continue
        raw = method.get("balance")
        if not isinstance(raw, str):
            continue
        if not raw or len(raw) > 128 or raw != raw.strip():
            raise ValueError("Invalid numeric balance string")
        number = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
        if not isinstance(number, Decimal) or not number.is_finite():
            raise ValueError("Invalid numeric balance string")
        value = float(number)
        if not math.isfinite(value) or (number != 0 and value == 0):
            raise ValueError("Unrepresentable balance")
        method["balance"] = value
        warnings.append({"code": "numeric_string_balance", "path": f"payment_methods[{index}].balance",
                         "original_value": raw})
    return result, warnings


def _read(client_api, operation, method, path, *, body=None):
    try:
        if body is None:
            response = client_api.request(method, path)
        else:
            response = client_api.request(method, path, body=body)
    except Exception:
        raise ReadError("read_transport_failure", operation=operation) from None
    status = response.status_code
    if status != 200:
        error = response.body.get("error") if isinstance(response.body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        raise ReadError("read_http_failure", operation=operation, http_status=status,
                        api_code=code if isinstance(code, str) and code in _ERROR_CODES else None)
    try:
        if not isinstance(response.body, dict):
            raise ReadError("invalid_read_response", operation=operation)
        body, warnings = (_customer_balance_compatibility(response.body)
                          if operation == "getCustomer" else (deepcopy(response.body), []))
        READ_MODELS[operation].model_validate(body)
    except (ValidationError, ValueError, OverflowError, InvalidOperation):
        raise ReadError("invalid_read_response", operation=operation) from None
    if warnings:
        body["response_warnings"] = warnings
    return body


def search_customer(client_api, selector):
    return _read(client_api, "searchCustomers", "POST", "/v1/customers/search", body=selector)


def read_customer(client_api, customer_id):
    customer_id = resource_id(customer_id)
    record = _read(client_api, "getCustomer", "GET", f"/v1/customers/{quote(customer_id, safe='')}")
    if record["customer_id"] != customer_id:
        raise ReadError("invalid_read_response", operation="getCustomer")
    return record


def read_order(client_api, order_id):
    order_id = resource_id(order_id)
    record = _read(client_api, "getOrder", "GET", f"/v1/orders/{quote(order_id, safe='')}")
    if record["order_id"] != order_id:
        raise ReadError("invalid_read_response", operation="getOrder")
    return record


def read_products(client_api):
    return _read(client_api, "listProducts", "GET", "/v1/catalog/products")


def read_product(client_api, product_id):
    product_id = resource_id(product_id)
    record = _read(client_api, "getProduct", "GET", f"/v1/catalog/products/{quote(product_id, safe='')}")
    if record["product_id"] != product_id:
        raise ReadError("invalid_read_response", operation="getProduct")
    return record


def read_catalog_item(client_api, item_id):
    item_id = resource_id(item_id)
    record = _read(client_api, "getCatalogItem", "GET", f"/v1/catalog/items/{quote(item_id, safe='')}")
    if record["item_id"] != item_id:
        raise ReadError("invalid_read_response", operation="getCatalogItem")
    return record
