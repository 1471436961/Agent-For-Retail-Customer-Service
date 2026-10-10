"""One explicit business request, no retry; validate receipts before claiming success."""
from copy import deepcopy
from urllib.parse import quote
from pydantic import ValidationError
from support_agent.adapters.read_models import Record, Address, OrderCancellation, OrderPayment, OrderItem, OrderReturn, OrderExchange
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


class PaymentResult(Record):
    order_id: str
    payments: list[OrderPayment]


class ItemModificationResult(Record):
    order_id: str
    status: Literal["pending (items modified)"]
    items: list[OrderItem]
    payments: list[OrderPayment]


class ReturnResult(Record):
    order_id: str
    status: Literal["return requested"]
    return_request: OrderReturn


class ExchangeResult(Record):
    order_id: str
    status: Literal["exchange requested"]
    exchange: OrderExchange


OPERATIONS = {
    "default_address": ("replaceCustomerDefaultShippingAddress", "PUT", "/v1/customers/{}/default-shipping-address", DefaultAddressResult),
    "order_address": ("replaceOrderShippingAddress", "PUT", "/v1/orders/{}/shipping-address", OrderAddressResult),
    "cancel": ("createOrderCancellation", "POST", "/v1/orders/{}/cancellations", CancellationResult),
    "payment": ("replaceOrderPaymentMethod", "PUT", "/v1/orders/{}/payment-method", PaymentResult),
    "modify_items": ("createOrderItemModification", "POST", "/v1/orders/{}/item-modifications", ItemModificationResult),
    "return": ("createOrderReturn", "POST", "/v1/orders/{}/returns", ReturnResult),
    "exchange": ("createOrderExchange", "POST", "/v1/orders/{}/exchanges", ExchangeResult),
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
        validate_financial_receipt(facts, actual)
    except (ValueError, TypeError, KeyError, ValidationError):
        return {"state": "unknown", "operation": operation, "error": "invalid_write_receipt"}
    return {"state": "accepted" if facts["kind"] in {"return", "exchange"} else "completed", "operation": operation, "receipt": deepcopy(actual)}


def validate_financial_receipt(facts, actual):
    kind, body = facts["kind"], facts["body"]
    order = facts["snapshot"]["order"]
    if kind in {"cancel", "payment", "modify_items"}:
        expected = deepcopy(order["payments"])
        if kind == "cancel":
            expected.extend({**p, "transaction_type": "refund"} for p in order["payments"] if p["transaction_type"] == "payment")
        elif kind == "payment":
            original = order["payments"][0]
            expected.extend([{"transaction_type": "payment", "amount": original["amount"], "payment_method_id": body["payment_method_id"]},
                             {**original, "transaction_type": "refund"}])
        else:
            delta = facts["calculations"][0]["result"]
            expected.append({"transaction_type": "payment" if delta > 0 else "refund", "amount": abs(delta), "payment_method_id": body["payment_method_id"]})
        if actual["payments"] != expected:
            raise ValueError("Mismatched financial receipt")
    if kind == "modify_items":
        items = deepcopy(order["items"])
        for row, catalog in zip(body["replacements"], facts["snapshot"]["catalog"]):
            old = next(i for i in items if i["item_id"] == row["existing_item_id"])
            new = catalog["target"]
            old.update(item_id=new["item_id"], price=new["price"], options=new["options"])
        if actual["items"] != items:
            raise ValueError("Mismatched item receipt")
    if kind == "return" and actual["return_request"] != body:
        raise ValueError("Mismatched return receipt")
    if kind == "exchange" and actual["exchange"] != {**body, "price_difference": facts["calculations"][0]["result"]}:
        raise ValueError("Mismatched exchange receipt")
