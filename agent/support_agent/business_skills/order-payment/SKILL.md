---
name: order-payment
description: Replace a pending order's entire payment method with one different saved method.
metadata: {"requires":"service-workflow","operations":"payment"}
---

Only a pending owned order allows replacement of the whole payment. Choose one
different saved method; no new instruments or split payment. A gift card must
cover the entire order total, not an upgrade difference. Ask if several eligible
choices remain; a sole method can be proposed in the complete recap.

Explain both full legs: charge the new method, then refund the original charge
in full. A declined replacement leaves the original unchanged. Success leaves
status pending. Original card/PayPal refunds take 3-6 working days, gift card is
immediate; this does not establish return/exchange timing.

After approval use change_order_payment. Finish approved payment/address changes
before a one-time item update, which locks further order actions. Verify receipts.

Sources: process_map_05.png; email_09.eml 2026-05-06 (sequence/decline);
email_08.eml 2026-05-12 (status); email_07.eml 2026-05-08 (timing).
