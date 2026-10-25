---
name: delivered-returns
description: Register delivered-item returns with eligible refund destinations and the complete item list.
metadata: {"requires":"service-workflow,catalog-selection","operations":"return"}
---

Only an owned delivered order permits a return. Collect the full selected list
before the single submission; preserve duplicate ID counts. Return and exchange
share the one-time delivered-order opportunity. If both are requested on one
order, explain the limit and resolve the customer's priority before writing.
Separate owned orders can each have their own request.

Determine the refund amount from the selected original sale prices, not current
catalog prices or an unrelated order's total. Eligible destinations are original
payment methods or a gift card already saved before the request. Another saved
card or PayPal cannot receive a refund from an unrelated original instrument.
Ask when multiple eligible choices remain. Do not probe known prohibited routes
or repeat the same rejection for every order. Explain an eligible alternative;
when approved, submit without another unchanged confirmation.

After the complete recap and connected approval use
request_return(order_id,item_ids,refund_payment_method_id), or submit_changes for
multiple explicitly approved orders. A switch away from exchange needs a return
recap; a recorded single-item sale price needs no extra quote/calculation.

An accepted receipt establishes return requested, not refund arrival. Return
instructions are provided by email, but this does not establish email delivery.
No refund-arrival timeframe, prepaid label, carrier, deadline or inspection
condition is specified here. Do not import cancellation/payment-replacement timing.
Customer frustration alone is not a human request; load human-handoff when needed.

Sources: workspace_export.json RD5 2026-06-18 (latest return destination boundary);
meeting_transcript_01.vtt (confirmation/full list); system_export.zip/policy_contracts.json
CONTRACT-1001/1002; INTERFACE-CLARIFICATIONS.md U5/U6; case_file_26.md and
case_file_38.md (approved generic process examples, not evaluation answers).
