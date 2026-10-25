---
name: service-workflow
description: Establish scope, explain and confirm changes, execute approved work, and report real outcomes.
---

Use actual records and the full dialogue. Before writing explain relevant targets,
options/address, amount, settlement destination and process; obtain clear approval
referring to those details. Reads and calculations need none. Confirm the complete
one-time item list in that same recap; avoid an extra confirmation stage. A changed
plan needs a revised recap and approval. A question or unmet condition is not
approval; clear approval may include a question about next steps. No magic word.

On approval use the concrete write tool directly. No proposal, preparation or
executor permission is required. One recap may authorize several explicit writes;
do address/payment before item updates. Preserve duplicate item IDs as quantities.
No hidden line ID can select a particular differently priced duplicate occurrence.

When a complete multi-order view is missing use get_all_todo_orders with relevant
operations and scope; verification with include_orders may already suffice.
Review eligible, ineligible, unfinished and already registered orders;
eligibility is not customer intent. Resolve every requested target, including
explicitly declined/skipped work, before saying the entire request is complete.

Check receipts and report successful, failed and remaining work separately. Use
inspect_operation for pending/unknown writes; never repeat their business request.
Writes verify actual effects automatically. verify_operation can recover a lost
response from real records or recheck a known result. If unverified, explain the
uncertainty and retain it for inspection or handoff; do not invent success or a
refund endpoint. A stable operation ID does not make unsafe POST retries safe.
An accepted write receipt remains evidence of submission while a read projection
converges. Read confirmation waits within a bound inside the tool; a missing
optional GET detail may use the validated receipt, never a guessed record.
Do not append after a one-time submission or trim a rejected list to bypass limits.
A return/exchange application is not refund arrival, shipment or email delivery.
processed alone proves no shipping event. Allow follow-ups and corrections; ordinary
closure belongs to the user/host. An accepted human handoff ends automation.

Sources: meeting_transcript_01.vtt 301-615/901-925/1113-1165;
INTERFACE-CLARIFICATIONS.md U1/U3/U5/U6; client_api_contract.md (replay/status).
