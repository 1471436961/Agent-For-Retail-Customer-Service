# Submit an already-approved natural-language change

The reported failure is a real execution gap. The Agent had read the customer,
order and catalog, explained the exact two-item exchange and difference, and
received explicit approval of those details. It nevertheless created only an
internal draft. The supplied API trace contains no exchange POST. A successful
`prepare_change` result is not a successful business operation.

## Material requirement and implementation

`meeting_transcript_01.vtt` (semantic confirmation and complete item collection),
`case_file_03.md` and `POLICY-REGISTER.md` CONF-01/02 require a complete relevant
recap and clear later customer approval before writing. They do not require an
internal proposal ID or a particular footer before a valid recap can count.

`submit_change` is an actual WRITE tool for all seven supported operations. The
model interprets the full conversation, the complete requested scope and the
customer's consent. No phrase regex, keyword approval classifier or case routing
was added. A structural earlier-assistant/later-user check is only chronology;
it is not represented as proof of semantic consent.

The toolkit freezes genuine prior customer/order/catalog reads, validates the
requested parameters against those observations, re-reads current evidence,
and submits through the existing exact REST adapter only when the evidence
matches. Changed price/status/payment facts do not silently become an approved
new quote. The optional prepare/display/execute quote path remains available.

A local reference bound to the actual user turn, operation and target prevents
repeating the same direct submission. It is never sent as a REST field. Actual
legacy execution outcomes are recorded under the same reference so the Agent
does not process a successful exchange a second time through the alternative
tool. Unknown outcomes are not retried. A later, genuinely new address request
can still restore an earlier address.

## Validation

- Real local tau2 SDK/database comparisons cover all seven direct operations,
  environment history replay, duplicate delivery, changed facts, uncertain
  outcomes, missing earlier observations and later distinct address changes.
- An additional regression reproduces the model's mistaken execute-then-submit
  sequence: one genuine write, duplicate rejected before execution, then an
  explanation from the actual receipt.
- All 230 offline tests and the submission package audit pass for this repair.
- The authorized Kimi round used 13 of 16 actual calls, estimated upper cost
  CNY 1.0149155 of CNY 10. Both local scenarios issued one exchange POST and
  reached `exchange requested` with difference -71.06. One scenario starts
  directly from the already-approved ordinary recap; the other begins with
  identity verification and a four-customer-turn conversation.
- That full-conversation run exposed the redundant second-tool attempt described
  above. It did not produce a second write; the added regression and prompt
  correction address it. No additional model call was spent on that correction.

These are local checks using user-supplied conversation data and a reconstructed
isolated backend, not a claim of passing the platform or the full scenario suite.
The platform binding remains t2 with its existing selected case; no credentials,
downloaded materials or local test artifacts are included in the submission.
