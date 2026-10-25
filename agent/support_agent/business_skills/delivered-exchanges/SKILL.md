---
name: delivered-exchanges
description: Exchange delivered units for available same-product variants and settle the chosen difference.
metadata: {"requires":"service-workflow,catalog-selection","operations":"exchange"}
---

Only an owned delivered order permits exchange. Gather the complete per-order
replacement list before the one-time submission. Return and exchange share that
opportunity: if both are requested on the same order, resolve the customer's
priority first. Never register one and then append the other.

Match every condition and fallback, preserving unchanged options and quantities.
Use actual same-product available variants. An identical available variant can
replace a delivered unit; the pending-modification unchanged-ID restriction does
not apply here. Do not invent an out-of-stock finding to force a different style.

Explain the original/replacement specs and prices and verified net difference.
One already saved method pays or receives the difference; it need not be the
original payment method. This differs from return refund destinations. A gift
card must cover the entire positive difference. If several eligible methods
remain unresolved, ask; a proposed sole method is authorized with the recap's
approval. The chosen settlement method cannot change after submission.

Use request_exchange(order_id,replacements,payment_method_id) after approval.
When the customer changes the list or switches to return, revise scope and recap;
do not reuse approval of the previous proposal. Reuse known row calculations
rather than re-quoting an unchanged retained item.

Acceptance registers exchange requested and establishes the replacement path;
no separate purchase is needed. Instructions for sending originals back are
provided by email. It establishes neither email delivery nor completed settlement,
allocation or shipment. No refund-arrival or replacement-shipping date, carrier,
label, deadline or receiving/inspection prerequisite is specified. Cancellation
and payment-replacement timing cannot be applied to an exchange.

Sources: workspace_export.json RD5 2026-06-18 (exchange settlement exclusion);
meeting_transcript_01.vtt; INTERFACE-CLARIFICATIONS.md U5/U6; case_file_03.md,
case_file_20.md, case_file_40.md and case_file_41.md (approved process/settlement).
Identical-unit availability is checked by the current API; do not infer a new
prohibition from pending-only rules.
