---
name: human-handoff
description: Escalate an explicit human request, unsupported-limit complaint, or genuine unresolved policy gap.
---

Accepted bases are an explicit human request, escalation requested after explaining
an unsupported limit, or known facts for which policy genuinely supplies no rule.
Anger, complexity or delay alone are not a transfer condition. An explicit human
request needs no extra justification or mandatory troubleshooting.

Pause unattempted writes on a transfer request unless the customer clearly asks
to finish already confirmed work first. Completed changes remain completed. If
the customer requested complaint escalation after returns, report the actual
separate results before transferring; do not invent additional approvals.

Use transfer_to_human with the actual basis and a minimal factual summary: current
goals, relevant order references, actual outcomes and why human handling is needed.
Do not include unnecessary full addresses, email or payment details. A proposed
call is not transfer completion. After an accepted transfer use its configured
exact terminal notice and stop customer API automation. Unknown outcome stops
automation without claiming success or automatically retrying.

Unsupported operations include new purchases/payment instruments, arbitrary
refunds, quantity edits, split shipments, Care email edits, notes/flags/hidden
fields and post-submission item additions. Explain supported alternatives without
silently expanding authorization. No invented lockouts or account exceptions.

Sources: meeting_transcript_04.vtt (direct request 465–493, allowed bases and terminal
flow); meeting_transcript_05.vtt (scope/signoff); workspace_export.json QS4/EM3;
system_export.zip/policy_contracts.json (active production closed surface);
materials/framework/client_api_contract.md (transfer receipt and replay).
