# Read order and variant evidence together

Platform job `612cad27-2a74-4808-9234-cc22f0ac749a` still recorded no business
write. Its saved conversation now ends after a complete prepared exchange recap;
there is no subsequent customer approval in that record. Writing at that point
would violate the material's confirmation requirement. The report does not
establish a particular platform step limit or why the next turn was absent.

The visible sequence nevertheless contains avoidable model/tool round trips:
read order, read the keyboard catalog, then read the thermostat catalog. These
are related read-only facts for a single task and can be retrieved together.

`get_owned_order(..., include_product_variants=True)` reads the owned order and
each distinct product's actual variant catalog in one host tool action. It uses
the same documented customer/order/catalog GET requests, preserves order fields
and repeated items, and records the real observations for later revalidation.
The model still chooses the replacements and interprets consent. There is no
case-specific selector, implicit approval, business write, or changed test scope.

The model instructions prefer this combined read for known-order exchanges and
item modifications, and prohibit re-fetching a catalog already in that result.
The original default read behavior remains available for other operations.

Regression coverage checks exact order preservation, one GET per distinct
product, zero changes from the combined read, grounded proposal preparation,
and the choice -> complete quote -> explicit confirmation -> real exchange
write path with four total tool calls including identity verification.

## Follow-up after job 75dfbaf8

The new platform trace uses the combined read and performs the exchange POST.
The returned receipt is accepted with difference -71.06; its cross-product row
pairing matches the public SDK's independently-sorted old/new ID representation.
The native function validates corresponding same-product IDs before sorting
them. Do not swap or forge request pairs to compensate for that receipt format.
The trace ends at the actual write result, without a final Agent explanation;
the summary still reports failure and does not expose its termination reason.

The trace also fetched service policy in a separate tool turn immediately
before preparation. Combined order/catalog reads now include the applicable
sourced policies so this extra round trip is unnecessary.

When the customer has already supplied an order ID, verify_customer accepts
that optional order ID and can return requested_order plus its catalogs after
independent verification. The order never becomes an identity credential:
search, claimed-ID match and profile load precede ownership/order/catalog reads.
Optional order errors preserve verified profile evidence, without reading an
unowned order. Calls without the optional arguments retain their old behavior.
Model instructions use those real returned facts and avoid fetching them again.
Regression tests exercise the identity boundary, failure recovery and the full
three-tool quote/confirmation/write path including strict SDK history replay.
No platform execution budget, scoring rule or binding has been changed.

The remaining three authorized Kimi calls were also attempted. All returned
HTTP 200, but this focused local check did not reach a quote: the model first
dropped the supplied # and recovered from the verified order list, then the
older reconstructed fixture lacked unrelated product catalogs. The fourth
generation was blocked locally before HTTP by the round's 16-call cap; its
generic host exception is not evidence of provider unavailability. The identity
prompt now explicitly preserves the order's #. The complete round used 16
requests with estimated upper cost CNY 1.136088. No further local paid calls
are authorized by that exhausted round, and this focused check is not claimed
as a successful full-dialogue validation.
