---
name: address-changes
description: Update a profile default or pending order address; copy or restore an owned address.
metadata: {"requires":"service-workflow","operations":"default_address,order_address"}
---

Treat profile default and order shipping addresses as separate records. Establish
which the customer wants changed; a default update does not propagate to orders.
When asked for all eligible orders, inspect actual owned statuses: only pending
orders permit this update. processed, cancelled and pending (items modified) do
not. processed alone does not establish an actual shipping event or date.

For new/corrected input, collect required street, city, region, country and postal
code; read back the complete resulting address and obtain connected approval.
For a suite-only correction, retain other fields from the exact target order.
Do not carry an old suite into a wholly replaced address; absent line 2 is empty.

When copying an existing owned address, use the actual source record. Recap source
order and destination scope without reprinting private street fields unless the
customer asks. Never ask the customer to retype facts already accessible. A later
request to restore only the profile leaves order addresses unchanged; recover
the original profile from actual history, not from a possibly different order.

Use update_default_address(address) or update_order_address(order_id,address).
For approved multi-record work use submit_changes with separate explicit targets.
Report which records actually changed and any ineligible/failed targets, using
receipts. An HTTP response is not permission to claim unrelated orders changed.

Sources: process_map_05.png; intake_form.md (own-profile scope); meeting_transcript_01.vtt
(address read-back/confirmation); meeting_transcript_02.vtt (closed record surface);
system_export.zip/policy_contracts.json CONTRACT-1001/1002 (active production);
INTERFACE-CLARIFICATIONS.md U3/U6. Privacy-preserving source recaps and empty line 2
are presentation/API choices; they do not remove confirmation.
