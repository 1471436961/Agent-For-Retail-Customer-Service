---
name: catalog-selection
description: Find, count or rank variants under customer constraints; calculate selected values and price differences.
---

After verification read relevant public products, including ones not purchased.
Reuse observed facts. When verification already selects a named order and named
products, batch those reads; do not also discover every order unnecessarily.
Before criteria are known, ask for missing preferences rather than dump stock.

Consider all customer requirements together when selecting from actual catalogs.
Numeric comparisons require explicit units; never guess size scales. Respect
priorities and fallbacks. Disclose other changed attributes in the recap. Explicit
same-except constraints must stay fixed. Do not invent hard constraints from every
original attribute the customer did not ask to retain. Propose a unique best match
instead of asking the customer to select it again. Use actual product records.

Use rank_preferred for constrained comparisons: supply explicit hard filters,
numeric units and ordered preference values from the customer's request. It
returns every available match, ranked by those priorities and then price. Use
search_products for a complete product-name search. Both tools are read-only;
ranking is evidence for a proposal, never permission to submit it.

get_products returns options, prices and availability together. Distinguish an
absent configuration from one that exists but is unavailable. List all matching
options/prices when requested. variant_count includes unavailable choices;
available_variant_count does not. An observed match needs no extra query.

Use calculate with ONE complete parenthesized arithmetic expression, or reuse a
previous verified calculation. Whole-order value covers all occurrences, not refunds;
a selected subset needs its own sum even if its requested write is unsupported.
Explain selected value separately from a permitted refund. Never substitute the
whole order amount. A single recorded price or computed row difference is reusable.
Replacement differences use float(new-old) accumulation in request order, then round
once to 2 digits; do not substitute per-row rounding or Decimal HALF_UP.

Sources: meeting_transcript_01.vtt; process_map_01.png;
INTERFACE-CLARIFICATIONS.md U3-U6. Query batching is an engineering choice.
