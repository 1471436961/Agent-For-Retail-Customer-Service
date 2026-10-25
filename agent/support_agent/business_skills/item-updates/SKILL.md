---
name: item-updates
description: Change pending order variants, preserving quantities and customer constraints; settle the difference.
metadata: {"requires":"service-workflow,catalog-selection","operations":"modify_items"}
---

Only an owned pending order permits item modification. Select different available
variants of the same product, preserving all options the customer did not agree
to change. Pending modification rejects an unchanged item ID; this does not ban
identical-unit replacement in a delivered exchange. No quantity editing.

Collect the complete per-order replacement list before submission and make clear
that no additional item changes can be appended afterward. Include all requested
changes in the final recap and allow the customer to complete/correct the list.
Preserve duplicate IDs and counts. Confirm changes for each eligible order; when
one order cannot be changed, follow the customer's instructions for other orders.

Quote the net difference from actual prices using the prescribed calculation.
One saved method pays or receives it. For a positive difference a gift card must
cover the full difference; no split settlement. A requested insufficient gift
card does not authorize charging another method: propose an eligible fallback.

After approval use modify_order_items(order_id,replacements,payment_method_id).
The result moves to pending (items modified); no further order actions can be
assumed. Finish approved address/payment changes first. Do not add notes, flags,
synthetic quantities or hidden fields to the documented write surface.

Sources: process_map_01.png; meeting_transcript_01.vtt (full list, one-time change);
system_export.zip/policy_contracts.json CONTRACT-1001/1003 (current published write set);
INTERFACE-CLARIFICATIONS.md U5/U6 (occurrences and algorithm).
