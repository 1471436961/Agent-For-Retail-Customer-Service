"""One model-owned quote/confirmation, then actual business execution.

Provider choices are controlled. These check orchestration, preserved customer
text, receipts and SDK effects, not real-model comprehension of consent.
"""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from tau2.data_model.message import UserMessage


class SingleConfirmationTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def quote(self):
        self.build()
        message = completion.TaskCompletionTests.prepare(self, "exchange")
        p = json.loads(message.content)
        facts = p["facts"]
        # A controlled provider's ordinary recap grounded in the real quote.
        text = ("For order " + facts["order_id"] + ", the complete exchange is "
                + json.dumps(facts["body"]["replacements"]) + ". The exact new-minus-original difference is "
                + str(facts["calculations"][0]["result"]) + ", using " + facts["body"]["payment_method_id"]
                + ". This is a request, not completed refund or shipment. Do you approve this complete exchange for submission?")
        reply = self.decision(message, text=text)
        self.assertEqual(reply.content, text)
        self.assertEqual(reply.content.count("?"), 1)
        self.assertNotIn("Prepared change proposal-", reply.content)
        self.assertEqual(self.writes(), [])
        return p, text

    def test_single_quote_approval_executes_without_another_display(self):
        p, text = self.quote()
        restored = self.agent.get_init_state(deepcopy(self.state["history"]))
        self.assertEqual(restored["quote_response_turns"], self.state["quote_response_turns"])
        self.state = restored
        outcome = self.result(self.decision(UserMessage(role="user", content="That is the complete change I approve. Submit it now."),
            "execute_exchange", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(json.loads(outcome.content)["state"], "accepted")
        final = self.decision(outcome, text="Your exchange request was accepted. Return instructions are provided by email.")
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn("Please confirm", final.content)
        self.assertNotIn("Prepared change", final.content)
        self.assertEqual(self.runtime.environment.tools.db.orders[self.case["order_id"]].status, "exchange requested")

    def test_clarification_does_not_repeat_quote_or_prepare_again(self):
        p, _ = self.quote()
        before = len(self.requests)
        explanation = "The request registers the exchange. Instructions for sending originals back are provided by email; refund arrival timing is not specified."
        reply = self.decision(UserMessage(role="user", content="Before approving: do I need to order the replacement separately?"), text=explanation)
        self.assertEqual(reply.content, explanation)
        self.assertNotIn("?", reply.content)
        self.assertEqual(len(self.requests), before)
        self.assertEqual(self.writes(), [])
        # No new review is necessary; only the model's interpretation of the
        # next customer's actual approval changes the chosen action.
        receipt = self.result(self.decision(UserMessage(role="user", content="Understood. I approve the exact unchanged complete exchange and settlement. Submit it."),
            "execute_exchange", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(json.loads(receipt.content)["state"], "accepted")
        self.assertEqual(len(self.writes()), 1)

    def test_explicit_submission_approval_with_process_question_is_not_reapproved(self):
        p, _ = self.quote()
        receipt = self.result(self.decision(UserMessage(role="user", content="Yes, submit this complete exchange. What return instructions will I receive?"),
            "execute_exchange", {"proposal_id": p["proposal_id"]}))
        final = self.decision(receipt, text="The exchange was accepted. Policy provides instructions by email; no separate purchase is required.")
        self.assertEqual(len(self.writes()), 1)
        self.assertNotIn("?", final.content)

    def test_preparation_request_is_not_submission_consent(self):
        p, _ = self.quote()
        self.decision(UserMessage(role="user", content="I am only asking you to prepare a quote. Do not submit anything."),
            text="This remains an unsubmitted quote; no record or funds changed.")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state["proposals"][p["proposal_id"]]["state"], "prepared")

    def test_reading_unchanged_quote_after_approval_does_not_restart_confirmation(self):
        for action in ("review_change", "prepare_change"):
            with self.subTest(action=action):
                p, _ = self.quote()
                args = ({"proposal_id": p["proposal_id"]} if action == "review_change" else
                        {"kind": "exchange", "parameters": self.params})
                quoted_at = dict(self.state["quote_response_turns"])
                read = self.result(self.decision(UserMessage(role="user", content="I approve the exact complete exchange. Submit it."), action, args))
                receipt = self.result(self.decision(read, "execute_exchange", {"proposal_id": p["proposal_id"]}))
                self.assertEqual(self.state["user_turn"], quoted_at[p["proposal_id"]] + 1)
                self.assertEqual(json.loads(receipt.content)["state"], "accepted")
                self.assertEqual(len(self.writes()), 1)
                self.assertEqual(len(self.kit._proposals), 1)

    def test_changed_quote_gets_new_version_without_using_old_approval(self):
        p, _ = self.quote()
        old = self.kit._observed_orders[self.case["order_id"]]["items"]
        selected = self.params["replacements"][0]
        product_id = next(i["product_id"] for i in old if i["item_id"] == selected["existing_item_id"])
        self.runtime.environment.tools.db.products[product_id].variants[selected["replacement_item_id"]].price += 5
        read = self.result(self.decision(UserMessage(role="user", content="Please check if the price changed. I have not approved a different amount."),
            "read_products", {"product_ids": [product_id]}))
        refreshed = self.result(self.decision(read, "prepare_change", {"kind": "exchange", "parameters": self.params}))
        updated = json.loads(refreshed.content)
        self.assertNotEqual(updated["proposal_id"], p["proposal_id"])
        self.decision(refreshed, text="The actual quote changed. Here is the updated amount for your approval.")
        self.assertNotIn(p["proposal_id"], self.state["quote_response_turns"])
        self.assertEqual(self.state["proposals"][p["proposal_id"]]["state"], "superseded")
        self.assertEqual(self.writes(), [])
