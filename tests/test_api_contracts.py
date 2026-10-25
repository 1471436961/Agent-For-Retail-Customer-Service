"""Migrated valid REST contracts; independent of retired Agent state machinery."""
import json,unittest
from copy import deepcopy
from typing import get_args
from unittest.mock import patch
from fixtures.native_runtime import reference_kit,serialized_reference_kit,SEED
from test_simple_tools import Clock
from tools import Tools
from tau2.environment.environment import Environment
from tau2.hyper.client_api import ClientAPI
from tau2.hyper.client_api.defects import DefectProfile,RateLimitDefect
from tau2.hyper.client_api.runtime import ClientAPIRuntime
from support_agent.adapters.read_api import read_order,read_catalog_item,read_products
from support_agent.adapters.read_models import Order
from support_agent.adapters.response_values import normalize_response
from support_agent.domain.identity import ReadError


class MigratedScalarContracts(unittest.TestCase):

    def test_catalog_boolean_false_and_option_strings_are_not_truthy_or_numbers(self):
            kit, runtime, requests = serialized_reference_kit()
            case = SEED["cases"][0]
            kit.verify_customer(email=case["email"])
            product = next(iter(runtime.environment.tools.db.products.values()))
            item = next(iter(product.variants.values()))
            item.available = False
            item.options["capacity"] = "300"
            actual = read_catalog_item(kit.client_api, item.item_id)
            self.assertIs(actual["available"], False)
            self.assertEqual(actual["options"]["capacity"], "300")
            self.assertIsInstance(actual["price"], float)

    def test_invalid_numeric_strings_never_become_valid_order_money(self):
            kit, runtime, requests = reference_kit()
            case = SEED["cases"][0]
            original = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + case["order_id"][1:]})
            for raw in ("NaN", "Infinity", "true", "null", "", "1_000", " 48.0 ", "1e1000", "1e-1000", "[]"):
                with self.subTest(raw=raw):
                    response = deepcopy(original)
                    response["body"]["items"][0]["price"] = raw
                    api = ClientAPI(lambda request: response)
                    with self.assertRaises(ReadError) as caught:
                        read_order(api, case["order_id"])
                    self.assertEqual(caught.exception.code, "invalid_read_response")
                    self.assertEqual(caught.exception.validation_issues,
                        [{"path": "items[0].price", "type": "invalid_serialized_scalar"}])

    def test_unknown_field_diagnostic_reveals_neither_key_nor_value(self):
            kit, runtime, requests = reference_kit()
            case = SEED["cases"][0]
            response = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + case["order_id"][1:]})
            response["body"]["private-account-key"] = "private-account-value"
            with self.assertRaises(ReadError) as caught:
                read_order(ClientAPI(lambda request: response), case["order_id"])
            self.assertIn("*:extra_forbidden", str(caught.exception))
            self.assertNotIn("private-account", str(caught.exception))

    def test_missing_required_order_field_is_still_rejected_with_safe_location(self):
            kit, runtime, requests = reference_kit()
            case = SEED["cases"][0]
            response = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + case["order_id"][1:]})
            del response["body"]["payments"][0]["amount"]
            with self.assertRaises(ReadError) as caught:
                read_order(ClientAPI(lambda request: response), case["order_id"])
            self.assertIn("payments.0.amount:missing", str(caught.exception))
            self.assertNotIn(case["customer_id"], str(caught.exception))

    def test_status_field_drift_exposes_observed_schema_without_guessing_an_alias(self):
            kit, runtime, requests = reference_kit()
            case = SEED["cases"][0]
            response = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + case["order_id"][1:]})
            original = deepcopy(response)
            # Synthetic drift names exercise diagnostics, not production aliases.
            for field in ("order_phase", "lifecycle_state"):
                changed = deepcopy(original)
                observed = changed["body"].pop("status")
                changed["body"][field] = observed
                with self.subTest(field=field), self.assertRaises(ReadError) as caught:
                    read_order(ClientAPI(lambda request: changed), case["order_id"])
                shape = caught.exception.response_shape
                self.assertIn({"field": field, "type": "string", "observed_enum_value": observed}, shape)
                self.assertEqual(caught.exception.validation_issues,
                                 [{"path": "status", "type": "missing"}, {"path": "*", "type": "extra_forbidden"}])
                self.assertNotIn(case["customer_id"], str(caught.exception))
                self.assertNotIn(case["order_id"], str(caught.exception))
                self.assertNotIn("status", changed["body"])
            self.assertEqual(response, original)

    def test_order_shape_diagnostic_redacts_values_and_unsafe_keys_and_is_bounded(self):
            from support_agent.adapters.response_values import response_shape
            body = {"email": "private-email@example.test", "private_account_key": "delivered",
                    "customer_secret": "private-value", "customer_id": "private-customer",
                    "notes": "not a public enum", "payment": {"secret": "private-value"},
                    "active": False, "count": 3, "items": [], "unknown": None,
                    "unrecognized-field": "delivered"}
            shape = response_shape(body, Order)
            text = json.dumps(shape)
            for value in ("private-email", "private-value", "private-customer", "private_account_key",
                          "customer_secret", "unrecognized-field", "delivered", "not a public enum"):
                self.assertNotIn(value, text)
            self.assertIn({"field": "active", "type": "boolean"}, shape)
            self.assertIn({"field": "count", "type": "number"}, shape)
            self.assertIn({"field": "items", "type": "array"}, shape)
            self.assertIn({"field": "unknown", "type": "null"}, shape)
            self.assertEqual(len(response_shape({"field_" + str(i): "private-value" for i in range(100)}, Order)), 32)


