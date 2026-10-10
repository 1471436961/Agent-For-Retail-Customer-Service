"""Synthetic state changes through exact public paths; never production code."""
from copy import deepcopy
from urllib.parse import unquote
from fixtures.p2_backend import SyntheticBackend


class WriteBackend(SyntheticBackend):
    def __call__(self, request):
        key = (request["method"], request["path"])
        if key in self.overrides or request["method"] == "GET" or key == ("POST", "/v1/customers/search"):
            return super().__call__(request)
        self.calls.append(deepcopy(request))
        parts = [unquote(part) for part in request["path"].split("/")]
        body = request["body"]
        if parts[2] == "customers" and parts[4] == "default-shipping-address":
            customer = next(c for c in self.records["customers"] if c["customer_id"] == parts[3])
            customer["default_shipping_address"] = deepcopy(body)
            return self.response({"customer_id": parts[3], "default_shipping_address": body})
        order = next(o for o in self.records["orders"] if o["order_id"] == parts[3])
        if parts[4] == "shipping-address":
            order["shipping_address"] = deepcopy(body)
            return self.response({"order_id": parts[3], "shipping_address": body})
        if parts[4] == "cancellations":
            order["status"] = "cancelled"
            order["cancellation"] = deepcopy(body)
            for payment in list(order["payments"]):
                if payment["transaction_type"] == "payment":
                    order["payments"].append({**payment, "transaction_type": "refund"})
            return self.response({k: deepcopy(order[k]) for k in ("order_id", "status", "cancellation", "payments")})
        if parts[4] == "payment-method":
            original = deepcopy(order["payments"][0])
            order["payments"].extend([{**original, "payment_method_id": body["payment_method_id"]}, {**original, "transaction_type": "refund"}])
            return self.response({k: deepcopy(order[k]) for k in ("order_id", "payments")})
        if parts[4] in {"item-modifications", "exchanges"}:
            delta = 0.0
            targets = []
            for row in body["replacements"]:
                old = next(i for i in order["items"] if i["item_id"] == row["existing_item_id"])
                product = next(p for p in self.records["products"] if p["product_id"] == old["product_id"])
                new = next(i for i in product["items"] if i["item_id"] == row["replacement_item_id"])
                delta += new["price"] - old["price"]
                targets.append(new)
            delta = round(delta, 2)
            if parts[4] == "exchanges":
                order["status"] = "exchange requested"
                order["exchange"] = {**deepcopy(body), "price_difference": delta}
                return self.response({k: deepcopy(order[k]) for k in ("order_id", "status", "exchange")})
            for row, new in zip(body["replacements"], targets):
                old = next(i for i in order["items"] if i["item_id"] == row["existing_item_id"])
                old.update(item_id=new["item_id"], price=new["price"], options=deepcopy(new["options"]))
            order["status"] = "pending (items modified)"
            order["payments"].append({"transaction_type": "payment" if delta > 0 else "refund", "amount": abs(delta), "payment_method_id": body["payment_method_id"]})
            return self.response({k: deepcopy(order[k]) for k in ("order_id", "status", "items", "payments")})
        if parts[4] == "returns":
            order["status"] = "return requested"
            order["return_request"] = deepcopy(body)
            return self.response({k: deepcopy(order[k]) for k in ("order_id", "status", "return_request")})
        raise AssertionError("Unknown synthetic write")

    def writes(self):
        return [r for r in self.calls if r["method"] != "GET" and r["path"] != "/v1/customers/search"]
