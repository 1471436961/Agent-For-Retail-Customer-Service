"""Seven explicit preparation contracts; no remote writes and no language parsing."""
from collections import Counter
from copy import deepcopy
from pydantic import ValidationError
from support_agent.adapters.read_models import Address
from support_agent.adapters.change_parameters import ReplacementChoice
from support_agent.domain.identity import ReadError, resource_id
from support_agent.domain.money import calculate_values
from support_agent.domain.policy import service_policy

KINDS = ("default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange")


def require(condition, code):
    if not condition:
        raise ReadError(code)


def prepare(kit, kind, params):
    require(kind in KINDS, "unsupported_change")
    allowed = {
        "default_address": {"address"}, "order_address": {"order_id", "address"},
        "cancel": {"order_id", "reason"}, "payment": {"order_id", "payment_method_id"},
        "modify_items": {"order_id", "replacements", "payment_method_id", "preserve_options", "specific_instance"},
        "return": {"order_id", "item_ids", "refund_payment_method_id", "specific_instance"},
        "exchange": {"order_id", "replacements", "payment_method_id", "preserve_options", "specific_instance"},
    }[kind]
    require(not (set(params) - allowed), "unexpected_change_parameter")
    if "specific_instance" in params:
        require(type(params["specific_instance"]) is bool, "invalid_instance_selection")
    profile = kit._profile()
    order = kit.get_owned_order(resource_id(params.get("order_id", ""))) if kind != "default_address" else None
    if order:
        require(order["status"] == ("delivered" if kind in {"return", "exchange"} else "pending"), "ineligible_order_status")
    body, calculations, catalog = {}, [], []
    if kind in {"default_address", "order_address"}:
        try:
            address = Address.model_validate(params.get("address"))
        except ValidationError:
            raise ReadError("incomplete_or_invalid_address") from None
        body = address.model_dump(exclude_unset=True)
        require(all(str(body[field]).strip() for field in ("address_line_1", "city", "region", "country", "postal_code")), "incomplete_or_invalid_address")
    elif kind == "cancel":
        require(isinstance(params.get("reason"), str) and
                params["reason"] in {"no longer needed", "ordered by mistake"}, "unsupported_cancellation_reason")
        body = {"reason": params["reason"]}
        # Keep every original charge separate; never refund the net payment sum.
        calculations = [{"operation": "original_charge", "inputs": [p["amount"]],
                         "result": p["amount"], "payment_method_id": p["payment_method_id"]}
                        for p in order["payments"] if p["transaction_type"] == "payment"]
    elif kind == "payment":
        charges = order["payments"]
        require(len(charges) == 1 and charges[0]["transaction_type"] == "payment", "payment_history_not_replaceable")
        method = _method(profile, params.get("payment_method_id"))
        require(method["id"] != charges[0]["payment_method_id"], "unchanged_payment_method")
        calculation = calculate_values("sum", [charges[0]["amount"]])
        _balance(method, calculation["result"])
        body = {"payment_method_id": method["id"]}
        calculations = [calculation, {"operation": "original_charge_refund", **charges[0]}]
    elif kind == "return":
        ids = params.get("item_ids")
        _occurrences(order, ids, params.get("specific_instance", False))
        method = _method(profile, params.get("refund_payment_method_id"))
        originals = {p["payment_method_id"] for p in order["payments"] if p["transaction_type"] == "payment"}
        require(method["id"] in originals or (method["source"] == "gift_card" and method["id"] in kit._opening_gift_ids), "ineligible_refund_destination")
        remaining = deepcopy(order["items"])
        prices = []
        for identifier in ids:
            index = next(i for i, item in enumerate(remaining) if item["item_id"] == identifier)
            prices.append(remaining.pop(index)["price"])
        calculations = [calculate_values("sum", prices)]
        body = {"item_ids": ids, "refund_payment_method_id": method["id"]}
    else:
        rows = params.get("replacements")
        require(isinstance(rows, list) and bool(rows), "complete_replacement_list_required")
        try:
            choices = [ReplacementChoice.model_validate(row).model_dump() for row in rows]
        except ValidationError:
            raise ReadError("invalid_replacements") from None
        rows = [{key: choice[key] for key in ("existing_item_id", "replacement_item_id")} for choice in choices]
        _occurrences(order, [r["existing_item_id"] for r in rows], params.get("specific_instance", False))
        preserve = params.get("preserve_options", [])
        require(isinstance(preserve, list) and all(isinstance(k, str) for k in preserve), "invalid_preserved_options")
        originals = [next(item for item in order["items"] if item["item_id"] == row["existing_item_id"]) for row in rows]
        # A shared list is a union across different products, not a demand that
        # every product expose every key. Still reject an entirely unknown key.
        require(all(any(key in item["options"] for item in originals) for key in preserve), "required_option_changed")
        values = []
        for row, choice, old in zip(rows, choices, originals):
            product = kit.get_product_variants(old["product_id"])
            target = next((i for i in product["items"] if i["item_id"] == row["replacement_item_id"]), None)
            require(target is not None and target["available"], "replacement_not_same_product_or_unavailable")
            # Backend permissiveness is not business permission: both pending
            # modification and delivered exchange require a different option.
            require(target["item_id"] != old["item_id"] and target["options"] != old["options"], "unchanged_item")
            required = [key for key in preserve if key in old["options"]] + choice["preserve_options"]
            require(all(key in old["options"] and target["options"].get(key) == old["options"][key]
                        for key in required), "required_option_changed")
            values.extend([target["price"], old["price"]])
            catalog.append({"product_id": product["product_id"], "target": target})
        calculations = [calculate_values("price_difference", values)]
        calculations.extend([
            {"purpose": "replacement_total", **calculate_values("sum", values[::2])},
            {"purpose": "original_total", **calculate_values("sum", values[1::2])},
        ])
        calculations.extend({"purpose": "replacement_row", "row_index": index,
                             **calculate_values("price_difference", values[index * 2:index * 2 + 2])}
                            for index in range(len(rows)))
        method = _method(profile, params.get("payment_method_id"))
        _balance(method, calculations[0]["result"])
        body = {"replacements": rows, "payment_method_id": method["id"]}
    return {"kind": kind, "customer_id": profile["customer_id"],
            "order_id": order["order_id"] if order else None, "body": deepcopy(body),
            "calculations": calculations, "snapshot": {"profile": profile, "order": order, "catalog": catalog},
            "instance_selection": "ID-only; exact instance unavailable" if params.get("specific_instance") else "ordered ID occurrences",
            "application": "draft only; not submitted; no business record or funds changed",
            "policy": service_policy(kind)}


