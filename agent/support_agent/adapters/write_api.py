"""One explicit business request, no retry; validate receipts before claiming success."""
from copy import deepcopy
from collections import Counter
from urllib.parse import quote
from pydantic import ValidationError
from support_agent.adapters.read_models import Record, Address, OrderCancellation, OrderPayment, OrderItem, OrderReturn, OrderExchange
from typing import Literal
from support_agent.adapters.response_values import normalize_response, ResponseValueError, validation_issues
from support_agent.adapters.workflow_api import accepted_workflow, poll_workflow


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
    operation, method, template, _ = OPERATIONS[facts["kind"]]
    identifier = facts["customer_id"] if facts["kind"] == "default_address" else facts["order_id"]
    try:
        response = api.request(method, template.format(quote(identifier, safe="")), body=deepcopy(facts["body"]))
    except Exception:
        return {"state": "unknown", "operation": operation, "error": "write_transport_failure"}
    if response.status_code == 202:
        try:
            workflow = accepted_workflow(response, facts)
        except (ValueError, TypeError, OverflowError):
            return {"state": "unknown", "operation": operation, "http_status": 202,
                    "error": "invalid_async_write_receipt"}
        return resume_workflow(api, facts, workflow)
    if response.status_code != 200:
        # Capacity/structure/business rejections are definite failures. Server or
        # unexpected statuses may have committed; never assume a safe retry.
        return {"state": "failed" if response.status_code in {400, 404, 405, 409, 413, 422} else "unknown",
                "operation": operation, "http_status": response.status_code, "error": "write_http_failure"}
    return validate_receipt(facts, response.body)


def resume_workflow(api, facts, workflow):
    progress = poll_workflow(api, workflow)
    operation = OPERATIONS[facts["kind"]][0]
    if progress["state"] == "succeeded":
        outcome = validate_receipt(facts, progress["result"])
        outcome["workflow"] = progress["workflow"]
        outcome["workflow_status"] = "succeeded"
        return outcome
    return {"operation": operation, **progress}


def validate_receipt(facts, body):
    """Validate the same target, amounts and items for sync/async/read-back results."""
    operation, _, _, model = OPERATIONS[facts["kind"]]
    identifier = facts["customer_id"] if facts["kind"] == "default_address" else facts["order_id"]
    try:
        actual, warnings = normalize_response(body)
        model.model_validate(actual)
        key = "customer_id" if facts["kind"] == "default_address" else "order_id"
        if actual[key] != identifier:
            raise ValueError("Mismatched receipt target")
        if facts["kind"] in {"default_address", "order_address"}:
            key = "default_shipping_address" if facts["kind"] == "default_address" else "shipping_address"
            if canonical_address(actual[key]) != canonical_address(facts["body"]):
                raise ValueError("Mismatched receipt address")
        if facts["kind"] == "cancel" and actual["cancellation"] != facts["body"]:
            raise ValueError("Mismatched receipt reason")
        warnings.extend(validate_financial_receipt(facts, actual))
    except ValidationError as error:
        return {"state": "unknown", "operation": operation, "error": "invalid_write_receipt",
                "validation_issues": validation_issues(error, model)}
    except ResponseValueError as error:
        return {"state": "unknown", "operation": operation, "error": "invalid_write_receipt",
                "validation_issues": [{"path": error.path, "type": "invalid_serialized_scalar"}]}
    except (ValueError, TypeError, KeyError):
        return {"state": "unknown", "operation": operation, "error": "invalid_write_receipt"}
    outcome = {"state": "accepted" if facts["kind"] in {"return", "exchange"} else "completed", "operation": operation, "receipt": deepcopy(actual)}
    if warnings:
        outcome["response_warnings"] = warnings
    return outcome


def observed_receipt(facts, record):
    """Check current business state, retaining uncertainty if it does not match.

    This confirms only the observed matching record, not which actor wrote it.
    Extra read-only order fields are not mistaken for write-response fields.
    """
    model = OPERATIONS[facts["kind"]][3]
    projected = {key: deepcopy(record[key]) for key in model.model_fields if key in record}
    outcome = validate_receipt(facts, projected)
    if outcome["state"] in {"accepted", "completed"}:
        outcome["verification"] = "observed_matching_business_record"
    return outcome


def canonical_address(value):
    """Match the REST adapter's optional second-line normalization only.

    The reference API converts missing/null address_line_2 to legacy address2
    "" and returns that empty string. Every other field and any nonempty second
    line must still match exactly; no address guessing or general normalization.
    """
    address = Address.model_validate(value).model_dump()
    address["address_line_2"] = address["address_line_2"] or ""
    return address


def validate_financial_receipt(facts, actual):
    warnings = []
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
    if kind == "return":
        receipt = actual["return_request"]
        if (receipt["refund_payment_method_id"] != body["refund_payment_method_id"] or
                Counter(receipt["item_ids"]) != Counter(body["item_ids"])):
            raise ValueError("Mismatched return receipt")
        if receipt["item_ids"] != body["item_ids"]:
            warnings.append({"code": "receipt_item_order_changed", "path": "return_request.item_ids"})
    if kind == "exchange":
        receipt = actual["exchange"]
        if (receipt["payment_method_id"] != body["payment_method_id"] or
                receipt["price_difference"] != facts["calculations"][0]["result"]):
            raise ValueError("Mismatched exchange receipt")
        pairs = lambda rows: Counter((row["existing_item_id"], row["replacement_item_id"]) for row in rows)
        if pairs(receipt["replacements"]) != pairs(body["replacements"]):
            # RetailTools stores old/new lists independently sorted, and the
            # SDK REST adapter zips them back together. Recognize only that
            # exact public reference representation, never arbitrary remapping.
            old_ids = sorted(row["existing_item_id"] for row in body["replacements"])
            new_ids = sorted(row["replacement_item_id"] for row in body["replacements"])
            reference_rows = [{"existing_item_id": old, "replacement_item_id": new}
                              for old, new in zip(old_ids, new_ids)]
            if receipt["replacements"] != reference_rows:
                raise ValueError("Mismatched exchange receipt")
            warnings.append({"code": "reference_exchange_pairing_lost", "path": "exchange.replacements"})
        elif receipt["replacements"] != body["replacements"]:
            warnings.append({"code": "receipt_item_order_changed", "path": "exchange.replacements"})
    return warnings
