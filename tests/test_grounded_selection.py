"""Selection uses actual records; errors are corrected before host execution.

Only provider choices are controlled. REST writes and database comparisons use
the real SDK; these tests do not claim real-model semantic competence.
"""
import json
import unittest
from copy import deepcopy
from urllib.parse import quote

import test_task_completion as completion
from test_reference_runtime import reference_kit, apply_native_reference
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from support_agent.state import SessionState
from support_agent.domain.changes import prepare
from support_agent.domain.observations import ObservedFacts
from support_agent.domain.identity import ReadError
from fixtures.p2_backend import SyntheticBackend
from tools import Tools
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class GroundedSelectionTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    writes = completion.TaskCompletionTests.writes

    def test_short_customer_reference_recovers_to_exact_id_without_scanning_orders(self):
        self.build("exchange", compatibility=False)
        short = self.case["order_id"][1:]
        received = self.result(self.decision(UserMessage(role="user", content="My order reference is " + short),
            "verify_customer", {"email":self.case["email"], "claimed_customer_id":None, "order_id":short}))
        profile = json.loads(received.content)
        self.assertEqual(profile["requested_order_error"]["code"], "order_access_denied")
        self.assertFalse(any(r["path"].startswith("/v1/orders/") for r in self.requests))
        # An invalid proposed parameter is NOT sent to the host or rewritten.
        bad = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="bad-order", name="get_owned_order", arguments={"order_id":short})])
        corrected = AssistantMessage(role="assistant", tool_calls=[ToolCall(id="exact-order", name="get_owned_order", arguments={"order_id":self.case["order_id"]})])
        self.model.side_effect = [bad, corrected]
        reply, self.state = self.agent.generate_next_message(received, self.state)
        self.assertEqual(bad.tool_calls[0].arguments["order_id"], short)
        self.assertEqual(reply.tool_calls[0].id, "exact-order")
        self.assertIn("order_id must be selected exactly", self.model.call_args.kwargs["messages"][-1].content)
        order = json.loads(self.result(reply).content)
        self.assertEqual(order["order_id"], self.case["order_id"])
        self.assertEqual([r["path"] for r in self.requests if r["path"].startswith("/v1/orders/")],
                         ["/v1/orders/" + quote(self.case["order_id"], safe="")])
        restored = self.agent.get_init_state(deepcopy(self.state["history"]))
        self.assertEqual(restored["owned_order_ids"], profile["order_ids"])
        self.assertEqual(self.writes(), [])

    def test_approved_natural_recap_corrects_bad_id_and_reaches_one_real_write(self):
        self.build("exchange", compatibility=False)
        received = self.result(self.decision(UserMessage(role="user", content="Please exchange my items."),
            "verify_customer", {"email":self.case["email"], "claimed_customer_id":None, "order_id":self.case["order_id"]}))
        order = json.loads(received.content)["requested_order"]
        selected = {r["existing_item_id"] for r in self.params["replacements"]}
        ids = list(dict.fromkeys(r["product_id"] for r in order["items"] if r["item_id"] in selected))
        received = self.result(self.decision(received, "read_products", {"product_ids":ids}))
        facts = prepare(ObservedFacts(self.kit), "exchange", self.params)
        self.decision(received, text="The complete requested exchange is " + json.dumps(facts["body"]) +
                      " with the actual calculations " + json.dumps(facts["calculations"]) + ". Do you approve this exact full submission?")
        params = deepcopy(self.params)
        params["order_id"] = self.case["order_id"][1:]
        self.model.side_effect = [
            AssistantMessage(role="assistant", tool_calls=[ToolCall(id="bad-quote", name="prepare_change", arguments={"kind":"exchange", "parameters":params})]),
            AssistantMessage(role="assistant", tool_calls=[ToolCall(id="approved-submit", name="submit_change", arguments={"kind":"exchange", "parameters":self.params})]),
        ]
        reply, self.state = self.agent.generate_next_message(UserMessage(role="user", content="I approve that exact complete exchange and settlement. Submit it."), self.state)
        outcome = json.loads(self.result(reply).content)
        self.assertEqual(outcome["state"], "accepted")
        self.assertEqual(len(self.writes()), 1)
        self.assertTrue(self.writes()[0]["path"].endswith("/exchanges"))
        self.assertFalse(any(c["name"] == "prepare_change" for m in self.state["history"] for c in m.get("tool_calls", [])))
        _, native, _ = reference_kit()
        apply_native_reference(native.environment.tools, "exchange", self.case["customer_id"], self.params)
        self.assertEqual(self.runtime.environment.get_db_hash(), native.environment.get_db_hash())

    def test_exact_catalog_names_avoid_a_model_round_for_the_index(self):
        backend = SyntheticBackend()
        kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="exact-catalog")))
        kit.verify_customer(email="alex.p0@example.test")
        product = backend.records["products"][0]
        summary = kit.get_catalog_summary(product_name=product["name"].swapcase())
        self.assertEqual(summary["available_variant_count"], sum(i["available"] for i in product["items"]))
        self.assertEqual(summary["products"][0]["product_id"], product["product_id"])
        batch = kit.read_products(product_names=[product["name"], product["name"]], product_ids=[product["product_id"]])
        self.assertEqual(len(batch["products"]), 1)
        before = len(backend.calls)
        with self.assertRaisesRegex(ReadError, "catalog_name_not_unique"):
            kit.get_catalog_summary(product_name="ambiguous or missing catalog name")
        self.assertEqual([r["path"] for r in backend.calls[before:]], ["/v1/catalog/products"])
        backend.records["products"].append({**deepcopy(product), "product_id":"other-catalog-record"})
        with self.assertRaisesRegex(ReadError, "catalog_name_not_unique"):
            kit.read_products(product_names=[product["name"]])

    def test_live_order_schema_contains_only_actual_verified_ids(self):
        self.build("modify_items", compatibility=False)
        received = self.result(self.decision(UserMessage(role="user", content="I need a quote only; do not submit."),
            "verify_customer", {"email":self.case["email"], "claimed_customer_id":None}))
        self.decision(received, text="What would you like to change?")
        self.decision(UserMessage(role="user", content="For now, only inspect the available choices; I am not approving a write."),
                      text="I will only inspect choices.")
        actions = {a.name:a for a in self.agent.gateway.actions_for(SessionState.model_validate(self.state))}
        ids = self.state["owned_order_ids"]
        self.assertTrue(ids)
        self.assertNotIn("get_product_variants", actions)
        self.assertNotIn("verify_customer", actions)
        for name in ("prepare_change", "submit_change"):
            definitions = actions[name].params.model_json_schema()["$defs"]
            for model in definitions.values():
                field = model.get("properties", {}).get("order_id")
                if field:
                    self.assertEqual(field.get("enum", [field.get("const")]), ids)
        self.assertEqual(self.writes(), [])

    def test_catalog_goal_and_pending_change_wait_for_consent_then_really_modify(self):
        self.build("modify_items", compatibility=False)
        native_order = self.runtime.environment.tools.db.orders[self.case["order_id"]]
        original_id = self.params["replacements"][0]["existing_item_id"]
        product_id = next(row.product_id for row in native_order.items if row.item_id == original_id)
        product_name = self.runtime.environment.tools.db.products[product_id].name
        received = self.result(self.decision(UserMessage(role="user", content="How many options are available, and please help with pending changes."),
            "verify_customer", {"email":self.case["email"], "claimed_customer_id":None}))
        received = self.result(self.decision(received, "get_catalog_summary", {"product_name":product_name}))
        count = json.loads(received.content)["available_variant_count"]
        received = self.result(self.decision(received, "list_customer_orders", {"include_details":True}))
        self.decision(received, text=f"There are {count} available variants. Which pending items and specifications would you like to change?")
        received = self.result(self.decision(UserMessage(role="user", content="Please use the selected available variant and preserve the requested options."),
            "prepare_change", {"kind":"modify_items", "parameters":self.params}))
        facts = json.loads(received.content)["facts"]
        recap = self.decision(received, text="The complete pending change is " + json.dumps(facts["body"]) +
            "; actual price calculations " + json.dumps(facts["calculations"]) + ". Do you approve submitting these exact details?")
        self.assertFalse(self.agent.is_stop(recap))
        self.assertIsNone(self.state["halt_reason"])
        self.assertEqual(self.writes(), [])
        # A confirmation question is a live conversation turn, not completion
        # and not permission to turn the read-only quote into a write.
        received = self.result(self.decision(UserMessage(role="user", content="I approve the exact full change and the stated settlement. Submit it."),
            "submit_change", {"kind":"modify_items", "parameters":self.params}))
        self.assertEqual(json.loads(received.content)["state"], "completed")
        final = self.decision(received, text=f"There are {count} available variants. The confirmed pending-item modification completed. ###STOP###")
        self.assertTrue(self.agent.is_stop(final))
        self.assertEqual(len(self.writes()), 1)
        _, native, _ = reference_kit()
        apply_native_reference(native.environment.tools, "modify_items", self.case["customer_id"], self.params)
        self.assertEqual(self.runtime.environment.get_db_hash(), native.environment.get_db_hash())
