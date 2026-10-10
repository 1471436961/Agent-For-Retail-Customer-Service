"""Synthetic read backend. It deliberately does NOT enforce toolkit authorization."""

import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote


class SyntheticBackend:
    def __init__(self):
        self.records = json.loads((Path(__file__).parent / "p0_baseline.json").read_text(encoding="utf-8"))["records"]
        self.calls = []
        self.overrides = {}

    @staticmethod
    def response(body, status=200):
        return {"status_code": status, "body": deepcopy(body)}

    @classmethod
    def missing(cls):
        return cls.response({"error": {"code": "resource_not_found", "message": "Synthetic missing record."}}, 404)

    def __call__(self, request):
        self.calls.append(deepcopy(request))
        key = (request["method"], request["path"])
        if key in self.overrides:
            value = self.overrides[key]
            if isinstance(value, Exception):
                raise value
            return deepcopy(value)
        if key == ("POST", "/v1/customers/search"):
            body = request["body"]
            if set(body) == {"email"}:
                matches = [c for c in self.records["customers"] if c["email"] == body["email"]]
            elif set(body) == {"first_name", "last_name", "postal_code"}:
                matches = [c for c in self.records["customers"] if
                           c["name"]["first_name"] == body["first_name"] and
                           c["name"]["last_name"] == body["last_name"] and
                           c["default_shipping_address"]["postal_code"] == body["postal_code"]]
            else:
                raise AssertionError("Unexpected synthetic search shape")
            return self.response({"customer_id": matches[0]["customer_id"]}) if len(matches) == 1 else self.missing()
        if request["method"] != "GET":
            raise AssertionError("P2 must never issue a write")
        if request["path"] == "/v1/catalog/products":
            return self.response({"products": [{"product_id": p["product_id"], "name": p["name"]}
                                                for p in self.records["products"]]})
        for prefix, group, field in (
            ("/v1/customers/", "customers", "customer_id"),
            ("/v1/orders/", "orders", "order_id"),
            ("/v1/catalog/products/", "products", "product_id"),
        ):
            if request["path"].startswith(prefix):
                identifier = unquote(request["path"][len(prefix):])
                found = next((row for row in self.records[group] if row[field] == identifier), None)
                return self.response(found) if found is not None else self.missing()
        prefix = "/v1/catalog/items/"
        if request["path"].startswith(prefix):
            identifier = unquote(request["path"][len(prefix):])
            found = next((item for product in self.records["products"] for item in product["items"]
                          if item["item_id"] == identifier), None)
            return self.response(found) if found is not None else self.missing()
        raise AssertionError("Unexpected synthetic read path")
