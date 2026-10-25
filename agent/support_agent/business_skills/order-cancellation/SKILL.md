---
name: order-cancellation
description: Cancel a whole pending order or explain limits and the value of a requested subset.
metadata: {"requires":"service-workflow,catalog-selection","operations":"cancel"}
---

Only a whole owned pending order can be cancelled. No partial cancellation or
quantity removal, restoration of cancelled orders, or cancellation after item
modification. processed orders cannot be changed; after delivery eligible item
returns/exchanges are separate operations. Never expand a partial request into a
whole-order cancellation without approval. If the customer chooses to keep it,
leave the order unchanged and continue any remaining goals.

Identify the requested item subset and calculate its recorded subtotal before
explaining financial alternatives, even when partial cancellation is unsupported.
State it as selected value, not a refund that can currently be submitted. Do not
substitute the whole-order total or promise a future eligible return as completed.

Obtain a reason: the accepted canonical values are no longer needed and ordered
by mistake. Interpret clear synonyms semantically; clarify other reasons rather
than relabel a complaint to make it eligible.

Cancellation refunds every original charge/top-up separately, in full to its own
original method. Do not net entries, redirect refunds, withhold fees or invent a
separate refund endpoint. Explain the applicable arrival timing proactively:
card/PayPal 3–6 working days, gift card immediately. This timing is specific to
cancellation and payment replacement; it does not establish return/exchange timing.

After the complete recap and approval use cancel_order(order_id,reason), then
describe the actual cancellation receipt. If unavailable, explain supported
options; load human-handoff if escalation is requested or policy has a real gap.

Sources: workspace_export.json CR8 (final two reasons); email_06.eml (whole charge);
email_11.eml 2026-04-16 (separate original refunds supersede netting);
email_12.eml 2026-04-24 (3–6 supersedes 6–9 working days);
system_export.zip/policy_contracts.json CONTRACT-1001/1002; INTERFACE-CLARIFICATIONS.md U6.
