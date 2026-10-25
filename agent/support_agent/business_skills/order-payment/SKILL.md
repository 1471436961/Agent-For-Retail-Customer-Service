---
name: order-payment
description: Replace a pending order's entire payment method with one different saved method.
metadata: {"requires":"service-workflow","operations":"payment"}
---

Only an owned pending order supports payment replacement. Select one different
already saved method for the whole charge; no adding instruments or split payment.
A gift card must cover the entire order total, not just an item upgrade difference.
If several eligible methods remain, ask; if one qualifies and no different method
was requested, propose it in the complete recap rather than another selector step.

Explain both full legs: charge the new method for the order total, then refund
the old charge in full. A declined replacement leaves the original unchanged.
Successful replacement leaves order status pending. Original card/PayPal refunds
take 3–6 working days; gift card refunds are immediate. These timings do not govern
delivered return/exchange settlement.

Use change_order_payment(order_id,payment_method_id) after the exact recap and
approval. Inspect the receipt for actual effects. Perform approved address/payment
changes before a one-time item update, which locks the order's pending status.

Sources: process_map_05.png; email_09.eml 2026-05-06 (charge before refund, decline);
email_08.eml 2026-05-12 (unchanged pending status); email_07.eml 2026-05-08 (timing);
system_export.zip/policy_contracts.json CONTRACT-1001.
