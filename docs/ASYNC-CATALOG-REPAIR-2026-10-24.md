# Catalog goals and asynchronous business writes

The third reported case exposed two implementation gaps in remote commit
`b9169ba`: the Agent falsely denied its catalog capability before verification
and subsequently dropped the catalog-count request; a return's HTTP 202 was
treated as an opaque unknown error and its asynchronous receipt was discarded.
There was no status-resource request before the later human transfer.

## Contract review and changes

- U4 permits relevant public catalog reads after independent identity
  verification. Both prompt stages now explain that capability and retain all
  requested goals. Verification remains required; no identity parsing rules or
  benchmark answer constants were introduced.
- Product tools now derive `variant_count` and `available_variant_count` from
  actual normalized records. Availability counts exclude unavailable variants.
  Full owned-order reads also return applicable sourced change policies, so a
  return need not spend a separate model turn requesting an already known rule.
- The normal OpenAPI success codes remain 200 for the seven business changes
  and 201 for transfer. U3/U6 and the introductory clarification distinguish
  normal schemas from deployed response differences and asynchronous receipts.
- The public installed SDK runtime returns a pending workflow ID with HTTP 202,
  a local `Location` and `Retry-After`. The business mutation occurs when that
  workflow completes. The adapter now follows the actual returned resource
  using GET, waits as instructed, and validates the final operation receipt
  against the approved target, item occurrences, amounts and destination.
  No status path is guessed; external and unrelated customer/order locations
  are rejected. A 202 by itself is never reported as a registered return.
- One invocation waits up to 20 seconds of service-directed delays. This is an
  engineering wait bound, not a business policy or an approval requirement.
  A longer job remains pending and retains its receipt. `inspect_change_result`
  continues that workflow without a new approval or a second business write.
  Decisions depend on responses and accumulated instructed delays, not wall-clock
  timestamps. Terminal failures and malformed receipts are not successes.
- For lost or invalid write receipts, authorized owned-record inspection can
  validate the exact matching current business record. The result states that
  observation does not identify its writer. An unchanged or mismatched record
  preserves uncertainty and never permits replay. The initial outcome remains
  recorded when inspection resolves it.
- The six contractually read-only API operations, including customer search,
  can wait and retry a 429 only when the service supplies valid `Retry-After`.
  Waiting is bounded. Authentication failures, 503 and transport errors are not
  automatically retried. Business writes and human transfer are not replayed.

The earlier local commit `13d1fad` separately removes repeated confirmation
stages. These changes preserve the requirement to explain the complete relevant
change and obtain semantic approval before writing.

## Verification

All **254 offline tests pass**. Tests use synthetic records and the installed
public SDK, not benchmark tasks, private deployment manifests or expected answers.

- Each of the seven asynchronous operations makes one business write, shows no
  mutation at initial 202, polls the service-provided status resource and ends
  with the same database hash as an independent native SDK operation.
- A longer return workflow remains pending and resumes by inspection, without
  another customer turn, another approval or another POST.
- Invalid workflow references, terminal failures and a corrupted refund
  destination cannot produce success. Lost-response inspection covers all seven
  operation types. Fresh-toolkit replay preserves requests and results.
- Availability tests vary total/available counts and change stock after reads;
  the count follows the records. A mixed information/return dialogue preserves
  the original goals in model context and performs an actual asynchronous return.
- Read-rate-limit tests exercise the SDK's actual 429 behavior and verify no
  retry of authentication/server errors or missing/invalid retry instructions.

Model decisions in these dialogue tests are controlled. They validate the
protocol, business checks and actual effects, not real-model understanding or a
platform pass. The previous authorized 16 local model calls are exhausted; no
additional paid local generation was performed for this repair.

The original full run `51788b01-a42c-4ccb-87e5-9cef8016e858` recorded 50 cases:
2 passed, 29 business failures and 19 candidate errors; 84 were not run. Its
final report has `verdict=model_unavailable` and a Kimi agent-role HTTP 503,
so no complete score is available. This does not erase the demonstrated Agent
defects or establish the causes of every candidate error. Its snapshot remains
the old commit; local repairs do not change an already submitted run.
