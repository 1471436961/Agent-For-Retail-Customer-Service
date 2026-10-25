"""Packaged, allowlisted business skills; no customer-text routing or private files."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal, get_args

SkillName = Literal["service-workflow", "catalog-selection", "address-changes",
    "order-cancellation", "order-payment", "item-updates", "delivered-returns",
    "delivered-exchanges", "human-handoff", "delivered-service"]
SKILL_NAMES = get_args(SkillName)
KINDS = ("default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange")


@lru_cache(maxsize=1)
def library():
    """Cache immutable public assets, never customer evidence."""
    root = Path(__file__).resolve().parent / "business_skills"
    result = {}
    for name in SKILL_NAMES:
        path = root / name / "SKILL.md"
        if (any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (path, path.parent, root)) or
                not path.resolve().is_relative_to(root.resolve())):
            raise ValueError("Invalid packaged skill path")
        text = path.read_text(encoding="utf-8")
        if len(text.encode()) > 16000 or not text.startswith("---\n"):
            raise ValueError("Invalid packaged skill")
        header, body = text[4:].split("\n---\n", 1)
        meta = dict(line.split(": ", 1) for line in header.splitlines())
        if (meta.get("name") != name or not meta.get("description") or
                set(meta) - {"name", "description", "metadata"}):
            raise ValueError("Invalid skill metadata")
        # The supported metadata subset is inline JSON (valid YAML), so the
        # production package needs no extra YAML parser dependency.
        routing = json.loads(meta.get("metadata", "{}"))
        if (not isinstance(routing, dict) or set(routing) - {"requires", "operations", "visibility"} or
                any(not isinstance(value, str) for value in routing.values())):
            raise ValueError("Invalid skill routing metadata")
        requires = tuple(filter(None, routing.get("requires", "").split(",")))
        operations = tuple(filter(None, routing.get("operations", "").split(",")))
        if set(requires) - set(SKILL_NAMES) or set(operations) - set(KINDS):
            raise ValueError("Invalid skill dependencies or operations")
        result[name] = {"name": name, "description": meta["description"], "requires": requires,
                        "operations": operations, "version": sha256(text.encode()).hexdigest()[:16],
                        "instructions": body.strip(), "visibility": routing.get("visibility", "public")}
    return result


def skill_index():
    return "\n".join(name + ": " + row["description"] for name, row in library().items() if row["visibility"] == "public")


def skill_bundle(names):
    if (not isinstance(names, list) or not 1 <= len(names) <= len(SKILL_NAMES) or
            any(not isinstance(name, str) or name not in SKILL_NAMES for name in names)):
        raise ValueError("Select known skill names, not filesystem paths")
    ordered, visiting = [], set()
    def include(name):
        if name in visiting:
            raise ValueError("Cyclic skill dependency")
        if name in ordered:
            return
        visiting.add(name)
        for dependency in library()[name]["requires"]:
            include(dependency)
        visiting.remove(name)
        ordered.append(name)
    for name in names:
        include(name)
    # Tool trajectories (including judge input) need only an activation receipt.
    # The Agent loads current trusted bodies once, outside the tool transcript.
    return {"names": ordered, "versions": {name: library()[name]["version"] for name in ordered}}


def active_skill_names(history):
    """Restore selection only from matched successful real skill-read results."""
    calls, active = {}, ()
    for row in history:
        for call in row.get("tool_calls") or []:
            calls[call["id"]] = call
        results = row.get("tool_messages", []) if row["role"] == "tool" else []
        if row["role"] == "tool" and "id" in row:
            results = [row]
        for result in results:
            call = calls.get(result["id"], {})
            if result.get("error") or call.get("name") not in {"load_skills", "verify_customer"}:
                continue
            try:
                value = json.loads(result["content"])
            except (ValueError, TypeError):
                # A malformed ordinary read remains an explicit failed result
                # in the wire projection, not a policy activation or fatal halt.
                continue
            if not isinstance(value, dict):
                continue
            arguments = call.get("arguments", {})
            requested = arguments.get("names") if call["name"] == "load_skills" else arguments.get("skill_names")
            if not requested:
                if call["name"] == "verify_customer" and value.get("customer_id"):
                    active = tuple(dict.fromkeys([*active,*skill_bundle(["service-workflow"])["names"]]))
                continue  # Historical sessions get shared rules, not a policy-free path.
            actual = value.get("business_skills")
            if not isinstance(actual, dict) or not set(requested) <= set(actual.get("names", [])):
                continue
            # Rehydrate CURRENT packaged dependencies. Historical version changes
            # or untrusted receipt prose must neither become instructions nor
            # erase an otherwise valid conversation.
            expected = skill_bundle(requested)
            active = tuple(dict.fromkeys([*active,*expected["names"]]))
    return active


def instructions_for(names):
    return "\n\n".join("Skill " + name + " (" + library()[name]["version"] + "):\n" +
                         library()[name]["instructions"] for name in names)


def operations_for(names):
    chosen = {kind for name in names for kind in library()[name]["operations"]}
    return tuple(kind for kind in KINDS if kind in chosen)


def policy_reference(kind):
    return {"skill": next(name for name, row in library().items() if kind in row["operations"]),
            "shared_skill": "service-workflow"}
