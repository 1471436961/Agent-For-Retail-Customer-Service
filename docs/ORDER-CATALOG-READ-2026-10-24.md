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
