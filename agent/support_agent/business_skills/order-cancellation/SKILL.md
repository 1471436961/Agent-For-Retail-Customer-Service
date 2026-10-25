---
name: order-cancellation
description: Cancel a whole pending order or explain limits and the value of a requested subset.
metadata: {"requires":"service-workflow,catalog-selection","operations":"cancel"}
---

Cancel only a whole owned pending order. No partial cancellation, removal of
quantities, restoration, or cancellation after item modification. Never broaden
a partial request into whole-order cancellation without approval. Keep it intact
if the customer chooses that alternative and continue their remaining goals.

Identify and calculate a requested financial subset's recorded value even when
partial cancellation is unsupported; distinguish value from an eligible refund.
Obtain a canonical reason: no longer needed or ordered by mistake. Interpret
clear synonyms; clarify other reasons rather than relabel a complaint.

Refund each original charge/top-up in full to its own original method; no netting,
redirecting, fees or invented refund endpoint. Proactively explain cancellation
arrival timing: card/PayPal 3-6 working days, gift card immediate. This timing
does not apply to delivered returns/exchanges. After approval use cancel_order.

Sources: workspace_export.json CR8; email_06.eml; email_11.eml 2026-04-16
(separate refunds replace netting); email_12.eml 2026-04-24 (timing);
system_export.zip CONTRACT-1001/1002; INTERFACE-CLARIFICATIONS.md U6.
