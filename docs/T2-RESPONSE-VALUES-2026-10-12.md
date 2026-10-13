# t2 response validation investigation

The supplied exchange dialogue verified the customer by full name and postal
code successfully. The next `get_owned_order` call failed with
`invalid_read_response; operation=getOrder`. The model then removed the order
ID's leading `#`, which our ownership guard correctly rejected before a second
order GET. Removing that prefix is an Agent mistake; IDs must remain opaque and
the REST adapter already percent-encodes them.

The old read adapter exposed the same error for all model-validation failures
and for a mismatched returned order ID. The supplied transcript does not contain
the underlying REST response, so it does not establish which field failed in
that particular remote case. No raw customer record or benchmark-specific ID
is added to the implementation or regression data.

## Reproducible local defect and repair

The installed SDK's `Environment.to_json_str` converts nested numbers and
booleans to strings. Applying this actual SDK serializer to an order from the
real local ClientAPIRuntime/public development seed reproduces strict-model
failures at `items[0].price` and `payments[0].amount`. Previously only the gift
card balance string discrepancy had a compatibility path.

`INTERFACE-CLARIFICATIONS.md`, U3, distinguishes the normal JSON-number contract
from an optional compatibility implementation: accept only valid finite number
strings, preserve an anomaly record, and never default invalid values to zero.
This repair follows that allowance. It does not declare serialized strings to
be the normal OpenAPI type or prove they were present in the supplied remote
order response.

The response adapter now decodes scalar representations only at documented
field locations: gift balances, item/catalog prices, payment amounts, exchange
price differences, and catalog availability. Boolean strings are explicitly
decoded, including `False` as false. Unknown keys, missing fields, incorrect
containers, unknown enum states, bool-as-money, nonfinite values and invalid
numeric strings remain errors under the unchanged strict models. Identifiers,
option strings, addresses and user language are never rewritten. Decoding uses
a copy and retains response warnings; write receipts still require exact target,
financial and operation checks. An invalid/unknown write is never retried.

Read and write failures now expose bounded schema locations and error kinds
without input values. Unknown key names are masked. Order-ID response mismatch
has a distinct diagnostic location. Model instructions and the order tool
description explicitly forbid changing an opaque ID in response to a schema
failure.

## Validation

- Full offline regression: 191 tests, 0 failures/errors, 21.366 seconds.
- After a second supplied dialogue showed three separate `getOrder` schema
  failures, one additional regression passed (0.188 seconds): multi-turn
  full-name/postal-code verification, three owned order reads against the real
  public development backend with SDK scalar serialization, and reversed host
  batch results with matching provider `tool_call_id` values. Model outputs are
  controlled; this verifies protocol and tool behavior, not model comprehension
  or completion of that remote exchange case. No supplied customer data is used
  in the regression.
- Real local REST backend/public seed with the actual SDK scalar serializer:
  identity lookup, order details, catalog false/price/options, and all seven
  changes succeed with normal ownership, receipt checks and single-write behavior.
- Negative cases cover invalid money, missing/unknown fields with redacted
  diagnostics, stripping the order prefix, and unknown write results without
  a second write.
- Agent submission audit: 27 files / 126707 bytes, passed.
- No paid model call, credential read or evaluation-scope change.

## Latest submitted job

Using the user-confirmed process-local proxy `127.0.0.1:26799`, GitHub run
`38227139330` is completed with failure. Its saved report for Job
`eab87d49-ef6b-44f6-a3cd-ead50272b00a` says `phase=cancelled`,
`cancel_requested=true`, no start time, and no case score. It is not evidence of
a business failure in the submitted Agent. The report is retained under
`.git/enterprise-evaluations/38227139330/`.

These are local repairs; a fresh platform run is needed to verify the exact
remote response and subsequent exchange dialogue. Offline success is not a t2
business benchmark pass.
