---
name: address-changes
description: Update a profile default or pending order address; copy or restore an owned address.
metadata: {"requires":"service-workflow","operations":"default_address,order_address"}
---

Profile default and order shipping addresses are separate scopes. Update only
requested records; a default change does not propagate. Only pending owned orders
permit address changes; processed, cancelled and pending (items modified) do not.

Read back the complete new/corrected address and obtain connected approval. A
suite-only edit keeps other fields from the target order. A wholly replaced
address without line 2 clears the old suite. Required street/city/region/country/
postal fields must be complete.

Copying an existing owned address can be recapped by source order and destination
scope without reprinting private street fields unless requested. Reuse accessible
facts. A later profile-only restoration uses the actual original profile in
history and leaves order addresses unchanged.

Use update_default_address or update_order_address; approved multi-record work
uses submit_changes. Report actual receipts and ineligible/failed targets.

Sources: process_map_05.png; meeting_transcript_01.vtt/02.vtt;
system_export.zip CONTRACT-1001/1002; INTERFACE-CLARIFICATIONS.md U3/U6.
