"""Completion means a real SDK database change, not a canned chatbot response.

Provider decisions are controlled here. These tests check orchestration and
actual effects; semantic competence must be verified separately with a real model.
"""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import test_reference_runtime as reference
from test_reference_runtime import ROOT, SEED, reference_kit, apply_native_reference
from agent import create_agent
from support_agent.domain.identity import ReadError
from support_agent.domain.presentation import legacy_review_text, review_text
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.environment.environment import Environment
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from support_agent.adapters.change_tools import DIRECT_WRITE_KINDS


def live_call(agent, name, args):
    """Construct a controlled current tool choice; legacy tests retain the old ABI."""
    if name == "submit_change" and name not in {action.name for action in agent.gateway.live_actions}:
        return next(key for key,value in DIRECT_WRITE_KINDS.items() if value == args["kind"]), deepcopy(args["parameters"])
    return name, deepcopy(args)


class TaskCompletionTests(unittest.TestCase):
    def build(self, kind="exchange", *, compatibility=True):
        self.kit, self.runtime, self.requests = reference_kit()
        self.case = SEED["cases"][1 if kind in {"return", "exchange"} else 0]
        reader, _, _ = reference_kit()
        reader.verify_customer(email=self.case["email"])
        self.params = reference.ReferenceRuntimeTests.change_parameters(reader, kind, self.case)
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=[{"model": "offline-completion-test"}])
        with activate_agent_context(context):
            self.agent = create_agent()
        if compatibility:
            self.agent.gateway.live_actions = self.agent.gateway.compatibility_actions
        self.host = Environment("retail_plus", policy="", tools=self.kit)
        self.state = self.agent.get_init_state()
        self.model = self.enterContext(patch("tau2.hyper.agent_context.generate"))
        self.sequence = 0

    def decision(self, incoming, name=None, args=None, text=None):
        self.sequence += 1
        if name is not None:
            name,args = live_call(self.agent,name,args)
        self.model.return_value = (AssistantMessage(role="assistant", content=text) if name is None else
            AssistantMessage(role="assistant", tool_calls=[ToolCall(id=f"completion-{self.sequence}", name=name, arguments=args)]))
        reply, self.state = self.agent.generate_next_message(incoming, self.state)
        return reply

    def result(self, reply):
        self.assertTrue(reply.tool_calls, self.state)
        message = self.host.get_response(reply.tool_calls[0])
        self.assertFalse(message.error, message.content)
        return message

    def prepare(self, kind):
        initial = UserMessage(role="user", content="Please help with my requested change. My email is " + self.case["email"])
        verified = self.result(self.decision(initial, "verify_customer",
            {"email": self.case["email"], "claimed_customer_id": self.case["customer_id"]}))
        return self.result(self.decision(verified, "prepare_change", {"kind": kind, "parameters": self.params}))

    def writes(self):
        return [request for request in self.requests if request["method"] != "GET" and request["path"] != "/v1/customers/search"]

    def test_all_seven_changes_complete_without_an_extra_review_tool(self):
        executors = {"default_address": "execute_default_address", "order_address": "execute_order_address",
                     "cancel": "execute_cancellation", "payment": "execute_payment",
                     "modify_items": "execute_item_modification", "return": "execute_return", "exchange": "execute_exchange"}
        for kind, executor in executors.items():
            with self.subTest(kind=kind):
                self.build(kind)
                before = self.runtime.environment.get_db_hash()
                prepared = self.prepare(kind)
                self.assertEqual(json.loads(prepared.content)["state"], "prepared")
                self.assertEqual(self.runtime.environment.get_db_hash(), before)
                explanation = review_text(json.loads(prepared.content))
                recap = self.decision(prepared, text=explanation)
                self.assertEqual(recap.content, explanation)
                self.assertIn("Please confirm", recap.content)
                self.assertEqual(self.state["quote_response_turns"], {"proposal-1": self.state["user_turn"]})
                self.assertEqual(self.writes(), [])
                self.state = self.agent.get_init_state(deepcopy(self.state["history"]))
                approval = UserMessage(role="user", content="This is the entire requested change. I approve the exact displayed proposal; submit it.")
                result = self.result(self.decision(approval, executor, {"proposal_id": "proposal-1"}))
                receipt = json.loads(result.content)
                self.assertEqual(receipt["state"], "accepted" if kind in {"return", "exchange"} else "completed")
                self.decision(result, text="The operation has been registered as shown by the actual receipt.")
                self.assertIsNone(self.state["halt_reason"])
                self.assertEqual(self.state["proposals"]["proposal-1"]["state"], receipt["state"])
                self.assertEqual(len(self.writes()), 1)
                self.assertNotEqual(self.runtime.environment.get_db_hash(), before)
                _, native, _ = reference_kit()
                apply_native_reference(native.environment.tools, kind, self.case["customer_id"], self.params)
                self.assertEqual(self.runtime.environment.get_db_hash(), native.environment.get_db_hash())
                self.assertFalse(any(call["name"] == "review_change" for event in self.state["history"] for call in event.get("tool_calls", [])))

    def test_customer_question_keeps_draft_and_explanation_is_not_replaced(self):
        self.build()
        prepared = self.prepare("exchange")
        answer = "The exchange itself creates the replacement path; policy provides return instructions by email. No separate purchase is needed."
        reply = self.decision(prepared, text=answer)
        self.assertTrue(reply.content.startswith(answer))
        self.assertNotIn('"snapshot"', reply.content)
        self.assertNotIn(self.case["email"], reply.content)
        self.decision(UserMessage(role="user", content="I have a question, not permission to submit: has the replacement shipped?"),
                      text="No. The proposal is still unsubmitted, and no shipment is confirmed.")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state["proposals"]["proposal-1"]["state"], "prepared")
        self.assertIn("proposal-1", self.state["quote_response_turns"])

    def test_prepare_cannot_execute_before_actual_display_and_later_approval(self):
        self.build()
        prepared = self.prepare("exchange")
        self.model.side_effect = [
            AssistantMessage(role="assistant", tool_calls=[ToolCall(id="too-early", name="execute_exchange", arguments={"proposal_id": "proposal-1"})]),
            AssistantMessage(role="assistant", content=review_text(json.loads(prepared.content))),
        ]
        reply, self.state = self.agent.generate_next_message(prepared, self.state)
        self.assertFalse(reply.tool_calls)
        self.assertIn("Please confirm", reply.content)
        self.assertEqual(self.writes(), [])
        self.assertIn("proposal-1", self.state["quote_response_turns"])
        # No customer input has occurred since that display. Even a subsequent
        # tool result must not grant permission by advancing only a tool round.
        self.model.side_effect = None
        read = self.decision(UserMessage(role="user", content="Please just read the order; I am not approving yet."), "get_owned_order", {"order_id": self.case["order_id"]})
        incoming = self.result(read)
        self.decision(incoming, text="The order is still delivered; no change has been submitted.")
        self.assertEqual(self.writes(), [])

    def test_withdrawal_before_display_discards_pending_recap(self):
        self.build()
        prepared = self.prepare("exchange")
        withdraw = self.decision(prepared, "invalidate_change", {"proposal_id": "proposal-1"})
        outcome = self.result(withdraw)
        reply = self.decision(outcome, text="The draft has been withdrawn; nothing was submitted.")
        self.assertIsNone(self.state["quote_response_pending"])
        self.assertEqual(self.state["proposals"]["proposal-1"]["state"], "withdrawn")
        self.assertNotIn("Please confirm", reply.content)
        self.assertEqual(self.writes(), [])

    def test_old_fixed_json_recap_history_can_still_be_restored(self):
        self.build()
        prepared = self.prepare("exchange")
        self.decision(prepared, text="Please review this proposal.")
        proposal = self.state["proposals"]["proposal-1"]
        history = deepcopy(self.state["history"])
        history[-1]["content"] = legacy_review_text(proposal)
        restored = self.agent.get_init_state(history)
        self.assertEqual(restored["quote_response_turns"], self.state["quote_response_turns"])

    def test_catalog_counts_and_order_discovery_use_actual_records(self):
        self.build()
        self.kit.verify_customer(email=self.case["email"])
        listing = self.kit.list_customer_orders(include_details=True)
        self.assertEqual([order["order_id"] for order in listing["orders"]], listing["order_ids"])
        self.assertTrue(all(order["customer_id"] == self.case["customer_id"] for order in listing["orders"]))
        product_id = listing["orders"][0]["items"][0]["product_id"]
        product = self.kit.get_product_variants(product_id)
        summary = self.kit.get_catalog_summary(product_id=product_id)
        self.assertEqual(summary["variant_count"], len(product["items"]))
        self.assertEqual(summary["available_variant_count"], sum(item["available"] for item in product["items"]))
        self.assertEqual(summary["product_type_count"], 1)
        self.assertEqual(self.writes(), [])

    def test_order_discovery_rejects_foreign_order_even_if_listed_in_profile(self):
        self.build()
        self.kit.verify_customer(email=self.case["email"])
        foreign = next(order.order_id for order in self.runtime.environment.tools.db.orders.values()
                       if order.user_id != self.case["customer_id"])
        self.runtime.environment.tools.db.users[self.case["customer_id"]].orders.append(foreign)
        with self.assertRaisesRegex(ReadError, "order_access_denied"):
            self.kit.list_customer_orders(include_details=True)
        self.assertEqual(self.writes(), [])

    def test_policy_and_totals_are_operation_specific_tool_facts(self):
        self.build()
        prepared = self.prepare("exchange")
        proposal = json.loads(prepared.content)
        self.assertEqual(proposal["facts"]["policy"]["skill"], "delivered-exchanges")
        totals = {row["purpose"]: row["result"] for row in proposal["facts"]["calculations"]
                  if row.get("purpose") in {"original_total", "replacement_total"}}
        self.assertEqual(set(totals), {"original_total", "replacement_total"})
        from support_agent.skills import library
        policy = self.kit.get_service_policy("exchange")
        instructions = library()[policy["skill"]]["instructions"]
        self.assertIn("no separate purchase", instructions)
        self.assertNotIn("3–6", instructions)
        self.assertIn("3–6", library()[self.kit.get_service_policy("cancel")["skill"]]["instructions"])
        self.assertEqual(self.writes(), [])

    def test_only_displayed_changes_with_a_later_customer_turn_have_executors(self):
        self.build()
        prepared = self.prepare("exchange")
        from support_agent.state import SessionState
        self.decision(prepared, text="Here is your proposed exchange.")
        actions = self.agent.gateway.actions_for(SessionState.model_validate(self.state))
        self.assertFalse(any(action.name.startswith("execute_") for action in actions))
        self.decision(UserMessage(role="user", content="Please explain the process; I am not authorizing it yet."), text="The change remains a draft.")
        actions = self.agent.gateway.actions_for(SessionState.model_validate(self.state))
        executors = {action.name for action in actions if action.name.startswith("execute_")}
        self.assertEqual(executors, {"execute_exchange"})
        # Availability is not consent: the real model must decide that this
        # question authorizes nothing. No execution is inferred by the program.
        self.assertEqual(self.writes(), [])

    def test_independent_changes_share_real_confirmation_and_execute_sequentially(self):
        self.build("default_address")
        address = self.params["address"]
        prepared_default = self.prepare("default_address")
        order_params = {"order_id": self.case["order_id"], "address": address}
        prepared_order = self.result(self.decision(prepared_default, "prepare_change",
            {"kind": "order_address", "parameters": order_params}))
        summary = ("Apply this new address to your profile and the named pending order: "
                   + json.dumps(address) + ". Do you approve both exact changes?")
        recap = self.decision(prepared_order, text=summary)
        self.assertEqual(recap.content, summary)
        self.assertEqual(set(self.state["quote_response_turns"]), {"proposal-1", "proposal-2"})
        self.assertEqual(self.writes(), [])
        first = self.result(self.decision(UserMessage(role="user", content="I approve both displayed address changes. Apply both."),
                                          "execute_default_address", {"proposal_id": "proposal-1"}))
        # The same explicit approval covers the already displayed independent
        # order-address change. No invented extra user confirmation is inserted.
        second = self.result(self.decision(first, "execute_order_address", {"proposal_id": "proposal-2"}))
        final = self.decision(second, text="Both requested address changes completed, with separate actual receipts.")
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(len(self.writes()), 2)
        self.assertEqual(final.content, "Both requested address changes completed, with separate actual receipts.")
        _, native, _ = reference_kit()
        apply_native_reference(native.environment.tools, "default_address", self.case["customer_id"], self.params)
        apply_native_reference(native.environment.tools, "order_address", self.case["customer_id"], order_params)
        self.assertEqual(self.runtime.environment.get_db_hash(), native.environment.get_db_hash())