class MigratedAliasContracts(unittest.TestCase):

    def setUp(self):
            kit, runtime, _ = reference_kit()
            self.case = SEED["cases"][1]
            self.response = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + self.case["order_id"][1:]})

    def test_alias_preserves_every_documented_enum_and_original_record(self):
            for state in get_args(Order.model_fields["status"].annotation):
                with self.subTest(state=state):
                    response = deepcopy(self.response)
                    response["body"].pop("status")
                    response["body"]["orderStatus"] = state
                    before = deepcopy(response)
                    result = read_order(ClientAPI(lambda request: response), self.case["order_id"])
                    self.assertEqual(result["status"], state)
                    self.assertNotIn("orderStatus", result)
                    self.assertEqual(result["response_warnings"], [{"code": "observed_order_status_alias",
                        "path": "status", "source_path": "orderStatus"}])
                    self.assertEqual(response, before)

    def test_missing_invalid_or_duplicate_status_is_not_guessed(self):
            for value in (None, True, 17, {}, [], "shipped", "DELIVERED"):
                response = deepcopy(self.response)
                response["body"]["orderStatus"] = value
                response["body"].pop("status")
                with self.subTest(value=value), self.assertRaisesRegex(ReadError, "invalid_read_response"):
                    read_order(ClientAPI(lambda request: response), self.case["order_id"])
            for value in ("delivered", "pending"):
                response = deepcopy(self.response)
                response["body"]["orderStatus"] = value
                with self.subTest(duplicate=value), self.assertRaisesRegex(ReadError, "invalid_read_response"):
                    read_order(ClientAPI(lambda request: response), self.case["order_id"])
            response = deepcopy(self.response)
            response["body"].pop("status")
            with self.assertRaisesRegex(ReadError, "invalid_read_response"):
                read_order(ClientAPI(lambda request: response), self.case["order_id"])

    def test_alias_is_not_a_global_key_rewrite_or_a_value_change(self):
            record = {"orderStatus": "delivered", "shipping_address": {"orderStatus": "nested-value"}}
            for operation in (None, "getCustomer", "getProduct", "createOrderExchange"):
                result, warnings = normalize_response(record, operation=operation)
                self.assertEqual(result, record)
                self.assertEqual(warnings, [])
            result, _ = normalize_response(record, operation="getOrder")
            self.assertEqual(result["status"], "delivered")
            self.assertEqual(result["shipping_address"], record["shipping_address"])
            self.assertNotIn("status", record)


class MigratedRateContracts(unittest.TestCase):

    def test_real_sdk_rate_limited_search_waits_then_verifies_before_details(self):
            _, original, _ = reference_kit()
            clock = Clock()
            profile = DefectProfile(manifest_id="synthetic-read-rate", manifest_sha256="1" * 64,
                manifest_version=1, domain="retail_plus", defects=(RateLimitDefect(
                    id="synthetic-search-limit", operation_id="searchCustomers", trigger_call_ordinal=1,
                    min_delay_seconds=2.5, max_delay_seconds=2.5, retry_after_seconds=2),))
            runtime = ClientAPIRuntime(original.environment, defect_profile=profile,
                                      monotonic_clock=clock)
            requests, responses = [], []

            def transport(request):
                requests.append(deepcopy(request))
                response = runtime.dispatch(request)
                responses.append(response["status_code"])
                return response

            kit = Tools(ClientAPI(transport, context=runtime.context))
            case = SEED["cases"][0]
            with patch("support_agent.adapters.read_api.time.sleep", clock.sleep):
                verified = kit.verify_customer(email=case["email"], claimed_customer_id=case["customer_id"])
            self.assertEqual(verified["customer_id"], case["customer_id"])
            self.assertEqual(responses, [429, 429, 200, 200])
            self.assertEqual(clock.delays, [2, 2])
            self.assertTrue(all(row["path"] == "/v1/customers/search" for row in requests[:-1]))
            self.assertEqual(requests[-1]["path"], "/v1/customers/" + case["customer_id"])

    def test_auth_server_errors_and_missing_retry_after_are_not_retried(self):
            for status, headers in ((401, {}), (403, {}), (503, {"Retry-After": "1"}),
                                    (429, {}), (429, {"Retry-After": "0"}),
                                    (429, {"Retry-After": "9999"})):
                with self.subTest(status=status, headers=headers):
                    requests = []

                    def transport(request):
                        requests.append(request)
                        return {"status_code": status, "body": {}, "headers": headers}

                    with patch("support_agent.adapters.read_api.time.sleep") as sleep:
                        with self.assertRaises(ReadError):
                            read_products(ClientAPI(transport))
                    self.assertEqual(len(requests), 1)
                    sleep.assert_not_called()

    def test_persistent_read_limit_is_bounded_and_never_returns_a_fake_empty_catalog(self):
            requests = []

            def transport(request):
                requests.append(request)
                return {"status_code": 429, "body": {}, "headers": {"Retry-After": "10"}}

            clock = Clock()
            with patch("support_agent.adapters.read_api.time.sleep", clock.sleep):
                with self.assertRaises(ReadError) as caught:
                    read_products(ClientAPI(transport))
            self.assertEqual(caught.exception.http_status, 429)
            self.assertEqual(clock.delays, [10, 10])
            self.assertEqual(len(requests), 3)
