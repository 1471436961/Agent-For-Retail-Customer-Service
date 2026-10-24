"""A combined read saves model/tool turns without changing the business API."""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from support_agent.domain.changes import prepare
from support_agent.domain.observations import ObservedFacts
from tau2.data_model.message import UserMessage
from tau2.environment.environment import Environment
from test_reference_runtime import reference_kit
from support_agent.state import decode


class OrderCatalogReadTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def test_order_catalog_read_preserves_order_and_grounded_observations(self):
        self.build()
        self.kit.verify_customer(email=self.case["email"])
        basic = self.kit.get_owned_order(self.case["order_id"])
        start = len(self.requests)
        combined = self.kit.get_owned_order(self.case["order_id"], include_product_variants=True)
        self.assertEqual({key: value for key, value in combined.items() if key not in {"product_variants", "change_policies"}}, basic)
        self.assertEqual(set(combined["change_policies"]), {"return", "exchange"})
        product_ids = {row["product_id"] for row in basic["items"]}
        self.assertEqual({row["product_id"] for row in combined["product_variants"]}, product_ids)
        for product_id in product_ids:
            reads = [r for r in self.requests[start:] if r["path"] == "/v1/catalog/products/" + product_id]
            self.assertEqual(len(reads), 1)
        self.assertTrue(all(r["method"] == "GET" for r in self.requests[start:]))
        self.assertEqual(self.writes(), [])
        self.assertEqual(prepare(ObservedFacts(self.kit), "exchange", self.params)["order_id"], basic["order_id"])
        self.assertNotIn("product_variants", self.kit._observed_orders[self.case["order_id"]])

    def test_choice_then_quote_then_confirmation_reaches_real_write(self):
        self.build()
        initial = UserMessage(role="user", content="Please exchange two items in my order. My email is " + self.case["email"])
        verified = self.result(self.decision(initial, "verify_customer", {"email": self.case["email"], "claimed_customer_id": None}))
        combined = self.result(self.decision(verified, "get_owned_order", {
            "order_id": self.case["order_id"], "include_product_variants": True}))
        self.assertIn("product_variants", json.loads(combined.content))
        self.decision(combined, text="Here are the available choices from your order's product catalogs. Which replacements do you prefer?")
        selected = UserMessage(role="user", content="I choose the listed replacements. Please show the complete exchange and difference before submitting.")
        prepared = self.result(self.decision(selected, "prepare_change", {"kind": "exchange", "parameters": self.params}))
        self.decision(prepared, text="Here is the complete two-item quote and settlement for approval.")
        self.assertEqual(self.writes(), [])
        approved = UserMessage(role="user", content="That is the complete list. I approve these exact items and settlement. Submit it.")
        receipt = self.result(self.decision(approved, "execute_exchange", {"proposal_id": "proposal-1"}))
        self.assertEqual(json.loads(receipt.content)["state"], "accepted")
        self.assertEqual(len(self.writes()), 1)
        # Count actual chronological host messages, not an invented platform
        # limit. A greeting/identity clarification can add earlier messages;
        # this test proves fewer read-only tool round trips reach a real write.
        self.assertEqual(self.state["tool_rounds"], 4)

    def test_known_order_is_read_only_after_independent_verification(self):
        self.build()
        result = self.kit.verify_customer(email=self.case["email"], claimed_customer_id=self.case["customer_id"],
            order_id=self.case["order_id"], include_product_variants=True)
        self.assertEqual(result["customer_id"], self.case["customer_id"])
        self.assertEqual(result["requested_order"]["order_id"], self.case["order_id"])
        self.assertIn("product_variants", result["requested_order"])
        self.assertIn("exchange", result["requested_order"]["change_policies"])
        self.assertEqual(self.requests[0]["method"], "POST")
        self.assertEqual(self.requests[0]["path"], "/v1/customers/search")
        self.assertEqual(self.requests[0]["body"], {"email": self.case["email"]})
        self.assertEqual(self.writes(), [])

    def test_wrong_claim_never_reads_the_optional_order(self):
        self.build()
        with self.assertRaisesRegex(Exception, "verification"):
            self.kit.verify_customer(email=self.case["email"], claimed_customer_id="unmatched_claim",
                order_id=self.case["order_id"], include_product_variants=True)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["path"], "/v1/customers/search")

    def test_optional_order_failure_preserves_verified_identity(self):
        self.build()
        result = self.kit.verify_customer(email=self.case["email"], order_id="not-an-owned-order",
            include_product_variants=True)
        self.assertEqual(result["customer_id"], self.case["customer_id"])
        self.assertEqual(result["requested_order_error"]["code"], "order_access_denied")
        self.assertNotIn("requested_order", result)
        self.assertFalse(any(r["path"].startswith("/v1/orders/") for r in self.requests))

    def test_verified_order_quote_and_write_replay_as_one_business_change(self):
        self.build()
        verified = self.result(self.decision(UserMessage(role="user", content="Please help exchange two items from my order. My email is " + self.case["email"]),
            "verify_customer", {"email": self.case["email"], "claimed_customer_id": None,
                "order_id": self.case["order_id"], "include_product_variants": True}))
        self.decision(verified, text="I verified your account and read the order and available product variants. Which replacements would you like?")
        quote = self.result(self.decision(UserMessage(role="user", content="Please use these two chosen variants and show me the full difference and settlement."),
            "prepare_change", {"kind": "exchange", "parameters": self.params}))
        self.decision(quote, text="Here is the complete proposed change and settlement. It has not been submitted.")
        receipt = self.result(self.decision(UserMessage(role="user", content="This is the full list and exact settlement I approve. Please submit it."),
            "execute_exchange", {"proposal_id": "proposal-1"}))
        final = self.decision(receipt, text="Your exchange request has been registered. The receipt confirms the requested exchange and difference.")
        self.assertFalse(final.tool_calls)
        self.assertEqual(self.state["tool_rounds"], 3)
        self.assertEqual(len(self.writes()), 1)
        restored = self.agent.get_init_state(deepcopy(self.state["history"]))
        self.assertEqual(restored["submissions"], self.state["submissions"])
        fresh, replay_runtime, _ = reference_kit()
        Environment("retail_plus", policy="", tools=fresh).set_state(
            None, None, [decode(event) for event in self.state["history"]])
        self.assertEqual(replay_runtime.environment.get_db_hash(), self.runtime.environment.get_db_hash())
