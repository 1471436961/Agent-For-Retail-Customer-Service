"""One explicit business request, no retry; validate receipts before claiming success."""
from copy import deepcopy
from urllib.parse import quote
from pydantic import ValidationError
from support_agent.adapters.read_models import Record, Address, OrderCancellation, OrderPayment
from typing import Literal


class DefaultAddressResult(Record):
    customer_id: str
    default_shipping_address: Address


class OrderAddressResult(Record):
    order_id: str
    shipping_address: Address


class CancellationResult(Record):
    order_id: str
    status: Literal["cancelled"]
    cancellation: OrderCancellation
    payments: list[OrderPayment]


OPERATIONS = {
    "default_address": ("replaceCustomerDefaultShippingAddress", "PUT", "/v1/customers/{}/default-shipping-address", DefaultAddressResult),
    "order_address": ("replaceOrderShippingAddress", "PUT", "/v1/orders/{}/shipping-address", OrderAddressResult),
    "cancel": ("createOrderCancellation", "POST", "/v1/orders/{}/cancellations", CancellationResult),
}


def write_once(api, facts):
    operation, method, template, model = OPERATIONS[facts["kind"]]
    identifier = facts["customer_id"] if facts["kind"] == "default_address" else facts["order_id"]
    try:
        response = api.request(method, template.format(quote(identifier, safe="")), body=deepcopy(facts["body"]))
    except Exception:
        return {"state": "unknown", "operation": operation, "error": "write_transport_failure"}
    if response.status_code != 200:
        # Capacity/structure/business rejections are definite failures. Server or
        # unexpected statuses may have committed; never assume a safe retry.
        return {"state": "failed" if response.status_code in {400, 404, 405, 409, 413, 422} else "unknown",
                "operation": operation, "http_status": response.status_code, "error": "write_http_failure"}
    try:
        model.model_validate(response.body)
        actual = response.body
        key = "customer_id" if facts["kind"] == "default_address" else "order_id"
        if actual[key] != identifier:
            raise ValueError("Mismatched receipt target")
        if facts["kind"] in {"default_address", "order_address"}:
            key = "default_shipping_address" if facts["kind"] == "default_address" else "shipping_address"
            if Address.model_validate(actual[key]) != Address.model_validate(facts["body"]):
                raise ValueError("Mismatched receipt address")
        if facts["kind"] == "cancel" and actual["cancellation"] != facts["body"]:
            raise ValueError("Mismatched receipt reason")
    except (ValueError, TypeError, KeyError, ValidationError):
        return {"state": "unknown", "operation": operation, "error": "invalid_write_receipt"}
    return {"state": "completed", "operation": operation, "receipt": deepcopy(actual)}
