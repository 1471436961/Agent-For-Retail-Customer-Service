"""Present verified change facts, not customer-language parsing or consent inference."""
from copy import deepcopy
import json
from support_agent.domain.policy import service_policy


def proposal_view(proposal):
    """Keep the requested changes, without repeating unrelated profile/order data."""
    facts = proposal["facts"]
    profile, order = facts["snapshot"]["profile"], facts["snapshot"]["order"]
    body = facts["body"]
    view = {"proposal_id": proposal["proposal_id"], "state": proposal["state"],
            "kind": facts["kind"], "order_id": facts["order_id"],
            "body": deepcopy(body), "calculations": deepcopy(facts["calculations"]),
            "policy": deepcopy(facts.get("policy", service_policy(facts["kind"])))}
    method_id = body.get("payment_method_id", body.get("refund_payment_method_id"))
    if method_id:
        view["settlement_method"] = next(
            {key: value for key, value in method.items() if key != "balance"}
            for method in profile["payment_methods"] if method["id"] == method_id)
    if facts["kind"] in {"modify_items", "exchange", "return"}:
        remaining = deepcopy(order["items"])
        ids = body.get("item_ids", [row["existing_item_id"] for row in body.get("replacements", [])])
        selected = []
        for index, identifier in enumerate(ids):
            position = next(i for i, item in enumerate(remaining) if item["item_id"] == identifier)
            row = {"original": remaining.pop(position)}
            if facts["kind"] != "return":
                row["replacement"] = deepcopy(facts["snapshot"]["catalog"][index]["target"])
            selected.append(row)
        view["selected_items"] = selected
    return view


def money(value):
    # Tool facts have already passed monetary validation; SDK messages can
    # serialize valid numbers to decimal strings. This formats those facts only.
    return f"{float(value):.2f}"


def method_text(method):
    label = {"credit_card": "Card", "paypal": "PayPal", "gift_card": "Gift card"}[method["source"]]
    if method.get("brand"):
        label += " " + method["brand"]
    if method.get("last_four"):
        label += " ending " + method["last_four"]
    return label + " (" + method["id"] + ")"


def item_text(item, name=None):
    options = ", ".join(f"{key}: {value}" for key, value in item["options"].items())
    return f"{name or item.get('name', 'Item')} [{item['item_id']}] — {options}; price {money(item['price'])}"


def review_text(proposal):
    """Legacy factual recap, retained for historical fixtures and compatibility.

    Live Agent replies are model-owned natural language; this formatter is not
    appended to them or used to classify whether the customer gave consent.
    """
    view = proposal_view(proposal)
    kind, body = view["kind"], view["body"]
    labels = {"default_address": "Default address", "order_address": "Order shipping address",
              "cancel": "Whole-order cancellation", "payment": "Order payment replacement",
              "modify_items": "Pending-order item changes", "return": "Return request", "exchange": "Exchange request"}
    lines = [f"Prepared change {view['proposal_id']} — {labels[kind]}"]
    if view["order_id"]:
        lines.append("Order: " + view["order_id"])
    if kind in {"default_address", "order_address"}:
        lines.append("New address: " + ", ".join(str(value) for value in body.values() if value))
    elif kind == "cancel":
        lines.append("Reason: " + body["reason"])
        methods = {method["id"]: method for method in proposal["facts"]["snapshot"]["profile"]["payment_methods"]}
        for payment in view["calculations"]:
            method = methods.get(payment["payment_method_id"])
            source = method["source"] if method else None
            timing = "immediate" if source == "gift_card" else "3–6 working days" if source in {"credit_card", "paypal"} else "timing not specified"
            lines.append(f"Original-charge refund: {money(payment['result'])} to {method_text(method) if method else payment['payment_method_id']} ({timing}).")
    elif kind == "payment":
        lines.append("New full-order charge: " + money(view["calculations"][0]["result"]))
        for charge in proposal["facts"]["snapshot"]["order"]["payments"]:
            lines.append(f"Refund original charge: {money(charge['amount'])} to {charge['payment_method_id']}.")
    else:
        for index, row in enumerate(view["selected_items"], 1):
            lines.append(f"{index}. " + item_text(row["original"]))
            if "replacement" in row:
                lines.append("   → " + item_text(row["replacement"], row["original"]["name"]))
        calculation = view["calculations"][0]["result"]
        if kind == "return":
            lines.append("Return amount based on original sale prices: " + money(calculation))
        else:
            direction = "refund difference" if float(calculation) < 0 else "additional payment" if float(calculation) > 0 else "no difference"
            lines.append(f"Price difference (new minus original): {money(calculation)} ({direction}).")
    if "settlement_method" in view:
        lines.append("Settlement method: " + method_text(view["settlement_method"]))
    lines.append("Not submitted. No business record or funds have changed from preparing this proposal.")
    if kind in {"return", "exchange"}:
        lines.append("Submission registers a request; it does not mean a refund has arrived or replacement items have shipped.")
    ending = "Please confirm this exact change."
    if kind in {"modify_items", "return", "exchange"}:
        ending += " Is this the complete list, or do you need any other item changes before this one-time request?"
    return "\n".join([*lines, ending])


def legacy_review_text(proposal):
    """Recognize previously emitted recaps during history replay only."""
    facts = proposal["facts"]
    recap = {key: value for key, value in facts.items() if key != "snapshot"}
    if facts["kind"] in {"modify_items", "return", "exchange"}:
        recap["original_items"] = facts["snapshot"]["order"]["items"]
        recap["replacement_details"] = facts["snapshot"]["catalog"]
    ending = "Please confirm this exact change."
    if facts["kind"] in {"modify_items", "return", "exchange"}:
        ending += " Is this the complete list, or do you need any other item changes before this one-time request?"
    return "Prepared change " + proposal["proposal_id"] + ":\n" + json.dumps(recap, ensure_ascii=False, indent=2) + "\n" + ending
