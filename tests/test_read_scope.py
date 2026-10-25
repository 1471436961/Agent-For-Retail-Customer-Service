"""P2 authorization, exact REST contracts and replay on synthetic records."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from fixtures.p2_backend import SyntheticBackend
from support_agent.adapters import read_models
from support_agent.adapters.customer_tools import READ_ACTION_NAMES
from support_agent.adapters.read_api import read_customer, read_order, read_product, read_catalog_item
from support_agent.domain.identity import ReadError
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class ReadScopeTests(unittest.TestCase):
    def setUp(self):
        self.backend = SyntheticBackend()
        self.api = ClientAPI(self.backend, context=ClientAPIContext(conversation_id="synthetic_p2"))
        self.kit = Tools(self.api)
        self.customer = self.backend.records["customers"][0]

    def verify(self):
        return self.kit.verify_customer(email="alex.p0@example.test")

    def test_read_item_total_counts_occurrences_without_netting_payments(self):
        order = self.backend.records["orders"][0]
        repeated = deepcopy(order["items"][0])
        order["items"] = [{**deepcopy(repeated),"price":0.1},
                          {**deepcopy(repeated),"price":0.2},
                          {**deepcopy(repeated),"price":0.1}]
        order["payments"] = [{"transaction_type":"payment","amount":999.0,
                              "payment_method_id":self.customer["payment_methods"][0]["id"]}]
        verified = self.kit.verify_customer(email=self.customer["email"],order_id=order["order_id"])
        view = verified["requested_order"]
        self.assertEqual(view["item_price_total"]["result"],0.4)
        self.assertEqual(view["item_price_total"]["inputs"],[0.1,0.2,0.1])
        self.assertEqual(len(view["items"]),3)
        self.assertNotIn("item_price_total",order)
        self.assertNotIn("item_price_total",self.kit._observed_orders[order["order_id"]])

    def test_email_matches_independently_before_profile(self):
        self.assertEqual(self.verify(), self.customer)
        self.assertEqual([(row["method"], row["path"]) for row in self.backend.calls],
                         [("POST", "/v1/customers/search"), ("GET", "/v1/customers/p0_customer_a")])
        self.assertEqual(self.backend.calls[0]["body"], {"email": "alex.p0@example.test"})

    def test_full_name_and_zip_selector_preserves_strings(self):
        actual = self.kit.verify_customer(first_name="Alex", last_name="Li", postal_code="10001")
        self.assertEqual(actual, self.customer)
        self.assertEqual(self.backend.calls[0]["body"],
                         {"first_name": "Alex", "last_name": "Li", "postal_code": "10001"})
        self.assertNotIn("email", self.backend.calls[0]["body"])

    def test_observed_balance_string_is_normalized_only_after_verification(self):
        record = deepcopy(self.customer)
        gift_index = next(i for i, method in enumerate(record["payment_methods"])
                          if method["source"] == "gift_card")
        record["payment_methods"][gift_index]["balance"] = "500.0"
        self.backend.overrides[("GET", "/v1/customers/p0_customer_a")] = self.backend.response(record)
        output = self.kit.verify_customer(email="alex.p0@example.test", claimed_customer_id="p0_customer_a")
        self.assertEqual(output["payment_methods"][gift_index]["balance"], 500.0)
        self.assertIs(type(output["payment_methods"][gift_index]["balance"]), float)
        self.assertEqual(output["response_warnings"], [{"code": "numeric_string_balance",
                         "path": f"payment_methods[{gift_index}].balance", "original_value": "500.0"}])
        self.assertEqual(record["payment_methods"][gift_index]["balance"], "500.0")
        self.assertEqual([row["path"] for row in self.backend.calls],
                         ["/v1/customers/search", "/v1/customers/p0_customer_a"])

    def test_invalid_balance_strings_never_become_zero_or_valid_records(self):
        for raw in ("", "NaN", "Infinity", "-Infinity", "true", "null", "1_000", " 500.0 ",
                    "1e1000", "1e-1000", "1e999999999999999999999", "[]", "{}", "not-money"):
            with self.subTest(raw=raw):
                record = deepcopy(self.customer)
                gift = next(m for m in record["payment_methods"] if m["source"] == "gift_card")
                gift["balance"] = raw
                self.backend.overrides[("GET", "/v1/customers/p0_customer_a")] = self.backend.response(record)
                receipt = self.verify()
                self.assertEqual(receipt["verification"]["profile_state"], "unavailable")
                self.assertNotIn("payment_methods", receipt)
                with self.assertRaises(ReadError) as caught:
                    self.kit.get_customer_summary()
                self.assertEqual(caught.exception.code, "invalid_read_response")

    def test_balance_compatibility_cannot_turn_id_mismatch_into_profile_access(self):
        record = deepcopy(self.customer)
        next(m for m in record["payment_methods"] if m["source"] == "gift_card")["balance"] = "500.0"
        self.backend.overrides[("GET", "/v1/customers/p0_customer_a")] = self.backend.response(record)
        with self.assertRaises(ReadError):
            self.kit.verify_customer(email="alex.p0@example.test", claimed_customer_id="p0_customer_b")
        self.assertEqual([row["path"] for row in self.backend.calls], ["/v1/customers/search"])

    def test_incomplete_mixed_or_id_only_selectors_never_contact_backend(self):
        for arguments in ({}, {"claimed_customer_id": "p0_customer_a"}, {"first_name": "Alex"},
                          {"first_name": "Alex", "last_name": "Li"}, {"email": "   "},
                          {"email": "alex.p0@example.test", "postal_code": "10001"},
                          {"first_name": "Alex", "last_name": "Li", "postal_code": 10001}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ReadError):
                    self.kit.verify_customer(**arguments)
        self.assertEqual(self.backend.calls, [])

    def test_correct_claim_is_only_a_match_check(self):
        result = self.kit.verify_customer(email="alex.p0@example.test", claimed_customer_id="p0_customer_a")
        self.assertEqual(result, self.customer)
        self.assertNotIn("customer_id", self.backend.calls[0]["body"])

    def test_claim_mismatch_never_reads_any_profile(self):
        with self.assertRaises(ReadError) as caught:
            self.kit.verify_customer(email="alex.p0@example.test", claimed_customer_id="p0_customer_b")
        self.assertEqual(caught.exception.code, "identity_verification_failed")
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.backend.calls[0]["path"], "/v1/customers/search")
        self.assertIsNone(self.kit._identity.customer_id)

    def test_not_found_and_claim_mismatch_are_indistinguishable(self):
        outcomes = []
        for arguments in ({"email": "not-present@example.test"},
                          {"email": "alex.p0@example.test", "claimed_customer_id": "wrong"}):
            with self.assertRaises(ReadError) as caught:
                self.kit.verify_customer(**arguments)
            outcomes.append(str(caught.exception))
        self.assertEqual(outcomes, ["identity_verification_failed"] * 2)
        self.assertTrue(all(row["path"] == "/v1/customers/search" for row in self.backend.calls))

    def test_failed_verification_can_be_corrected_without_invented_lockout(self):
        for _ in range(4):
            with self.assertRaises(ReadError):
                self.kit.verify_customer(first_name="Alex", last_name="Li", postal_code="wrong")
        self.assertEqual(self.kit.verify_customer(first_name="Alex", last_name="Li", postal_code="10001"), self.customer)

    def test_profile_reads_fail_before_verification(self):
        actions = [lambda: self.kit.get_customer_summary(), lambda: self.kit.list_customer_orders(),
                   lambda: self.kit.get_owned_order("#P0-PENDING"), lambda: self.kit.list_products(),
                   lambda: self.kit.get_product_variants("p0_shirt"), lambda: self.kit.get_catalog_item("p0_shirt_s_blue"),
                   lambda: self.kit.get_catalog_summary()]
        for action in actions:
            with self.assertRaises(ReadError) as caught:
                action()
            self.assertEqual(caught.exception.code, "identity_required")
        self.assertEqual(self.backend.calls, [])

    def test_verified_customer_cannot_switch_by_email_name_or_compatibility_tool(self):
        self.verify()
        for action in (
            lambda: self.kit.verify_customer(email="casey.p0@example.test"),
            lambda: self.kit.verify_customer(first_name="Casey", last_name="Roe", postal_code="30301"),
            lambda: self.kit.lookup_customer("p0_customer_b", "casey.p0@example.test"),
        ):
            with self.assertRaises(ReadError) as caught:
                action()
            self.assertEqual(str(caught.exception), "identity_verification_failed")
        self.assertEqual(self.kit.get_customer_summary(), self.customer)
        self.assertFalse(any(row["path"] == "/v1/customers/p0_customer_b" for row in self.backend.calls))

    def test_returned_profile_mutation_does_not_grant_access(self):
        record = self.verify()
        record["customer_id"] = "p0_customer_b"
        record["order_ids"].append("#P0-FOREIGN")
        with self.assertRaises(ReadError):
            self.kit.get_owned_order("#P0-FOREIGN")
        self.assertEqual(self.kit._identity.customer_id, "p0_customer_a")
        self.assertEqual(self.customer["customer_id"], "p0_customer_a")

    def test_known_and_unknown_foreign_order_ids_do_not_trigger_order_read(self):
        self.verify()
        for identifier in ("#P0-FOREIGN", "#NO-SUCH-ORDER"):
            with self.assertRaises(ReadError) as caught:
                self.kit.get_owned_order(identifier)
            self.assertEqual(caught.exception.code, "order_access_denied")
        self.assertFalse(any(row["path"].startswith("/v1/orders/") for row in self.backend.calls))

    def test_actual_owner_is_checked_even_if_profile_list_is_wrong(self):
        self.verify()
        self.customer["order_ids"].append("#P0-FOREIGN")
        with self.assertRaises(ReadError) as caught:
            self.kit.get_owned_order("#P0-FOREIGN")
        self.assertEqual(caught.exception.code, "order_access_denied")
        self.assertNotIn("casey", str(caught.exception))
        self.assertNotIn("p0_customer_b", str(caught.exception))

    def test_current_own_list_is_refreshed_for_each_order(self):
        self.verify()
        self.customer["order_ids"].remove("#P0-PENDING")
        with self.assertRaises(ReadError):
            self.kit.get_owned_order("#P0-PENDING")
        self.assertFalse(any(row["path"].startswith("/v1/orders/") for row in self.backend.calls))

    def test_owned_order_preserves_repetition_shipping_and_payment_direction(self):
        self.verify()
        for identifier in ("#P0-PENDING", "#P0-AMBIGUOUS", "#P0-CANCELLED", "#P0-PROCESSED"):
            expected = next(order for order in self.backend.records["orders"] if order["order_id"] == identifier)
            actual = self.kit.get_owned_order(identifier)
            total = actual.pop("item_price_total")
            self.assertEqual(total["inputs"], [i["price"] for i in expected["items"]])
            self.assertEqual(total["result"], round(sum(i["price"] for i in expected["items"]), 2))
            self.assertNotIn("item_price_total", self.kit._observed_orders[identifier])
            self.assertEqual(actual, expected)
        repeated = self.kit.get_owned_order("#P0-AMBIGUOUS")["items"]
        self.assertEqual([item["price"] for item in repeated[:2]], [20.0, 18.0])
        cancelled = self.kit.get_owned_order("#P0-CANCELLED")
        self.assertEqual([p["transaction_type"] for p in cancelled["payments"]], ["payment", "refund"])
        self.assertIn("%23P0", self.backend.calls[-1]["path"])

    def test_order_ids_are_full_list_without_invented_dates(self):
        self.verify()
        self.assertEqual(self.kit.list_customer_orders(), {"order_ids": self.customer["order_ids"]})

    def test_combined_verification_reuses_only_reads_from_that_same_action(self):
        target=self.customer["order_ids"][0]
        actual=self.kit.verify_customer(email=self.customer["email"],order_id=target,
            include_order_details=True,skill_names=["address-changes"])
        selected=[r for r in self.backend.calls if r["method"]=="GET"]
        self.assertEqual(len([r for r in selected if r["path"]=="/v1/customers/"+self.customer["customer_id"]]),1)
        from urllib.parse import quote
        target_path="/v1/orders/"+quote(target,safe="")
        self.assertEqual(len([r for r in selected if r["path"]==target_path]),1)
        context=actual["requested_context"]
        self.assertEqual(next(o for o in context["orders"] if o["order_id"]==target),actual["requested_order"])
        before=len(self.backend.calls)
        self.kit.get_owned_order(target)
        self.assertTrue(any(r["path"]==target_path for r in self.backend.calls[before:]))

    def test_full_public_catalog_is_allowed_outside_purchase_history(self):
        self.verify()
        self.customer["order_ids"] = []
        actual = self.kit.list_products()
        self.assertEqual(actual, {"products": [{"product_id": p["product_id"], "name": p["name"]}
                                               for p in self.backend.records["products"]]})
        product = self.kit.get_product_variants("p0_shirt")
        raw = self.backend.records["products"][0]
        self.assertEqual(product, {**raw, "variant_count": len(raw["items"]),
                                  "available_variant_count": sum(item["available"] for item in raw["items"])})
        item = self.kit.get_catalog_item("p0_shirt_s_blue")
        self.assertNotIn("product_id", item)
        self.assertIsInstance(item["available"], bool)

    def test_catalog_counts_are_distinct_and_derived_from_complete_records(self):
        self.verify()
        self.backend.calls.clear()
        summary = self.kit.get_catalog_summary()
        self.assertEqual([summary[key] for key in ("product_type_count", "variant_count", "available_variant_count")], [2, 5, 4])
        self.assertEqual(summary["products"], self.backend.records["products"])
        self.assertEqual([row["path"] for row in self.backend.calls],
                         ["/v1/catalog/products", "/v1/catalog/products/p0_shirt", "/v1/catalog/products/p0_mug"])
        self.backend.records["products"][0]["items"][0]["available"] = False
        self.assertEqual(self.kit.get_catalog_summary()["available_variant_count"], 3)

    def test_empty_catalog_counts_are_zero_without_missing_becoming_zero(self):
        self.verify()
        self.backend.records["products"] = []
        self.assertEqual(self.kit.get_catalog_summary(),
                         {"product_type_count": 0, "variant_count": 0, "available_variant_count": 0, "products": []})
        self.backend.overrides[("GET", "/v1/catalog/products")] = self.backend.response({})
        with self.assertRaises(ReadError):
            self.kit.get_catalog_summary()

    def test_catalog_failure_yields_no_partial_counts(self):
        self.verify()
        self.backend.overrides[("GET", "/v1/catalog/products/p0_mug")] = self.backend.missing()
        with self.assertRaises(ReadError) as caught:
            self.kit.get_catalog_summary()
        self.assertEqual(caught.exception.http_status, 404)

    def test_all_path_identifiers_are_encoded_as_single_opaque_segment(self):
        for reader, original, field, prefix in (
            (read_customer, self.customer, "customer_id", "/v1/customers/"),
            (read_order, self.backend.records["orders"][0], "order_id", "/v1/orders/"),
            (read_product, self.backend.records["products"][0], "product_id", "/v1/catalog/products/"),
            (read_catalog_item, self.backend.records["products"][0]["items"][0], "item_id", "/v1/catalog/items/"),
        ):
            record = deepcopy(original)
            record[field] = "#x/a?b% c"
            path = prefix + "%23x%2Fa%3Fb%25%20c"
            self.backend.overrides[("GET", path)] = self.backend.response(record)
            self.assertEqual(reader(self.api, record[field]), record)
            self.assertEqual(self.backend.calls[-1]["path"], path)
            self.assertEqual(self.backend.calls[-1]["query"], {})
            self.assertIsNone(self.backend.calls[-1]["body"])

    def test_response_id_redirection_cannot_read_another_customer_or_resource(self):
        self.backend.overrides[("GET", "/v1/customers/p0_customer_a")] = self.backend.response(self.backend.records["customers"][1])
        receipt = self.verify()
        self.assertEqual(receipt["profile_error"]["code"], "invalid_read_response")
        self.assertEqual(receipt["verification"]["profile_state"], "unavailable")
        self.assertNotIn("casey", json.dumps(receipt))
        self.assertEqual(self.kit._identity.customer_id, "p0_customer_a")

    def test_detail_failure_cannot_release_the_session_binding(self):
        self.backend.overrides[("GET", "/v1/customers/p0_customer_a")] = self.backend.missing()
        receipt = self.verify()
        self.assertEqual(receipt["profile_error"]["http_status"], 404)
        self.assertEqual(receipt["verification"]["profile_state"], "unavailable")
        with self.assertRaises(ReadError) as caught:
            self.kit.verify_customer(email="casey.p0@example.test")
        self.assertEqual(str(caught.exception), "identity_verification_failed")
        self.assertEqual(self.kit._identity.customer_id, "p0_customer_a")

    def test_malformed_search_result_cannot_authorize_details(self):
        for body in ({"customer_id": None}, {}, {"customer_id": ""}, {"customer_id": "p0_customer_a", "notes": "extra"}):
            self.backend.calls.clear()
            self.backend.overrides[("POST", "/v1/customers/search")] = self.backend.response(body)
            with self.assertRaises(ReadError):
                self.verify()
            self.assertEqual(len(self.backend.calls), 1)
            self.assertIsNone(self.kit._identity.customer_id)

    def test_http_error_status_and_code_are_distinguished_without_backend_text(self):
        self.verify()
        for status, code in ((400, "invalid_body"), (404, "resource_not_found"), (405, "method_not_allowed"),
                             (409, "resource_conflict"), (413, "request_too_large"),
                             (422, "business_rule_violation"), (502, "response_too_large"), (503, "private-secret")):
            self.backend.overrides[("GET", "/v1/catalog/products")] = self.backend.response(
                {"error": {"code": code, "message": "SECRET-record-email@example.test", "details": self.customer}}, status)
            previous = len(self.backend.calls)
            with self.assertRaises(ReadError) as caught:
                self.kit.list_products()
            self.assertEqual(caught.exception.http_status, status)
            self.assertEqual(caught.exception.api_code, None if status == 503 else code)
            self.assertEqual(len(self.backend.calls), previous + 1)
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertNotIn("private-secret", str(caught.exception))

    def test_transport_exception_is_safe_and_not_retried(self):
        self.verify()
        self.backend.overrides[("GET", "/v1/catalog/products")] = RuntimeError("credential=must-not-leak")
        previous = len(self.backend.calls)
        with self.assertRaises(ReadError) as caught:
            self.kit.list_products()
        self.assertEqual(caught.exception.code, "read_transport_failure")
        self.assertNotIn("credential", str(caught.exception))
        self.assertEqual(len(self.backend.calls), previous + 1)

    def test_real_client_api_request_capacity_rejects_before_backend(self):
        with self.assertRaises(ReadError) as caught:
            self.kit.verify_customer(email="x" * 1048576)
        self.assertEqual(caught.exception.http_status, 413)
        self.assertEqual(caught.exception.api_code, "request_too_large")
        self.assertEqual(self.backend.calls, [])
        self.assertIsNone(self.kit._identity.customer_id)

    def test_real_client_api_response_capacity_does_not_truncate_or_retry(self):
        self.verify()
        product = deepcopy(self.backend.records["products"][0])
        product["name"] = "x" * 4194304
        self.backend.overrides[("GET", "/v1/catalog/products/p0_shirt")] = self.backend.response(product)
        previous = len(self.backend.calls)
        with self.assertRaises(ReadError) as caught:
            self.kit.get_product_variants("p0_shirt")
        self.assertEqual(caught.exception.http_status, 502)
        self.assertEqual(caught.exception.api_code, "response_too_large")
        self.assertEqual(len(self.backend.calls), previous + 1)

    def test_non_normal_body_or_status_is_not_silently_accepted(self):
        self.verify()
        for body, status in (([], 200), ({"products": []}, 202), ({"products": [], "extra": True}, 200)):
            self.backend.overrides[("GET", "/v1/catalog/products")] = self.backend.response(body, status)
            with self.assertRaises(ReadError):
                self.kit.list_products()

    def test_money_type_unknown_state_and_extra_fields_are_rejected(self):
        self.verify()
        invalids = []
        for value in (True, None, "not-money", float("nan"), float("inf")):
            record = deepcopy(self.backend.records["orders"][0])
            record["items"][0]["price"] = value
            invalids.append(record)
        record = deepcopy(self.backend.records["orders"][0]); record["status"] = "shipped"; invalids.append(record)
        record = deepcopy(self.backend.records["orders"][0]); record["items"][0]["quantity"] = 2; invalids.append(record)
        for record in invalids:
            self.backend.overrides[("GET", "/v1/orders/%23P0-PENDING")] = self.backend.response(record)
            with self.assertRaises(ReadError):
                self.kit.get_owned_order("#P0-PENDING")

    def test_fresh_toolkits_and_context_reset_do_not_inherit_identity(self):
        self.verify()
        other = Tools(self.api)
        with self.assertRaises(ReadError):
            other.get_customer_summary()
        self.api._set_context(ClientAPIContext(conversation_id="another_synthetic_conversation"))
        with self.assertRaises(ReadError):
            self.kit.get_customer_summary()
        self.assertEqual(self.kit.verify_customer(email="casey.p0@example.test")["customer_id"], "p0_customer_b")

    def test_registered_tools_offer_no_direct_customer_or_write_backdoor(self):
        tools = self.kit.get_tools()
        self.assertTrue(set(READ_ACTION_NAMES).issubset(tools))
        self.assertTrue(all(tools[name].info["tool_type"] == "read" for name in READ_ACTION_NAMES))
        for name, tool in tools.items():
            if tool.info["tool_type"] == "write":
                explicit = {
                    "update_default_address":{"address","request_id"},
                    "update_order_address":{"order_id","address","request_id"},
                    "cancel_order":{"order_id","reason","request_id"},
                    "change_order_payment":{"order_id","payment_method_id","request_id"},
                    "request_return":{"order_id","item_ids","refund_payment_method_id","specific_instance","request_id"},
                    "modify_order_items":{"order_id","replacements","payment_method_id","preserve_options","specific_instance","request_id"},
                    "request_exchange":{"order_id","replacements","payment_method_id","preserve_options","specific_instance","request_id"},
                }
                expected = explicit.get(name, {"kind", "parameters", "request_id"} if name == "submit_change" else
                                        {"requests"} if name == "submit_changes" else {"proposal_id"})
                self.assertEqual(set(tool.params.model_json_schema()["properties"]), expected)
        for name in ("get_customer_summary", "list_customer_orders", "get_owned_order"):
            self.assertNotIn("customer_id", tools[name].params.model_json_schema()["properties"])
        self.verify()
        self.kit.get_owned_order("#P0-PENDING")
        self.kit.get_catalog_summary()
        self.assertTrue(all(row["method"] == "GET" or
                            (row["method"], row["path"]) == ("POST", "/v1/customers/search") for row in self.backend.calls))

    def test_tool_trajectory_replays_with_new_backend_and_toolkit(self):
        sequence = [("verify_customer", {"email": "alex.p0@example.test"}),
                    ("get_owned_order", {"order_id": "#P0-AMBIGUOUS"}), ("get_catalog_summary", {}),
                    ("get_owned_order", {"order_id": "#P0-FOREIGN"}),
                    ("verify_customer", {"email": "casey.p0@example.test"}), ("get_customer_summary", {})]
        runs = []
        for _ in range(2):
            backend = SyntheticBackend()
            kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="synthetic_replay")))
            outcomes = []
            for name, arguments in sequence:
                try:
                    outcomes.append({"result": kit.get_tools()[name](**arguments)})
                except ReadError as error:
                    outcomes.append({"error": str(error)})
            runs.append((outcomes, backend.calls, kit._identity.customer_id))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0][2], "p0_customer_a")

    def test_read_models_match_reviewed_openapi_shapes(self):
        spec = json.loads((ROOT / "materials/client_api/openapi.yaml").read_text(encoding="utf-8"))

        def expanded(node, document):
            if isinstance(node, list):
                return [expanded(child, document) for child in node]
            if not isinstance(node, dict):
                return node
            if "$ref" in node:
                target = document
                for part in node["$ref"].split("/")[1:]:
                    target = target[part]
                return expanded(target, document)
            result = {key: expanded(value, document) for key, value in node.items()
                      if key not in {"title", "description", "default", "$defs"}}
            if "anyOf" in result:
                result["anyOf"] = sorted(result["anyOf"], key=lambda value: json.dumps(value, sort_keys=True))
            return result

        for paths in spec["paths"].values():
            for operation in paths.values():
                name = operation["operationId"]
                if name not in read_models.READ_MODELS:
                    continue
                current = read_models.READ_MODELS[name].model_json_schema()
                expected = operation["responses"]["200"]["content"]["application/json"]["schema"]
                self.assertEqual(expanded(current, current), expanded(expected, spec), name)

    def test_full_name_model_host_loop_receives_unparsed_multi_turn_context(self):
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()),
                                      resource_root=ROOT / "agent", model_configs=[{"model": "offline_p2", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(context):
            agent = create_agent()
        state = agent.get_init_state()
        with patch("tau2.hyper.agent_context.generate") as provider:
            provider.return_value = AssistantMessage(role="assistant", content="请提供账户邮编。")
            _, state = agent.generate_next_message(UserMessage(role="user", content="Li 是姓，名字是 Alex。"), state)
            provider.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="verify_full_name", name="verify_customer",
                arguments={"first_name": "Alex", "last_name": "Li", "postal_code": "10001", "claimed_customer_id": None, "skill_names": ["service-workflow"]})])
            reply, state = agent.generate_next_message(UserMessage(role="user", content="刚才写错了，账户邮编是10001。"), state)
            self.assertEqual(self.backend.calls, [])
            self.assertEqual(provider.call_args.kwargs["messages"][1].content, "Li 是姓，名字是 Alex。")
            result = self.kit.get_tools()[reply.tool_calls[0].name](**reply.tool_calls[0].arguments)
            provider.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="own_order", name="get_owned_order", arguments={"order_id": "#P0-PENDING"})])
            reply, state = agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
                ToolMessage(role="tool", id="verify_full_name", content=json.dumps(result))]), state)
            order = self.kit.get_tools()[reply.tool_calls[0].name](**reply.tool_calls[0].arguments)
            provider.return_value = AssistantMessage(role="assistant", content="订单当前是 pending。")
            reply, state = agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[
                ToolMessage(role="tool", id="own_order", content=json.dumps(order))]), state)
            self.assertEqual(provider.call_count, 4)
            self.assertEqual(reply.content, "订单当前是 pending。")
            self.assertEqual(state["pending_call_ids"], [])

    def test_verification_cannot_share_a_batch_with_reads_or_another_verification(self):
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()), resource_root=ROOT / "agent",
                                      model_configs=[{"model": "offline_p2"}])
        with activate_agent_context(context):
            agent = create_agent()
        with patch("tau2.hyper.agent_context.generate", return_value=AssistantMessage(role="assistant", tool_calls=[
            ToolCall(id="verify", name="verify_customer", arguments={"email": "alex.p0@example.test"}),
            ToolCall(id="read", name="get_customer_summary", arguments={}),
        ])):
            reply, state = agent.generate_next_message(UserMessage(role="user", content="Verify and read my account."), agent.get_init_state())
        self.assertIsNone(state["halt_reason"])
        self.assertIsNotNone(state["protocol_diagnostic"])
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
