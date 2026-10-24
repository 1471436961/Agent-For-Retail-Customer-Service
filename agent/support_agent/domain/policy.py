"""Operation-specific instructions compiled from the supplied, applicable materials.

These are business policy facts for the model, never phrase matching or a
case-specific answer. References identify the supplied sources, not extra APIs.
"""
from copy import deepcopy


def service_policy(operation):
    common = {
        "confirmation": "Explain the complete relevant change and obtain clear consent referring to that change before writing. A clarification question alone, a pause or an unmet condition is not permission. Clear approval to submit may also contain a question about next steps. Read-only preparation needs no approval and is not submission.",
        "sources": ["meeting_transcript_01.vtt", "INTERFACE-CLARIFICATIONS.md U5"],
    }
    policies = {
        "default_address": {"scope": "Change only the verified customer's default shipping address; do not change order addresses without a separate request.", "sources": ["process_map_05.png"]},
        "order_address": {"eligibility": "Only a pending owned order; a profile default change does not change this order.", "sources": ["process_map_05.png"]},
        "cancel": {"eligibility": "Whole pending order only. Permitted reasons: no longer needed or ordered by mistake, interpreted semantically.",
                   "refund": "Refund each original charge separately to its original method. Card/PayPal: 3–6 working days; gift card: immediate. No netting, alternate destination or partial cancellation.",
                   "sources": ["workspace_export.json CR8", "email_06.eml", "email_11.eml", "email_12.eml"]},
        "payment": {"eligibility": "Whole pending order; one different saved method covers the whole charge. Charge the new method and refund the original charge. A declined replacement leaves the original unchanged.",
                    "refund": "Original card/PayPal refund: 3–6 working days; gift card: immediate.",
                    "sources": ["process_map_05.png", "email_09.eml", "email_08.eml", "email_07.eml"]},
        "modify_items": {"eligibility": "Pending owned order; same-product available variants only. Preserve requested unchanged options, duplicate counts and the complete per-order list. No second pass after submission.",
                         "settlement": "One saved method pays or receives the difference. A gift card must cover the positive difference.",
                         "sources": ["process_map_01.png", "INTERFACE-CLARIFICATIONS.md U5–U6"]},
        "return": {"eligibility": "Delivered owned order. Submit the complete selected item list once, preserving duplicate counts.",
                   "refund": "Use original payment or a gift card already saved before the request. Registration is not refund arrival. No refund-arrival timeframe is specified in the supplied return policy or receipt.",
                   "process": "Return instructions are provided by email. Do not claim that an email was sent or delivered unless the actual result establishes that.",
                   "sources": ["workspace_export.json RD5", "case_file_26.md", "case_file_38.md"]},
        "exchange": {"eligibility": "Delivered owned order; same-product available replacements, including an identical available variant for replacing a unit. Return and exchange share the one-time delivered-order opportunity, so choose one operation for that order before writing. Gather and confirm the complete list before the single submission. No second pass to append items.",
                     "settlement": "One saved method pays or receives the difference; it need not be the original payment. A gift card must cover the positive difference.",
                     "refund_timing": "No refund-arrival timeframe is specified in the supplied exchange policy or receipt. Do not apply cancellation/payment-replacement timing to exchanges, even for the same card or PayPal.",
                     "process": "Submission registers exchange requested. Instructions for sending originals back are provided by email. The exchange itself establishes the replacement path, so no separate purchase is required. A request is not completed settlement or shipping.",
                     "unspecified": "Do not promise a prepaid label, carrier, return deadline, refund-arrival date or replacement delivery date. The available API does not establish these details or email delivery.",
                     "sources": ["workspace_export.json RD5", "case_file_03.md", "case_file_41.md", "case_file_40.md", "INTERFACE-CLARIFICATIONS.md U5–U6"]},
    }
    policy = deepcopy(policies[operation])
    return {"operation": operation, "confirmation": common["confirmation"],
            **policy, "sources": [*common["sources"], *policy["sources"]]}


def business_rules():
    """Model-facing rule data, separate from conversation/control instructions.

    These facts compile the supplied current policy, not test IDs or reference
    actions. Operation tool results retain the detailed source citations.
    """
    kinds = ("default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange")
    policies = {kind: service_policy(kind) for kind in kinds}
    return {
        "sources": ["INTERFACE-CLARIFICATIONS.md U3-U6", "meeting_transcript_01.vtt",
                    "meeting_transcript_02.vtt", "meeting_transcript_03.vtt",
                    "meeting_transcript_04.vtt", "meeting_transcript_05.vtt",
                    "workspace_export.json", "process_map_01.png", "process_map_05.png"],
        "identity": "Verify by independently supplied email OR complete first_name, last_name, postal_code. A supplied customer ID is a claim that must match the search result before profile details. It is not authentication. Failure establishes no account existence; request accepted information or suggest website recovery. No invented lockouts or retry-count policy.",
        "scope": "One independently verified customer per conversation, no family/delegated account switching. Only owned orders and saved payment methods. Read actual profile/order/catalog facts; do not invent dates, quantities, missing preferences or hidden record fields. Disclose only requested or necessary customer facts.",
        "confirmation": policies["default_address"]["confirmation"],
        "one_time_lists": "Collect and confirm the full per-order list before item modification, return or exchange. Preserve repeated item IDs as quantities, never deduplicate or append after submission. The API cannot select a specific hidden duplicate-ID line instance.",
        "order_states": {
            "pending": ["order_address", "cancel", "payment", "modify_items"],
            "delivered": ["return", "exchange"],
            "processed": "Read only; not proof of a precise shipping event or date.",
            "pending (items modified)": "No further item modification or cancellation.",
            "cancelled": "Read existing status/payments; do not restore or modify.",
            "return requested": "Explain existing request; do not submit again or append.",
            "exchange requested": "Explain existing request; do not submit again or append.",
            "unknown": "Clarify the actual status before a related write; do not invent a shipped enum.",
        },
        "catalog": "After identity verification, relevant public products need not be in purchase history. Product types, all variants and available variants differ. Count actual available records for available choices. Same-product replacements must be available; preserve attributes the customer did not authorize changing. Item IDs do not encode product relationships. No guessed size scales or incompatible units.",
        "arithmetic": "Use the general calculator for derived arithmetic, or the grounded quote's verified calculations. The model determines the formula and operands from the request and rules. Never quote mental arithmetic. Backend replacement differences accumulate float(new-old) in request order, then Python round(total,2), not per-line rounding or Decimal HALF_UP. A quote is not a write. Verify the actual receipt before claiming any effect.",
        "settlement_choice": "If several saved eligible methods qualify, ask the customer. If only one qualifies and no different method was requested, propose that method in the complete quote; no separate permission to prepare is required. It becomes authorized only with approval of the actual change.",
        "unsupported": ["new purchases", "quantity edits", "split shipments", "email edits",
                        "partial cancellation", "new payment instruments", "arbitrary refunds",
                        "notes, flags or hidden customer fields", "post-submission additions"],
        "handoff": "Only explicit human request, escalation requested after an unsupported-limit explanation, or a real unaddressed policy gap with known facts. Anger/complexity/slowness alone is insufficient. An explicit request needs no extra reason or troubleshooting. Pause unattempted writes unless the customer explicitly requests already-confirmed work first. Use a minimal factual summary. Accepted handoff emits the exact configured notice and ends customer API automation; unknown handoff stops automation without claiming success.",
        "operations": {kind: {key: value for key, value in policy.items()
                               if key not in {"operation", "confirmation", "sources"}}
                       for kind, policy in policies.items()},
    }
