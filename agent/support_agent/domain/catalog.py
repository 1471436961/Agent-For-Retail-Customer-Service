"""Compare API variants using explicit structured preferences, never user text."""
import re
import math
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
