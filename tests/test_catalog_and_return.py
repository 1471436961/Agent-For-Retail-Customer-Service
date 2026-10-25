"""Mixed goals stay in the LLM context; real reads and one actual return write."""
import json
import unittest
from unittest.mock import patch

from agent import create_agent
from tools import Tools
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.environment.environment import Environment
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext
from fixtures.p2_backend import SyntheticBackend
from test_reference_runtime import ROOT, SEED, apply_native_reference, reference_kit
from test_async_operations import async_kit
from test_task_completion import live_call


class CatalogAndReturnTests(unittest.TestCase):
    def test_variant_and_available_counts_follow_live_booleans_not_an_expected_answer(self):
        backend = SyntheticBackend()
        kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="catalog-counts")))
        kit.verify_customer(email="alex.p0@example.test")
        product = backend.records["products"][0]
        for total, available in ((12, 10), (7, 5), (3, 0)):
            with self.subTest(total=total, available=available):
                product["items"] = [{"item_id": f"variant-{index}", "options": {"size": str(index)},
                    "available": index < available, "price": 10.0} for index in range(total)]
                variants = kit.get_product_variants(product["product_id"])
                summary = kit.get_catalog_summary(product["product_id"])
                self.assertEqual((variants["variant_count"], variants["available_variant_count"]), (total, available))
                self.assertEqual((summary["variant_count"], summary["available_variant_count"]), (total, available))
                # A fresh stock change invalidates the earlier count naturally.
                product["items"][0]["available"] = not product["items"][0]["available"]
                self.assertEqual(kit.get_product_variants(product["product_id"])["available_variant_count"],
                                 available - 1 if available else 1)

    def test_mixed_request_keeps_catalog_goal_and_finishes_async_return_without_more_user_input(self):
        # Controlled model decisions verify protocol and effects. This is not
        # represented as a real-model semantic-understanding test.
        kit, runtime, requests, _, _, clock = async_kit("return", delay=30)
        case = SEED["cases"][1]
        context = build_agent_context(domain="retail_plus", tools=list(kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=[{"model": "offline-mixed-goals"}])
        with activate_agent_context(context):
            agent = create_agent()
        host = Environment("retail_plus", policy="", tools=kit)
        state = agent.get_init_state()
        sequence = 0
        original_request = "How many options are currently available, and please return my delivered items. My email is " + case["email"]
        received = UserMessage(role="user", content=original_request)
        before = runtime.environment.get_db_hash()

        with patch("tau2.hyper.agent_context.generate") as model, \
                patch("support_agent.adapters.workflow_api.time.sleep", clock.sleep):
            def decision(name=None, args=None, text=None):
                nonlocal state, sequence
                sequence += 1
                if name:
                    name, args = live_call(agent, name, args)
                model.return_value = (AssistantMessage(role="assistant", content=text) if name is None else
                    AssistantMessage(role="assistant", tool_calls=[ToolCall(id=f"mixed-{sequence}", name=name, arguments=args)]))
                reply, state = agent.generate_next_message(received, state)
                self.assertIsNone(state["halt_reason"], state)
                messages = model.call_args.kwargs["messages"]
                self.assertTrue(any(getattr(row, "content", None) == original_request for row in messages))
                # Capability information reaches the model both before and after verification.
                self.assertIn("catalog-selection:", messages[0].content)
                if name:
                    result = host.get_response(reply.tool_calls[0])
                    self.assertFalse(result.error, result.content)
                    return result
                return reply

            received = decision("verify_customer", {"email": case["email"], "claimed_customer_id": None})
            received = decision("list_customer_orders", {"include_details": True})
            listing = json.loads(received.content)
            self.assertIn("return", listing["change_policies"])
            order = next(row for row in listing["orders"] if row["order_id"] == case["order_id"])
            product_id = order["items"][0]["product_id"]
            received = decision("get_catalog_summary", {"product_id": product_id})
            count = json.loads(received.content)["available_variant_count"]
            params = {"order_id": order["order_id"], "item_ids": [row["item_id"] for row in order["items"]],
                      "refund_payment_method_id": case["payment_method_ids"]["credit_card"]}
            received = decision("prepare_change", {"kind": "return", "parameters": params})
            self.assertEqual(runtime.environment.get_db_hash(), before)
            quote = json.loads(received.content)
            recap = decision(text=f"There are {count} currently available choices. Return the complete listed items for {quote['facts']['calculations'][0]['result']} to the original card. Do you approve submission?")
            self.assertIn(str(count), recap.content)
            received = UserMessage(role="user", content="That is the complete list. Submit this exact return to my original card.")
            received = decision("submit_change", {"kind": "return", "parameters": params})
            self.assertEqual(json.loads(received.content)["state"], "pending")
            self.assertEqual(state["proposals"][quote["proposal_id"]]["state"], "prepared")  # result not consumed yet
            received = decision("inspect_change_result", {"proposal_id": quote["proposal_id"]})
            self.assertEqual(json.loads(received.content)["state"], "accepted")
            final = decision(text=f"There are {count} available choices. Your return application is registered; a refund arrival is not confirmed.")
            self.assertEqual(state["proposals"][quote["proposal_id"]]["state"], "accepted")
            self.assertTrue(all(row["state"] == "accepted" for row in state["submissions"].values()))
            self.assertNotIn('"state": "pending"', final.content)
        self.assertNotEqual(runtime.environment.get_db_hash(), before)
        self.assertEqual(len([row for row in requests if row["path"].endswith("/returns")]), 1)
        self.assertFalse(any(row["path"].endswith("/transfers") for row in requests))
        _, native, _ = reference_kit()
        apply_native_reference(native.environment.tools, "return", case["customer_id"], params)
        self.assertEqual(runtime.environment.get_db_hash(), native.environment.get_db_hash())
