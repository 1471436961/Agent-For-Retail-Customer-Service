"""Order explicit changes by real dependencies, not by customer wording."""
from copy import deepcopy
from support_agent.domain.changes import KINDS, require


def order_requests(requests):
    require(isinstance(requests, list) and bool(requests), "empty_change_plan")
    groups = {}
    for row in requests:
        require(isinstance(row, dict) and set(row) == {"kind", "parameters"} and row["kind"] in KINDS and isinstance(row["parameters"], dict), "invalid_plan_step")
        target = row["parameters"].get("order_id") if row["kind"] != "default_address" else None
        groups.setdefault(target, []).append(deepcopy(row))
    ordered = []
    rank = {"default_address": 0, "order_address": 0, "payment": 1, "modify_items": 2, "cancel": 0, "return": 0, "exchange": 0}
    for group in groups.values():
        kinds = [r["kind"] for r in group]
        require(len(kinds) == len(set(kinds)), "duplicate_plan_change_requires_clarification")
        require(not ("cancel" in kinds and len(kinds) > 1) and not ({"return", "exchange"} <= set(kinds)), "incompatible_order_plan_requires_clarification")
        ordered.extend(sorted(group, key=lambda row: rank[row["kind"]]))
    return ordered
