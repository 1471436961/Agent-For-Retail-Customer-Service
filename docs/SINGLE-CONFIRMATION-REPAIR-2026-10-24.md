# One relevant submission confirmation

## Observed failures

The all-case run `51788b01-a42c-4ccb-87e5-9cef8016e858` uses the same submitted
commit `b9169ba` as the previous case-0 pass. The new case-0 conversation asks
whether the customer wants a quote prepared, answers clarification, asks about
preparation again, and only then prepares/displays the quote for final approval.
It eventually receives explicit submission approval and registers the exchange.
The saved trace ends at the accepted tool result, without a final Agent reply.
This identifies unnecessary stages; the final scoring/termination reason is
not inferred from the visible trace alone.

The earlier passing conversation followed a different route: natural recap,
customer approval, `submit_change`, actual outcome explanation, customer stop.
It initially misquoted -71.10 before the write calculated -71.06. Passing that
one run did not establish correct arithmetic or reliable dialogue execution.

## Implementation causes and changes

1. Repetitive instructions interleaved quote preparation, review and execution.
   The prompt now starts with one ordered workflow: factual clarification ->
   read-only validated quote -> one complete natural recap/approval -> actual
   write -> receipt explanation. Quote preparation needs no permission.
2. The Agent forcibly appended a second fixed recap and confirmation footer.
   It now preserves the model's complete natural-language reply. No footer
   matching is required to recognize an ordinary recap. The model must explain
   the real quote and interpret customer consent semantically; the program
   records response/customer chronology only, not a proof of either meaning.
3. Re-preparing an identical unattempted quote created a fresh proposal and
   invalidated the earlier response. Identical parameters and relevant evidence
   now reuse that prepared version. Actually changed facts get a new version.
4. Re-reading an unchanged quote reset its response record. Retrieval now keeps
   the existing response/customer timeline; it does not start another approval
   stage. Forged/different review data still invalidate that record.
5. Quote amounts must come from prepare_change's actual calculator results.
   A separate calculate call remains available for other arithmetic questions.
   Clear approval can include a question about next steps; a question alone or
   a request only to prepare a quote is not submission permission.

The requirements remain those in meeting_transcript_01.vtt (complete relevant
recap, semantic attachment of approval, complete one-time lists), case_file_03.md
and the supplied REST contracts. No user-language regex, approval keyword
classifier, hidden test lookup, new REST field or automatic write retry was added.
Identity verification, ownership, availability, quantities, payment eligibility,
price revalidation, one-time mutation and uncertain-outcome handling remain.

## Verification scope

All 242 offline regression tests and the submission package audit pass.

Controlled model decisions exercise real local SDK writes for all seven
operations, unchanged quote retrieval, changed quote invalidation, question-only
turns, approval accompanied by a process question, history restoration and exact
preservation of a single model-owned recap. These tests do not claim LLM semantic
competence. The prior real-model round exhausted its authorized 16 calls; no new
paid local generation was made for this repair. The all-case platform run is
left running with its original snapshot; local edits do not change that snapshot.
