"""Validate synthetic P0 development evidence without running an Agent or API.

This script uses the repository's OpenAPI, never credentials or live services.
The dialogue examples are data for future semantic tests, not a language parser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "materials/client_api/openapi.yaml"
FIXTURE = ROOT / "tests/fixtures/p0_baseline.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def local_refs(value: object) -> None:
    """Keep schema resolution entirely local, including future contract changes."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                require(isinstance(child, str) and child.startswith("#/"),
                        "External schema references are not supported offline")
            local_refs(child)
    elif isinstance(value, list):
        for child in value:
            local_refs(child)


def unique_index(records: list[dict], key: str) -> dict[str, dict]:
    result = {record[key]: record for record in records}
    require(len(result) == len(records), f"Duplicate record identifiers: {key}")
    return result


def finite_money(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"price", "amount", "balance", "price_difference"}:
                require(type(child) in {int, float} and math.isfinite(child),
                        f"Non-finite or non-numeric monetary field: {key}")
            finite_money(child)
    elif isinstance(value, list):
        for child in value:
            finite_money(child)


def validate() -> dict:
    contract_bytes = CONTRACT.read_bytes()
    spec = json.loads(contract_bytes)
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    digest = hashlib.sha256(contract_bytes).hexdigest()
    require(fixture["contract_source"] == CONTRACT.relative_to(ROOT).as_posix(),
            "Fixture points to a different contract")
    require(fixture["contract_sha256"] == digest, "Fixture contract hash is stale")
    require(fixture["stage"] == "P0", "Unexpected fixture stage")
    local_refs(spec)
    operations = {}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                identifier = operation["operationId"]
                require(identifier not in operations, "Duplicate operationId")
                operations[identifier] = (method.upper(), path, operation)
    require(len(operations) == 14, "Expected the reviewed 14-operation baseline")
    mutating = sum(op[2]["x-api-mutates-state"] for op in operations.values())
    require(mutating == 8, "Read/write classification changed")
    for _, _, operation in operations.values():
        writes = operation["x-api-mutates-state"]
        require(operation["x-api-idempotency"] == ("not_guaranteed" if writes else "safe") and
                operation["x-api-automatic-retries"] == ("forbidden" if writes else "allowed"),
                "Idempotency/retry contract changed")
        require(operation["x-api-consistency"] == "strong" and
                operation["x-api-pagination"] == "none" and
                operation["x-api-request-body-max-bytes"] == 1048576 and
                operation["x-api-response-body-max-bytes"] == 4194304,
                "Consistency, pagination or capacity contract changed")
        for status in (400, 404, 405, 409, 413, 422, 502):
            error_shape = operation["responses"][str(status)]["content"]["application/json"]["schema"]
            require(error_shape == {"$ref": "#/components/schemas/APIError"},
                    "Error envelope contract changed")
    schemas = spec["components"]["schemas"]
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    counts = Counter()

    def validator(shape: dict) -> Draft202012Validator:
        # Local OpenAPI references resolve against this root, with no resolver I/O.
        document = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                    **shape, "components": spec["components"]}
        Draft202012Validator.check_schema(document)
        return Draft202012Validator(document)

    def check(shape: dict, value: object, kind: str) -> None:
        validator(shape).validate(value)
        counts[kind] += 1

    def body_schema(identifier: str) -> dict:
        return operations[identifier][2]["requestBody"]["content"]["application/json"]["schema"]

    def response_schema(identifier: str, status: int = 200) -> dict:
        return operations[identifier][2]["responses"][str(status)]["content"]["application/json"]["schema"]

    def example_schema(example: dict) -> dict:
        kind = example["schema_kind"]
        if kind == "component":
            return schemas[example["schema_name"]]
        if kind == "request":
            return body_schema(example["operation_id"])
        require(kind == "response", "Unknown example schema kind")
        return response_schema(example["operation_id"], example["status_code"])

    wire = fixture["wire_examples"]
    require(Counter(row["operation_id"] for row in wire) == Counter(operations.keys()),
            "Normal wire examples must cover every operation exactly once")
    for row in wire + fixture["additional_wire_examples"]:
        method, path, operation = operations[row["operation_id"]]
        request, response = row["request"], row["response"]
        require(request["method"] == method and request["path_template"] == path,
                f"Wrong method/path: {row['operation_id']}")
        parameters = operation.get("parameters", [])
        require(all(p["in"] == "path" for p in parameters), "Unexpected parameter location")
        require(set(request["path_parameters"]) == {p["name"] for p in parameters},
                f"Wrong path parameter names: {row['operation_id']}")
        for parameter in parameters:
            check(parameter["schema"], request["path_parameters"][parameter["name"]],
                  "path_parameters")
        if "requestBody" in operation:
            check(body_schema(row["operation_id"]), request["body"], "request_bodies")
        else:
            require("body" not in request, "Body supplied to operation without requestBody")
        require(200 <= response["status_code"] < 300, "Wire example must be a normal response")
        check(response_schema(row["operation_id"], response["status_code"]),
              response["body"], "normal_responses")
        finite_money(row)

    for row in fixture["request_variants"]:
        check(body_schema(row["operation_id"]), row["body"], "request_bodies")
    records = fixture["records"]
    customers = unique_index(records["customers"], "customer_id")
    products = unique_index(records["products"], "product_id")
    orders = unique_index(records["orders"], "order_id")
    variants = {}
    variant_products = {}
    for customer in customers.values():
        check(response_schema("getCustomer"), customer, "record_snapshots")
        unique_index(customer["payment_methods"], "id")
        require(len(customer["order_ids"]) == len(set(customer["order_ids"])),
                "Duplicate order IDs in a customer snapshot")
    for product in products.values():
        check(response_schema("getProduct"), product, "record_snapshots")
        for item in product["items"]:
            check(schemas["CatalogItem"], item, "record_snapshots")
            require(item["item_id"] not in variants, "Duplicate catalog variant identifier")
            variants[item["item_id"]] = item
            variant_products[item["item_id"]] = product["product_id"]
    for order in orders.values():
        check(response_schema("getOrder"), order, "record_snapshots")
        customer = customers[order["customer_id"]]
        require(order["order_id"] in customer["order_ids"], "Order absent from owner's snapshot")
        saved = {method["id"] for method in customer["payment_methods"]}
        for payment in order["payments"]:
            require(payment["payment_method_id"] in saved, "Unknown payment method reference")
        for item in order["items"]:
            require(variant_products[item["item_id"]] == item["product_id"],
                    "Order variant points to a different product")
            require(item["options"] == variants[item["item_id"]]["options"],
                    "Order variant options disagree with catalog")
            # Historical sale prices may differ from the current catalog price.
        for fulfillment in order["fulfillments"]:
            require(not (Counter(fulfillment["item_ids"]) -
                         Counter(item["item_id"] for item in order["items"])),
                    "Fulfillment exceeds order item occurrences")
    for customer in customers.values():
        require(set(customer["order_ids"]) ==
                {oid for oid, order in orders.items() if order["customer_id"] == customer["customer_id"]},
                "Customer references another customer's order")
    finite_money(records)
    require(sum(item["item_id"] == "p0_shirt_s_blue"
                for item in orders["#P0-PENDING"]["items"]) == 2,
            "Duplicate item occurrences were lost")
    ambiguous_prices = {item["price"] for item in orders["#P0-AMBIGUOUS"]["items"]
                        if item["item_id"] == "p0_shirt_s_blue"}
    require(len(ambiguous_prices) == 2, "Ambiguous historical prices were lost")
    require(fixture["additional_wire_examples"][0]["response"]["body"]["exchange"]["price_difference"] < 0,
            "Missing negative exchange difference sample")

    rejected = []
    for row in fixture["negative_schema_examples"]:
        errors = list(validator(example_schema(row)).iter_errors(row["value"]))
        require(bool(errors), f"Negative example unexpectedly accepted: {row['id']}")
        rejected.append({"id": row["id"], "schema_errors": len(errors)})
    for row in fixture["schema_valid_policy_counterexamples"]:
        check(example_schema(row), row["value"], "schema_valid_policy_counterexamples")
        require(bool(row["required_behavior"]), "Policy counterexample has no expected behavior")
    for row in fixture["identity_dialogue_blueprints"]:
        require(bool(row["messages"]) and all(isinstance(m, str) and m for m in row["messages"]),
                "Dialogue blueprint messages must be nonempty strings")
        require("eventual_selector" in row or "required_behavior" in row,
                "Dialogue blueprint has no expectation")
        if "eventual_selector" in row:
            check(body_schema("searchCustomers"), row["eventual_selector"], "dialogue_selector_shapes")
    for row in fixture["error_examples"]:
        require(str(row["status_code"]) in operations["getOrder"][2]["responses"],
                "Unknown API error status")
        check(schemas["APIError"], row["body"], "error_envelopes")

    policy_text = (ROOT / "docs/POLICY-REGISTER.md").read_text(encoding="utf-8")
    coverage_text = (ROOT / "docs/COVERAGE-MATRIX.md").read_text(encoding="utf-8")
    policy_rows = re.findall(r"^\| ((?:[A-Z]+)-\d{2}) \|", policy_text, re.MULTILINE)
    coverage_rows = re.findall(r"^\| (CV-\d{2}) \|", coverage_text, re.MULTILINE)
    policies, coverage = set(policy_rows), set(coverage_rows)
    require(len(policies) == len(policy_rows), "Duplicate policy identifiers")
    require(len(coverage) == len(coverage_rows) == 22, "Coverage matrix must have 22 unique rows")
    require(set(re.findall(r"\bCV-\d{2}\b", policy_text)) <= coverage,
            "Policy references missing coverage rows")
    require(set(re.findall(r"\b(?:AUTH|FACT|CONF|STATE|ADDR|PAY|CANCEL|ITEM|RET|EXCH|MONEY|LIMIT|HUMAN|API|REPLAY|ARCH)-\d{2}\b",
                           coverage_text)) <= policies, "Coverage references missing policies")
    for row in fixture["schema_valid_policy_counterexamples"] + fixture["identity_dialogue_blueprints"]:
        require(set(row["policies"]) <= policies, "Fixture references a missing policy")
    for group in ("request_variants", "negative_schema_examples", "schema_valid_policy_counterexamples",
                  "identity_dialogue_blueprints", "additional_wire_examples"):
        unique_index(fixture[group], "id")
    baseline_text = (ROOT / "docs/API-BASELINE.md").read_text(encoding="utf-8")
    require(digest in baseline_text, "API documentation contract hash is stale")
    require(all(f"### `{identifier}`" in baseline_text for identifier in operations),
            "An operation is missing from the API documentation")

    return {
        "status": "passed",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_kind": "Offline schema and synthetic evidence consistency; no Agent, LLM, API or evaluation executed",
        "contract_sha256": digest,
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "operations": {"total": len(operations), "read": len(operations) - mutating,
                       "business_write": mutating - 1, "transfer": 1},
        "schema_checks": dict(counts),
        "accepted_schema_samples": sum(counts.values()),
        "negative_examples_rejected": rejected,
        "records": {"customers": len(customers), "products": len(products),
                    "catalog_variants": len(variants), "orders": len(orders)},
        "policy_entries": len(policies),
        "coverage_rows": len(coverage),
        "identity_dialogue_blueprints": {"count": len(fixture["identity_dialogue_blueprints"]),
                                         "semantic_tests_executed": False},
        "fixture_relations_and_monetary_types": "passed",
        "scope": {"binding": "3d81f316-16c1-452d-939f-48776870bd23", "branch": "main",
                  "domain": "retail_plus", "language": "python", "task": "t1", "case_ids": None},
        "business_agent_implemented": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write a successful validation report inside this repository")
    args = parser.parse_args()
    try:
        destination = None
        if args.report is not None:
            destination = (ROOT / args.report).resolve()
            require(destination.is_relative_to(ROOT), "Report destination must be inside the repository")
            require(destination.suffix == ".json", "Report must use a .json extension")
            require(destination not in {CONTRACT.resolve(), FIXTURE.resolve()}, "Cannot overwrite input evidence")
        report = validate()
        output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(output, encoding="utf-8")
        print(output, end="")
        return 0
    except (ValueError, KeyError, OSError, ValidationError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
