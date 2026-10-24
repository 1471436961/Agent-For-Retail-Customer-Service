"""A combined read saves model/tool turns without changing the business API."""
import json
import unittest

import test_task_completion as completion
from support_agent.domain.changes import prepare
from support_agent.domain.observations import ObservedFacts
from tau2.data_model.message import UserMessage


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
        self.assertEqual({key: value for key, value in combined.items() if key != "product_variants"}, basic)
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
