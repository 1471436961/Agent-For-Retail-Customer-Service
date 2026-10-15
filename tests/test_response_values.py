"""Representation compatibility without relaxing the business/API contract."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from tau2.environment.environment import Environment
from tau2.hyper.client_api import ClientAPI
from support_agent.adapters.read_api import read_order, read_catalog_item
from support_agent.adapters.read_models import Order
from support_agent.domain.identity import ReadError
from test_reference_runtime import reference_kit, serialized_reference_kit, SEED
from test_gateway_wire import completion
from agent import create_agent
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.data_model.message import UserMessage, MultiToolMessage


class ResponseValueTests(unittest.TestCase):
    def test_full_name_verification_then_three_sequential_order_reads(self):
        kit, runtime, requests = serialized_reference_kit()
        customer = next(user for user in runtime.environment.tools.db.users.values()
                        if len(user.orders) >= 3)
        order_ids = [identifier for identifier in customer.orders
                     if runtime.environment.tools.db.orders[identifier].user_id == customer.user_id][:3]
        self.assertEqual(len(order_ids), 3)
        context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
            resource_root=Path(__file__).resolve().parents[1] / "agent",
            model_configs=[{"model": "openai/contract-test-model", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(context):
            agent = create_agent()
        environment = Environment("retail_plus", policy="", tools=kit)
        state = agent.get_init_state()
        selectors = {"first_name": customer.name.first_name, "last_name": customer.name.last_name,
                     "postal_code": customer.address.zip, "claimed_customer_id": None}
        requests_to_model = [{"id": "order-read-" + str(i), "type": "function", "function": {
            "name": "get_owned_order", "arguments": json.dumps({"order_id": identifier})}}
            for i, identifier in enumerate(order_ids)]
        with patch("tau2.utils.llm_utils.completion") as provider, \
                patch("tau2.utils.llm_utils.get_response_cost", return_value=0):
            provider.return_value = completion(content="Please provide your full name and postal code.", raw_text=True)
            _, state = agent.generate_next_message(UserMessage(role="user", content="I'd like to exchange two purchases."), state)
            self.assertEqual(requests, [])
            provider.return_value = completion(arguments=selectors)
            verification, state = agent.generate_next_message(UserMessage(role="user", content=
                f"My name is {customer.name.first_name} {customer.name.last_name}; my postal code is {customer.address.zip}."), state)
            verified = MultiToolMessage(role="tool", tool_messages=[
                environment.get_response(verification.tool_calls[0])])
            self.assertFalse(verified.tool_messages[0].error)
            self.assertEqual(requests[0]["body"], {key: value for key, value in selectors.items()
                                                 if key != "claimed_customer_id"})
            incoming = verified
            results = []
            for proposed, identifier in zip(requests_to_model, order_ids):
                provider.return_value = completion(calls=[proposed])
                reply, state = agent.generate_next_message(incoming, state)
                self.assertEqual(len(reply.tool_calls), 1)
                self.assertEqual(reply.tool_calls[0].arguments["order_id"], identifier)
                result = environment.get_response(reply.tool_calls[0])
                self.assertFalse(result.error)
                self.assertEqual(json.loads(result.content)["order_id"], identifier)
                results.append(result)
                incoming = MultiToolMessage(role="tool", tool_messages=[result])
            provider.return_value = completion(content="I have loaded your orders. Which versions would you like?", raw_text=True)
            reply, state = agent.generate_next_message(incoming, state)
            self.assertIsNone(state["halt_reason"])
            self.assertEqual(state["pending_call_ids"], [])
            self.assertIn("Which versions", reply.content)
            last_wire_results = [message for message in provider.call_args.kwargs["messages"]
                                 if message["role"] == "tool"][-3:]
            self.assertEqual([result["tool_call_id"] for result in last_wire_results],
                             [result.id for result in results])
            self.assertTrue(all(json.loads(result["content"])["ok"] for result in last_wire_results))
        self.assertEqual([request["path"] for request in requests if request["path"].startswith("/v1/orders/")],
                         ["/v1/orders/%23" + identifier[1:] for identifier in order_ids])

    def test_real_order_scalar_serialization_reproduces_original_strict_failure(self):
        kit, runtime, requests = reference_kit()
        case = SEED["cases"][0]
        kit.verify_customer(email=case["email"])
        record = kit.get_owned_order(case["order_id"])
        wire = json.loads(Environment.to_json_str(record))
        with self.assertRaises(ValidationError) as caught:
            Order.model_validate(wire)
        paths = {row["loc"] for row in caught.exception.errors(include_input=False)}
        self.assertIn(("items", 0, "price"), paths)
        self.assertIn(("payments", 0, "amount"), paths)

        serialized, _, calls = serialized_reference_kit()
        serialized.verify_customer(email=case["email"])
        actual = serialized.get_owned_order(case["order_id"])
        self.assertEqual(actual["items"], record["items"])
        self.assertEqual(actual["payments"], record["payments"])
        self.assertEqual(actual["order_id"], case["order_id"])
        self.assertIn("items[0].price", {w["path"] for w in actual["response_warnings"]})
        self.assertTrue(calls[-1]["path"].endswith("%23" + case["order_id"][1:]))

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
        result = read_order(ClientAPI(lambda request: response), case["order_id"])
        diagnostic = json.dumps(result["response_warnings"])
        self.assertIn("extra_forbidden", diagnostic)
        self.assertNotIn("private-account", diagnostic)
        self.assertEqual(result["private-account-key"], "private-account-value")

    def test_finite_numeric_representation_has_no_invented_128_character_limit(self):
        from support_agent.adapters.response_values import normalize_response
        raw = "1." + "0" * 200
        actual, warnings = normalize_response({"price": raw})
        self.assertEqual(actual["price"], 1.0)
        self.assertEqual(warnings[0]["original_value"], raw)

    def test_missing_required_order_field_is_still_rejected_with_safe_location(self):
        kit, runtime, requests = reference_kit()
        case = SEED["cases"][0]
        response = runtime.dispatch({"method": "GET", "path": "/v1/orders/%23" + case["order_id"][1:]})
        del response["body"]["payments"][0]["amount"]
        with self.assertRaises(ReadError) as caught:
            read_order(ClientAPI(lambda request: response), case["order_id"])
        self.assertIn("payments.0.amount:missing", str(caught.exception))
        self.assertNotIn(case["customer_id"], str(caught.exception))

    def test_removing_order_prefix_is_denied_without_a_second_order_get(self):
        kit, runtime, requests = serialized_reference_kit()
        case = SEED["cases"][0]
        kit.verify_customer(email=case["email"])
        kit.get_owned_order(case["order_id"])
        before = len([r for r in requests if r["path"].startswith("/v1/orders/")])
        with self.assertRaisesRegex(ReadError, "order_access_denied"):
            kit.get_owned_order(case["order_id"][1:])
        self.assertEqual(len([r for r in requests if r["path"].startswith("/v1/orders/")]), before)

    def test_invalid_write_receipt_remains_unknown_and_is_never_repeated(self):
        kit, runtime, requests = serialized_reference_kit()
        case = SEED["cases"][0]
        kit.verify_customer(email=case["email"])
        proposal = kit.prepare_change("cancel", {"order_id": case["order_id"], "reason": "no longer needed"})
        original = kit.client_api._transport

        def broken_receipt(request):
            response = original(request)
            if request["path"].endswith("/cancellations"):
                response["body"]["payments"][0]["amount"] = "not-a-number"
            return response

        kit.client_api = ClientAPI(broken_receipt, context=runtime.context)
        outcome = kit.execute_cancellation(proposal["proposal_id"])
        self.assertEqual(outcome["state"], "unknown")
        self.assertEqual(outcome["error"], "invalid_write_receipt")
        self.assertEqual(outcome["validation_issues"][0]["path"], "payments[0].amount")
        self.assertEqual(kit.execute_cancellation(proposal["proposal_id"]), outcome)
        self.assertEqual(len([r for r in requests if r["path"].endswith("/cancellations")]), 1)
