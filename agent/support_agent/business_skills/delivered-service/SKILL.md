---
name: delivered-service
description: Shared delivered return/exchange procedure, including scope changes.
metadata: {"requires":"service-workflow,catalog-selection","visibility":"dependency"}
---

Use for delivered-order returns OR exchanges, including customer changes of mind.
They share one service opportunity per order: resolve the priority if both are
requested. Gather and confirm the complete per-order list before the single
submission, preserving duplicate counts. Separate orders have separate requests.

Return: select originals at their recorded sale prices. Refund only to original
payment methods or a gift card saved before the request. An unrelated saved card
or PayPal is ineligible; do not probe known forbidden routes. Explain the eligible
alternative, then submit on its approval without confirming the unchanged plan
again. Ask if several destinations remain unresolved. Use request_return.

Exchange: choose available same-product variants with DIFFERENT options. Stock of
the original variant does not authorize same-spec unit replacement. Explain the
actual policy limit without inventing an out-of-stock claim. Preserve hard
constraints; use every condition/fallback. One saved method pays or receives the
net difference and need not be the original method. Gift balance must cover a
positive difference. The settlement destination cannot change after submission.
Use request_exchange. This refund rule differs from returns.

For a changed scope/operation recap and obtain new connected approval. Reuse
known row differences; returning one original needs only its recorded price.
Accepted means return requested or exchange requested. Instructions for sending
originals back are provided by email; delivery of that email is not established.
Exchange establishes a replacement path; no separate purchase is needed. Refund
arrival, replacement shipment, labels, carrier, deadline and receiving/inspection
conditions are unspecified; do not import cancellation/payment timing.

Sources: workspace_export.json RD5; meeting_transcript_01.vtt; case_file_11.md
(approved variant-change boundary); case_file_03.md/20.md/26.md/38.md/40.md/41.md
(process/settlement); INTERFACE-CLARIFICATIONS.md U5/U6. The same-product,
different-option boundary also appears in upstream retail policy v1.0.1; backend
acceptance alone cannot create a policy exception.