def _method(profile, identifier):
    resource_id(identifier)
    found = [m for m in profile["payment_methods"] if m["id"] == identifier]
    require(len(found) == 1, "saved_payment_method_required")
    return found[0]


def execution_evidence(facts):
    """Compare the confirmed operation, excluding unrelated profile changes.

    prepare() rechecks identity, ownership, eligibility and sufficient funds on
    every execution. This projection additionally binds relevant recorded facts
    and quoted amounts to the approved version, without treating the customer's
    entire account as an atomic snapshot.
    """
    kind = facts["kind"]
    evidence = {key: deepcopy(facts[key]) for key in
                ("kind", "customer_id", "order_id", "body", "calculations", "instance_selection")}
    profile, order = facts["snapshot"]["profile"], facts["snapshot"]["order"]
    if kind == "default_address":
        evidence["previous_address"] = profile["default_shipping_address"]
    else:
        evidence["status"] = order["status"]
        if kind in {"order_address", "exchange"}:
            evidence["previous_address"] = order["shipping_address"]
        if kind in {"cancel", "modify_items", "return", "exchange"}:
            evidence["items"] = order["items"]
        if kind in {"cancel", "payment", "modify_items", "return", "exchange"}:
            evidence["payments"] = order["payments"]
    method_ids = {facts["body"][key] for key in ("payment_method_id", "refund_payment_method_id")
                  if key in facts["body"]}
    method_ids.update(row["payment_method_id"] for row in facts["calculations"]
                      if "payment_method_id" in row)
    evidence["methods"] = {method["id"]: {key: value for key, value in method.items() if key != "balance"}
                           for method in profile["payment_methods"] if method["id"] in method_ids}
    evidence["catalog"] = facts["snapshot"]["catalog"]
    return evidence


def _balance(method, amount):
    require(method["source"] != "gift_card" or method["balance"] >= max(amount, 0), "insufficient_gift_balance")


def _occurrences(order, identifiers, specific):
    require(isinstance(identifiers, list) and bool(identifiers) and all(isinstance(i, str) and i.strip() for i in identifiers), "complete_item_list_required")
    available = Counter(item["item_id"] for item in order["items"])
    require(not (Counter(identifiers) - available), "item_quantity_exceeds_order")
    if specific:
        require(all(available[i] == 1 for i in identifiers), "exact_instance_not_addressable")
