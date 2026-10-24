"""Natural recap -> actual customer approval -> one write, without a draft round.

Controlled provider decisions validate protocol/effects, not LLM comprehension.
The real local SDK and native database are used for every operation.
"""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from test_reference_runtime import reference_kit, apply_native_reference
from support_agent.domain.changes import prepare
from support_agent.domain.observations import ObservedFacts
from support_agent.domain.identity import ReadError
from tau2.data_model.message import UserMessage
from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.hyper.client_api import ClientAPI
from tau2.environment.environment import Environment
from support_agent.state import decode


class DirectSubmissionTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def observed_change(self, kind="exchange"):
        self.build(kind)
        incoming = UserMessage(role="user", content="Please help with my requested change. My email is " + self.case["email"])
        incoming = self.result(self.decision(incoming, "verify_customer",
            {"email": self.case["email"], "claimed_customer_id": self.case["customer_id"]}))
        if kind != "default_address":
            incoming = self.result(self.decision(incoming, "get_owned_order", {"order_id": self.case["order_id"]}))
        if kind in {"modify_items", "exchange"}:
            order = self.kit._observed_orders[self.case["order_id"]]
            product_ids = {item["product_id"] for item in order["items"]
                           if item["item_id"] in {row["existing_item_id"] for row in self.params["replacements"]}}
            for identifier in sorted(product_ids):
                incoming = self.result(self.decision(incoming, "get_product_variants", {"product_id": identifier}))
        facts = prepare(ObservedFacts(self.kit), kind, self.params)
        # These are actual read facts, not an internally prepared proposal ID.
        recap = (f"For {facts['order_id'] or 'your profile'}, the full {kind} change is "
                 + json.dumps(facts["body"]) + ". The relevant financial calculations are "
                 + json.dumps(facts["calculations"]) + ". Is this the complete change, and do you approve submission?")
        reply = self.decision(incoming, text=recap)
        self.assertEqual(reply.content, recap)
        self.assertEqual(self.state["proposals"], {})
        self.assertEqual(self.writes(), [])
        return facts

    def test_seven_natural_recap_approvals_write_without_preparation(self):
        for kind in ("default_address", "order_address", "cancel", "payment", "modify_items", "return", "exchange"):
            with self.subTest(kind=kind):
                self.observed_change(kind)
                before = self.runtime.environment.get_db_hash()
                approved = UserMessage(role="user", content="Yes, those exact details are right. This is the entire request. Please submit it now.")
                call = self.decision(approved, "submit_change", {"kind": kind, "parameters": self.params})
                self.assertEqual(call.tool_calls[0].name, "submit_change")
                self.assertTrue(call.tool_calls[0].arguments["request_id"])
                receipt = self.result(call)
                data = json.loads(receipt.content)
                self.assertEqual(data["state"], "accepted" if kind in {"return", "exchange"} else "completed")
                final = self.decision(receipt, text="The requested operation has now actually been submitted.")
                self.assertIn("Recorded operation facts", final.content)
                self.assertIsNone(self.state["halt_reason"])
                self.assertEqual(len(self.writes()), 1)
                self.assertNotEqual(before, self.runtime.environment.get_db_hash())
                self.assertFalse(any(c["name"] in {"prepare_change", "review_change"}
                    for e in self.state["history"] for c in e.get("tool_calls", [])))
                _, native, _ = reference_kit()
                apply_native_reference(native.environment.tools, kind, self.case["customer_id"], self.params)
                self.assertEqual(self.runtime.environment.get_db_hash(), native.environment.get_db_hash())
                before_replay = len(self.requests)
                replayed = self.result(call)
                self.assertEqual(replayed.content, receipt.content)
                self.assertEqual(len(self.requests), before_replay)
                restored = self.agent.get_init_state(deepcopy(self.state["history"]))
                self.assertEqual(restored["submissions"], self.state["submissions"])
                fresh, replay_runtime, _ = reference_kit()
                Environment("retail_plus", policy="", tools=fresh).set_state(
                    None, None, [decode(event) for event in self.state["history"]])
                self.assertEqual(replay_runtime.environment.get_db_hash(), self.runtime.environment.get_db_hash())

    def test_changed_price_after_customer_recap_never_writes(self):
        self.observed_change()
        order = self.kit._observed_orders[self.case["order_id"]]
        old_id = self.params["replacements"][0]["existing_item_id"]
        product_id = next(item["product_id"] for item in order["items"] if item["item_id"] == old_id)
        target_id = self.params["replacements"][0]["replacement_item_id"]
        self.runtime.environment.tools.db.products[product_id].variants[target_id].price += 10
        reply = self.decision(UserMessage(role="user", content="I approve the exact amount you just stated. Submit it."),
            "submit_change", {"kind": "exchange", "parameters": self.params})
        data = json.loads(self.result(reply).content)
        self.assertEqual(data["state"], "failed")
        self.assertEqual(data["error"], "facts_changed_prepare_and_confirm_again")
        self.assertEqual(self.writes(), [])

    def test_successful_prepared_execution_is_not_submitted_again(self):
        self.build()
        prepared = completion.TaskCompletionTests.prepare(self, "exchange")
        self.decision(prepared, text="Here is the full change for your approval.")
        approval = UserMessage(role="user", content="I approve the exact complete change. Submit it.")
        receipt = self.result(self.decision(approval, "execute_exchange", {"proposal_id": "proposal-1"}))
        before = len(self.requests)
        # The model mistakenly treats the alternative direct tool as another
        # required step. Reject that duplicate before the host can execute it,
        # then let the model explain the genuine already-successful receipt.
        self.model.side_effect = [
            AssistantMessage(role="assistant", tool_calls=[ToolCall(id="duplicate-path", name="submit_change",
                arguments={"kind": "exchange", "parameters": self.params})]),
            AssistantMessage(role="assistant", content="Your exchange has been registered, as the receipt confirms."),
        ]
        reply, self.state = self.agent.generate_next_message(receipt, self.state)
        self.assertFalse(reply.tool_calls)
        self.assertIn("Recorded operation facts", reply.content)
        self.assertEqual(len(self.requests), before)
        self.assertEqual(len(self.writes()), 1)
        self.assertIsNone(self.state["halt_reason"])
        restored = self.agent.get_init_state(deepcopy(self.state["history"]))
        self.assertEqual(restored["submissions"], self.state["submissions"])

    def test_prior_order_and_product_observations_are_required(self):
        self.build()
        self.kit.verify_customer(email=self.case["email"])
        with self.assertRaisesRegex(ReadError, "read_order_facts"):
            self.kit.submit_change("exchange", self.params, "one")
        self.kit.get_owned_order(self.case["order_id"])
        with self.assertRaisesRegex(ReadError, "read_replacement_variants"):
            self.kit.submit_change("exchange", self.params, "one")
        self.assertEqual(self.writes(), [])

    def test_no_language_classifier_turns_questions_into_writes(self):
        self.observed_change()
        for text in ("What happens to the refund?", "Yes to the explanation, but do not submit yet.", "Pause while I check."):
            # As in tau2, the model interprets the actual meaning. This test
            # supplies a non-write decision, not a programmatic yes/no detector.
            self.decision(UserMessage(role="user", content=text), text="The request remains unsubmitted.")
        self.assertEqual(self.writes(), [])

    def test_new_later_address_request_can_restore_a_previous_address(self):
        self.observed_change("default_address")
        first = deepcopy(self.params)
        second = {"address": {**first["address"], "city": "New York"}}
        for reference, params in (("one", first), ("two", second), ("three", first)):
            self.assertEqual(self.kit.submit_change("default_address", params, reference)["state"], "completed")
        self.assertEqual(len(self.writes()), 3)
        self.assertEqual(self.runtime.environment.tools.db.users[self.case["customer_id"]].address.city, first["address"]["city"])

    def test_unknown_write_is_not_repeated_under_same_or_new_reference(self):
        self.observed_change()
        original_transport = self.kit.client_api._transport
        def transport(request):
            if request["path"].endswith("/exchanges"):
                self.requests.append(deepcopy(request))
                raise TimeoutError("Synthetic uncertain delivery")
            return original_transport(request)
        self.kit.client_api = ClientAPI(transport, context=self.runtime.context)
        outcome = self.kit.submit_change("exchange", self.params, "one")
        self.assertEqual(outcome["state"], "unknown")
        self.assertEqual(self.kit.submit_change("exchange", self.params, "one"), outcome)
        with self.assertRaisesRegex(ReadError, "unresolved_write"):
            self.kit.submit_change("exchange", self.params, "two")
        self.assertEqual(len(self.writes()), 1)
