---
name: catalog-selection
description: Find, count or rank variants under customer constraints; calculate selected values and price differences.
---

After verification read relevant public products, including ones not purchased.
Reuse observed facts. When verification already selects a named order and named
products, batch those reads; do not also discover every order unnecessarily.
Before criteria are known, ask for missing preferences rather than dump stock.

Translate ALL hard constraints into one query or batch ranked alternatives.
Numeric comparisons require explicit units; never guess size scales. Respect
priorities and fallbacks. If a requested configuration requires changing another
attribute, explain that change in the proposal. Explicit same-except constraints
must stay fixed. Propose a unique best match instead of asking the customer to
choose it again. Use actual IDs; IDs do not encode product relationships.

query_product_variants filters available choices. Invalid vocabulary is not no
stock. No limit when all matches/prices were requested; limit only for a best/top
request. variant_count includes unavailable choices, available_variant_count does
not. Use an obvious observed match without another query.

Use calculate with ONE complete parenthesized arithmetic expression, or reuse a
quote's calculations. item_price_total covers all item occurrences, not refunds;
a selected subset needs its own sum even if its requested write is unsupported.
Explain selected value separately from a permitted refund. Never substitute the
whole order amount. A single recorded price or computed row difference is reusable.
Quotes retain backend float(new-old) accumulation in request order, then round
once to 2 digits; do not substitute per-row rounding or Decimal HALF_UP.

Sources: meeting_transcript_01.vtt; process_map_01.png;
INTERFACE-CLARIFICATIONS.md U3-U6. Query batching is an engineering choice.
