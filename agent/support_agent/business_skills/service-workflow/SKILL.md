---
name: service-workflow
description: Confirm, revise, submit and report a record change; handle partial or uncertain outcomes.
---

Use for every record change. Interpret intent and consent from the full dialogue;
chronology or a particular word cannot prove permission.

1. Establish the complete relevant target, scope and requested conditions from
   actual records. Before a one-time item operation, collect the complete list.
   Preserve duplicate item IDs as quantities and their requested order. There is
   no hidden line ID for selecting a particular duplicate with a different price.
2. Explain the complete relevant change: target records, items/options or address,
   amount and settlement destination, plus the operation's applicable process.
   Then obtain a later clear approval referring to this proposal. A question,
   pause, withdrawal or unmet condition is not approval; approval can also contain
   a next-step question. Clarify real ambiguity without demanding a magic word.
3. Reads, calculations and optional quotes need no approval. Use prepare_change
   only for missing facts/calculations, not as another customer permission stage.
   On an accurate approved recap, use the matching write tool immediately.
4. A change of operation, scope, amount, address or destination needs a revised
   recap and approval. Reuse known component differences when removing rows;
   a single original item's return uses its recorded price. New aggregation needs
   calculate or a quote. A draft is not a registered application. supersedes can
   replace drafts in the new quote; invalidate_change withdraws without replacement.
5. One recap can authorize several explicit changes. submit_changes executes
   separate operations in dependency order (address/payment before item changes).
   Preserve partial outcomes; no cross-operation atomicity is promised.
6. Check actual receipts. Never repeat an accepted or uncertain write. For a
   pending/unknown outcome inspect its actual status resource; do not repost.
   Report failed, unattempted and successful work separately. Read-only retries
   do not authorize a business write. Do not trim a rejected list to bypass limits.

Acceptance of a return/exchange registers a request. It does not establish funds
arriving, replacement allocation/shipping, or email delivery. Do not invent labels,
carriers, deadlines, inspection prerequisites or dates absent operation evidence.
After reporting results, allow follow-ups and corrections; success does not end
the conversation. Accepted human handoff has a separate terminal flow.

Sources: meeting_transcript_01.vtt (recap 301–615, one-time collection 901–925,
calculator 1113–1165); INTERFACE-CLARIFICATIONS.md U1/U3/U5/U6;
materials/framework/client_api_contract.md (errors, replay and asynchronous status).
Engineering: quotes are optional and batching preserves separate actual writes.
