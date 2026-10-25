---
name: catalog-selection
description: Find, count or rank variants under customer constraints; calculate selected values and price differences.
---

After verification, related public products may be browsed even if not purchased.
Read selected products, not every catalog before criteria are known. Reuse actual
orders/catalogs; item IDs do not encode product relationships or size scales.

Translate the customer's requirements, preserved options, priorities and fallback
conditions into filters. Use query_product_variants for complex filtering/ranking;
batch selected products in one call. Numeric comparisons require an explicit unit.
Ask for all matches with no limit when all options/prices were requested; apply a
limit for a requested best/top selection. List every relevant requested option and
price. variant_count includes unavailable variants; available_variant_count does
not. Invalid criteria are not a stock finding: use the returned vocabulary to fix
them. Distinguish an absent configuration from one that exists but is unavailable.

Same-product replacements must be available. Preserve attributes the customer did
not authorize changing, with per-row preserve_options for different products.
If priorities identify one best candidate, propose it without asking the customer
to repeat their choice. Clarify only unresolved preferences. A reference to a
variant in another owned order must use that actual record, not a guessed ID.

Use calculate for derived arithmetic (add/subtract/multiply/divide/round), choosing
formula and operands from facts. Reuse a recorded single price, an existing quote's
row difference or item_price_total for its exact whole-item scope. The latter sums
every item occurrence; it is neither payment net balance nor refund eligibility.
For a financial inquiry about a subset, identify that subset and calculate its
value even when the proposed write is unavailable; explain value separately from
a refund currently permitted. Clarify genuinely ambiguous categories.

Quotes compute replacement differences from the original sale and target prices:
accumulate float(new-old) in request order and round the final total to 2 digits.
Do not substitute per-row rounding or Decimal HALF_UP. The final receipt governs
the actual settlement. Unavailable, invalid and missing values are never zero.

Sources: meeting_transcript_01.vtt (facts/calculation controls); workspace_export.json
RD5; INTERFACE-CLARIFICATIONS.md U3–U6 (catalog scope, quantities, money algorithm);
process_map_01.png. Query limits and evidence reuse are engineering choices, not
new customer policy. Customer requirements determine filters and ranking.
