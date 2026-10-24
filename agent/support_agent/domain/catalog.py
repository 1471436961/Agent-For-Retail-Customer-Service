"""Compare API variants using explicit structured preferences, never user text."""
import re
import math
from copy import deepcopy
from operator import lt, le, eq, ge, gt
from support_agent.domain.identity import ReadError


def numeric_option(value, unit):
    # This is a catalog quantity codec, not a natural-language intent parser.
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*" + re.escape(unit) + r"\s*", value)
    if not match:
        raise ReadError("uncomparable_catalog_option")
    result = float(match[1])
    if not math.isfinite(result):
        raise ReadError("uncomparable_catalog_option")
    return result


def compare(product, filters, sort_by, descending, numeric_unit):
    candidates = [item for item in product["items"] if item["available"] and
                  all(item["options"].get(key) == value for key, value in filters.items())]
    if sort_by == "price":
        key = lambda item: item["price"]
    else:
        if not numeric_unit:
            raise ReadError("numeric_unit_required")
        def key(item):
            if sort_by not in item["options"]:
                raise ReadError("missing_catalog_option")
            return numeric_option(item["options"][sort_by], numeric_unit)
    # Stable sorting retains API order for ties; never infer size scales.
    return {"product_id": product["product_id"], "filters": filters,
            "sort_by": sort_by, "descending": descending,
            "candidates": sorted(candidates, key=key, reverse=descending)}


def query_variants(product, query):
    """Execute structured criteria against actual records; never interpret text.

    Unknown option vocabulary is an invalid criterion, not an out-of-stock
    finding. Numeric quantities require exact, explicitly supplied units.
    """
    values = {}
    for item in product["items"]:
        for key, value in item["options"].items():
            if value not in values.setdefault(key, []):
                values[key].append(value)
    invalid = {key: value for key, value in query["filters"].items()
               if key not in values or value not in values[key]}
    unknown = [row["option"] for row in query["numeric_filters"] if row["option"] not in values]
    base = {"product_id": product["product_id"], "name": product["name"],
            "query": deepcopy(query), "variant_count": len(product["items"]),
            "available_variant_count": sum(item["available"] for item in product["items"]),
            "option_values": values}
    if invalid or unknown or (query["sort_by"] != "price" and query["sort_by"] not in values):
        return {**base, "state": "invalid_criteria", "invalid_filters": invalid,
                "unknown_options": unknown,
                "notice": "Criteria do not match the catalog vocabulary. No availability conclusion was made."}
    operations = {"lt": lt, "lte": le, "eq": eq, "gte": ge, "gt": gt}
    matches = []
    for item in product["items"]:
        if not all(item["options"].get(key) == value for key, value in query["filters"].items()):
            continue
        selected = True
        for row in query["numeric_filters"]:
            if row["option"] not in item["options"]:
                raise ReadError("missing_catalog_option")
            value = numeric_option(item["options"][row["option"]], row["unit"])
            selected = selected and operations[row["operator"]](value, row["value"])
        if selected:
            matches.append(item)
    available = [item for item in matches if item["available"]]
    if query["sort_by"] == "price":
        key = lambda item: item["price"]
    else:
        def key(item):
            if query["sort_by"] not in item["options"]:
                raise ReadError("missing_catalog_option")
            return numeric_option(item["options"][query["sort_by"]], query["numeric_unit"])
    ranked = sorted(available, key=key, reverse=query["descending"])
    selected = ranked if query["limit"] is None else ranked[:query["limit"]]
    return {**base, "state": "complete", "matching_variant_count": len(matches),
            "available_matching_count": len(available),
            "unavailable_matching_count": len(matches) - len(available),
            "returned_count": len(selected), "has_more": len(selected) < len(available),
            "candidates": deepcopy(selected)}
