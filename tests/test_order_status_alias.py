"""Observed getOrder field drift through actual SDK, Agent and exchange replay.

Fixture data is synthetic. Model decisions are mocked; no real provider is used.
"""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from typing import get_args
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from support_agent.adapters.read_api import read_order
from support_agent.adapters.read_models import Order
from support_agent.adapters.response_values import normalize_response
from support_agent.domain.identity import ReadError
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, UserMessage
from tau2.environment.environment import Environment
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI
from test_reference_runtime import reference_kit, apply_native_reference, SEED


def aliased_kit():
    kit, runtime, requests = reference_kit()
    original = kit.client_api._transport

    def transport(request):
        response = original(request)
        if request["method"] == "GET" and request["path"].startswith("/v1/orders/"):
            response["body"]["orderStatus"] = response["body"].pop("status")
        response["body"] = json.loads(Environment.to_json_str(response["body"]))
        return response

    kit.client_api = ClientAPI(transport, context=runtime.context)
    return kit, runtime, requests


class OrderStatusAliasTests(unittest.TestCase):
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

    def test_alias_still_checks_identity_money_and_other_required_fields(self):
        for field, value in (("order_id", "#OTHER"), ("customer_id", None), ("payments", [])):
            response = deepcopy(self.response)
            response["body"]["orderStatus"] = response["body"].pop("status")
            response["body"][field] = value
            if field == "payments":
                response["body"]["items"][0]["price"] = "not-money"
            with self.subTest(field=field), self.assertRaisesRegex(ReadError, "invalid_read_response"):
                read_order(ClientAPI(lambda request: response), self.case["order_id"])
        kit, runtime, _ = aliased_kit()
        kit.verify_customer(email=self.case["email"])
        foreign = deepcopy(self.response)
        foreign["body"]["orderStatus"] = foreign["body"].pop("status")
        foreign["body"]["customer_id"] = "another-customer"
        original = kit.client_api._transport
        kit.client_api = ClientAPI(lambda request: foreign if request["path"].startswith("/v1/orders/")
                                  else original(request), context=runtime.context)
        with self.assertRaisesRegex(ReadError, "order_access_denied"):
            kit.get_owned_order(self.case["order_id"])

    def test_two_product_exchange_runs_through_agent_confirmation_and_sdk_replay(self):
        kit, runtime, requests = aliased_kit()
        reader, _, _ = reference_kit()
        reader.verify_customer(email=self.case["email"])
        order = reader.get_owned_order(self.case["order_id"])
        rows = []
        for item in (order["items"][0], order["items"][-1]):
            target = next(row for row in reader.get_product_variants(item["product_id"])["items"]
                          if row["available"] and row["item_id"] != item["item_id"])
            rows.append({"existing_item_id": item["item_id"], "replacement_item_id": target["item_id"],
                         "preserve_options": ["color"]})
        params = {"order_id": self.case["order_id"], "replacements": rows,
                  "payment_method_id": self.case["payment_method_ids"]["paypal"], "preserve_options": []}
        context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=[{"model": "offline-contract-model"}])
        with activate_agent_context(context):
            agent = create_agent()
        host = Environment("retail_plus", policy="", tools=kit)
        writes = lambda: [request for request in requests if request["method"] != "GET"
                          and request["path"] != "/v1/customers/search"]
        incoming = UserMessage(role="user", content=(
            "Please prepare an exchange draft for the two selected items, using my saved PayPal. "
            "Show me the details before I authorize submission. My email is " + self.case["email"]))
        state, history = agent.get_init_state(), []
        with patch("tau2.hyper.agent_context.generate") as model:
            for index, (name, args) in enumerate([
                ("verify_customer", {"email": self.case["email"], "claimed_customer_id": self.case["customer_id"]}),
                ("get_owned_order", {"order_id": self.case["order_id"]}),
                ("prepare_change", {"kind": "exchange", "parameters": params}),
                ("review_change", {"proposal_id": "proposal-1"}),
            ]):
                model.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(
                    id=f"alias-{index}", name=name, arguments=args)])
                reply, state = agent.generate_next_message(incoming, state)
                result = host.get_response(reply.tool_calls[0])
                self.assertFalse(result.error, result.content)
                self.assertEqual(state["user_turn"], 1)
                self.assertEqual(writes(), [])
                history.extend([reply, result])
                incoming = MultiToolMessage(role="tool", tool_messages=[result])
            from support_agent.domain.presentation import review_text
            model.return_value = AssistantMessage(role="assistant", content=review_text(json.loads(result.content)["review"]))
            recap, state = agent.generate_next_message(incoming, state)
            self.assertIn("Please confirm", recap.content)
            self.assertIn("Is this the complete list", recap.content)
            self.assertIn(params["payment_method_id"], recap.content)
            self.assertEqual(state["user_turn"], 1)
            self.assertEqual(writes(), [])
            model.return_value = AssistantMessage(role="assistant", tool_calls=[ToolCall(
                id="alias-execute", name="execute_exchange", arguments={"proposal_id": "proposal-1"})])
            reply, state = agent.generate_next_message(UserMessage(role="user",
                content="This is the entire list. I approve this exact exchange."), state)
            self.assertEqual(state["user_turn"], 2)
            result = host.get_response(reply.tool_calls[0])
            self.assertFalse(result.error, result.content)
            self.assertEqual(json.loads(result.content)["state"], "accepted")
            history.extend([reply, result])
            model.return_value = AssistantMessage(role="assistant", content="The exchange request was accepted.")
            _, state = agent.generate_next_message(MultiToolMessage(role="tool", tool_messages=[result]), state)
            self.assertIsNone(state["halt_reason"])
        self.assertEqual(len(writes()), 1)
        self.assertEqual(writes()[0]["body"], {
            "payment_method_id": params["payment_method_id"],
            "replacements": [{key: row[key] for key in ("existing_item_id", "replacement_item_id")} for row in rows]})
        _, gold, _ = reference_kit()
        apply_native_reference(gold.environment.tools, "exchange", self.case["customer_id"], params)
        self.assertEqual(runtime.environment.get_db_hash(), gold.environment.get_db_hash())
        fresh, replay_runtime, _ = aliased_kit()
        Environment("retail_plus", policy="", tools=fresh).set_state(None, None, history)
        self.assertEqual(replay_runtime.environment.get_db_hash(), runtime.environment.get_db_hash())
