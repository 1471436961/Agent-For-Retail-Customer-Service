---
name: item-updates
description: Change pending order variants, preserving quantities and customer constraints; settle the difference.
metadata: {"requires":"service-workflow,catalog-selection","operations":"modify_items"}
---

Only an owned pending order permits item updates. Select available same-product
variants with different options; preserve attributes the customer did not agree
to change explicitly. Do not invent extra hard constraints. No quantity edits;
keep duplicate item counts.

Collect the complete per-order list. In the final recap explicitly ask whether
there are any other items to change before submission, and wait for the answer.
This can be combined with approval of the complete proposal. No second pass or
undo is available. Follow the customer's instructions for other eligible orders
when one cannot be changed.

Use one complete calculator formula for the required net difference.
One saved method pays/receives the difference. Gift balance must cover the full
positive difference; insufficient balance does not authorize a different method.
Propose and confirm an eligible fallback. After approval use modify_order_items.
Status becomes pending (items modified); complete address/payment changes first.

Sources: process_map_01.png (full-list last-call question); meeting_transcript_01.vtt;
system_export.zip CONTRACT-1001/1003; INTERFACE-CLARIFICATIONS.md U5/U6.
