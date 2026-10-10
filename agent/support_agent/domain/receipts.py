"""Necessary customer facts derived from actual receipts, never predicted success."""


def customer_facts(facts, outcome):
    summary = {"kind": facts["kind"], "state": outcome["state"], "order_id": facts["order_id"]}
    if outcome["state"] not in {"completed", "accepted"}:
        summary["notice"] = "No successful change is confirmed. Do not retry an unknown write."
        return summary
    kind = facts["kind"]
    methods = {m["id"]: m for m in facts["snapshot"]["profile"]["payment_methods"]}
    if kind in {"cancel", "payment"}:
        original = [p for p in facts["snapshot"]["order"]["payments"] if p["transaction_type"] == "payment"]
        summary["original_charge_refunds"] = []
        for p in original:
            source = methods.get(p["payment_method_id"], {}).get("source")
            timing = "immediate" if source == "gift_card" else "3–6 working days" if source in {"credit_card", "paypal"} else "source unknown; no timing promised"
            summary["original_charge_refunds"].append({"amount": p["amount"], "payment_method_id": p["payment_method_id"], "source": source, "timing": timing})
        summary["notice"] = "Refund processing does not prove card or PayPal funds have arrived."
    elif kind == "return":
        summary.update(estimated_original_sale_refund=facts["calculations"][0]["result"], destination=facts["body"]["refund_payment_method_id"], notice="Return application accepted; no refund arrival or timing confirmed.")
    elif kind == "exchange":
        summary.update(price_difference=outcome["receipt"]["exchange"]["price_difference"], settlement_method=facts["body"]["payment_method_id"], notice="Exchange application accepted; no settlement or shipment completion confirmed.")
    elif kind == "modify_items":
        summary.update(price_difference=facts["calculations"][0]["result"], settlement_method=facts["body"]["payment_method_id"], status=outcome["receipt"]["status"])
    return summary
