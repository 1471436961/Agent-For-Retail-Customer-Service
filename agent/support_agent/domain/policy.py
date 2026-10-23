"""Operation-specific instructions compiled from the supplied, applicable materials.

These are business policy facts for the model, never phrase matching or a
case-specific answer. References identify the supplied sources, not extra APIs.
"""
from copy import deepcopy


def service_policy(operation):
    common = {
        "confirmation": "Explain the complete relevant change and obtain clear consent referring to that change before writing. Questions, pauses and unmet conditions are not permission. Preparation is not submission.",
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
        "exchange": {"eligibility": "Delivered owned order; same-product available replacements. Gather and confirm the complete list before the single submission. No second pass to append items.",
                     "settlement": "One saved method pays or receives the difference; it need not be the original payment. A gift card must cover the positive difference.",
                     "refund_timing": "No refund-arrival timeframe is specified in the supplied exchange policy or receipt. Do not apply cancellation/payment-replacement timing to exchanges, even for the same card or PayPal.",
                     "process": "Submission registers exchange requested. Instructions for sending originals back are provided by email. The exchange itself establishes the replacement path, so no separate purchase is required. A request is not completed settlement or shipping.",
                     "unspecified": "Do not promise a prepaid label, carrier, return deadline, refund-arrival date or replacement delivery date. The available API does not establish these details or email delivery.",
                     "sources": ["workspace_export.json RD5", "case_file_03.md", "case_file_41.md", "case_file_40.md", "INTERFACE-CLARIFICATIONS.md U5–U6"]},
    }
    policy = deepcopy(policies[operation])
    return {"operation": operation, "confirmation": common["confirmation"],
            **policy, "sources": [*common["sources"], *policy["sources"]]}
