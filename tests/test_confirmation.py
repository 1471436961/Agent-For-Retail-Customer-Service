"""Real host/Agent/tool protocol with synthetic semantic decisions and backend."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from agent import create_agent
from tools import Tools
from fixtures.write_backend import WriteBackend
from support_agent.domain.identity import ReadError
from support_agent.domain.presentation import review_text
from test_agent_protocol import call, calls_reply, text_reply, user, results, tool_result
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.backend = WriteBackend()
        self.kit = Tools(ClientAPI(self.backend, context=ClientAPIContext(conversation_id="confirm")))
        verified = self.kit.verify_customer(email="alex.p0@example.test")
        self.tools = self.kit.get_tools()
        context = build_agent_context(domain="retail_plus", tools=list(self.tools.values()),
                                      resource_root=Path(__file__).resolve().parents[1] / "agent",
                                      model_configs=[{"model": "offline_model", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(context):
            self.agent = create_agent()
        self.agent.gateway.live_actions = self.agent.gateway.compatibility_actions
        # Agent and toolkit must both have genuine verification evidence.
        self.state = self.agent.get_init_state([
            user("My email is alex.p0@example.test."),
            calls_reply(call("setup-verified", name="verify_customer", arguments={
                "email": "alex.p0@example.test", "claimed_customer_id": None})),
            results(tool_result("setup-verified", json.dumps(verified))),
        ])
        self.provider = patch("tau2.hyper.agent_context.generate").start()
        self.addCleanup(patch.stopall)
        self.sequence = 0
        self.address = {"address_line_1": "90 New Road", "city": "Boston", "region": "MA", "country": "USA", "postal_code": "02101"}

    def decision(self, message, name, arguments):
        self.sequence += 1
        self.provider.return_value = calls_reply(call(str(self.sequence), name=name, arguments=arguments))
        reply, self.state = self.agent.generate_next_message(message, self.state)
        return reply

    def host(self, reply, final=None):
        self.assertTrue(reply.tool_calls, self.state)
        c = reply.tool_calls[0]
        try:
            out, error = self.tools[c.name](**c.arguments), False
            content = json.dumps(out)
        except ReadError as exc:
            out, error, content = None, True, str(exc)
        if final is None:
            proposal = out.get("review", out) if isinstance(out, dict) else None
            final = review_text(proposal) if proposal and "facts" in proposal else "Tool result received."
        # Supply the controlled model's own complete quote; the Agent must not
        # append another renderer/confirmation on its behalf.
        self.provider.return_value = text_reply(final)
        next_reply, self.state = self.agent.generate_next_message(results(tool_result(c.id, content, error)), self.state)
        return out, next_reply

    def prepared(self, kind="default_address", params=None):
        p, _ = self.host(self.decision(user("Please change my address."), "prepare_change", {"kind": kind, "parameters": params or {"address": self.address}}))
        return p

    def reviewed(self, kind="default_address", params=None):
        p = self.prepared(kind, params)
        _, reply = self.host(self.decision(user("Please show the proposal."), "review_change", {"proposal_id": p["proposal_id"]}))
        self.assertIn("90 New Road" if "address" in kind else p["facts"]["body"]["reason"], reply.content)
        self.assertIn("Please confirm", reply.content)
        return p

    def test_unreviewed_forged_or_no_later_user_input_never_reaches_host(self):
        pending = self.decision(user("Prepare this address."), "prepare_change",
                                {"kind": "default_address", "parameters": {"address": self.address}})
        c = pending.tool_calls[0]
        p = self.tools[c.name](**c.arguments)
        self.provider.return_value = calls_reply(call("too-early", name="execute_default_address", arguments={"proposal_id": p["proposal_id"]}))
        reply, self.state = self.agent.generate_next_message(results(tool_result(c.id, json.dumps(p))), self.state)
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.writes(), [])
        self.setUp()
        p = self.prepared()
        review_call = self.decision(user("show it"), "review_change", {"proposal_id": p["proposal_id"]})
        c = review_call.tool_calls[0]
        fake = self.kit.review_change(p["proposal_id"])
        fake["review"]["facts"]["body"]["city"] = "tampered"
        self.provider.return_value = calls_reply(call("forged", name="execute_default_address", arguments={"proposal_id": p["proposal_id"]}))
        reply, self.state = self.agent.generate_next_message(results(tool_result(c.id, json.dumps(fake))), self.state)
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.writes(), [])

    def test_actual_recap_then_new_user_allows_exact_default_address_once(self):
        p = self.reviewed()
        out, _ = self.host(self.decision(user("That is the address I want; proceed."), "execute_default_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
        self.assertEqual(self.backend.records["customers"][0]["default_shipping_address"], self.address)
        self.assertNotEqual(self.backend.records["orders"][0]["shipping_address"], self.address)
        self.assertEqual(self.kit.execute_default_address(p["proposal_id"]), out)
        self.assertEqual(len(self.backend.writes()), 1)

    def test_order_address_does_not_change_default(self):
        params = {"order_id": "#P0-PENDING", "address": self.address}
        p = self.reviewed("order_address", params)
        out, _ = self.host(self.decision(user("Approved."), "execute_order_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
        self.assertEqual(self.backend.records["orders"][0]["shipping_address"], self.address)
        self.assertNotEqual(self.backend.records["customers"][0]["default_shipping_address"], self.address)

    def test_pause_revise_question_and_conditional_decisions_do_not_write(self):
        # The mock supplies semantic decisions, not a keyword classifier.
        for text in ("等一下", "yes, but only if you can change the city first", "yes to the explanation, what is next?", "not that address"):
            self.setUp()
            p = self.reviewed()
            self.host(self.decision(user(text), "invalidate_change", {"proposal_id": p["proposal_id"]}))
            with self.assertRaises(ReadError):
                self.kit.execute_default_address(p["proposal_id"])
            self.assertEqual(self.backend.writes(), [])

    def test_replacement_proposal_cannot_use_old_review(self):
        p = self.reviewed()
        new_params = {"address": {**self.address, "city": "Cambridge"}}
        newer, _ = self.host(self.decision(user("Use Cambridge instead."), "prepare_change", {"kind": "default_address", "parameters": new_params}))
        reply = self.decision(user("yes"), "execute_default_address", {"proposal_id": p["proposal_id"]})
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.writes(), [])
        self.assertNotEqual(newer["proposal_id"], p["proposal_id"])

    def test_changed_backend_facts_or_status_require_new_confirmation(self):
        p = self.reviewed("order_address", {"order_id": "#P0-PENDING", "address": self.address})
        self.backend.records["orders"][0]["status"] = "processed"
        _, reply = self.host(self.decision(user("confirmed"), "execute_order_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(self.backend.writes(), [])
        self.assertIn("Tool result", reply.content)
        self.setUp()
        p = self.reviewed()
        self.backend.records["customers"][0]["default_shipping_address"]["city"] = "Changed"
        with self.assertRaisesRegex(ReadError, "facts_changed"):
            self.kit.execute_default_address(p["proposal_id"])
        self.assertEqual(self.backend.writes(), [])

    def test_unrelated_profile_changes_do_not_invalidate_confirmed_address(self):
        p = self.reviewed()
        customer = self.backend.records["customers"][0]
        customer["order_ids"].append("#UNRELATED-NEW-ORDER")
        gift = next(m for m in customer["payment_methods"] if m["source"] == "gift_card")
        gift["balance"] += 1
        out, _ = self.host(self.decision(user("I confirm this address."), "execute_default_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
        self.assertEqual(len(self.backend.writes()), 1)

    def test_profile_default_address_change_does_not_invalidate_order_address(self):
        p = self.reviewed("order_address", {"order_id": "#P0-PENDING", "address": self.address})
        self.backend.records["customers"][0]["default_shipping_address"]["city"] = "Unrelated profile address"
        out, _ = self.host(self.decision(user("I confirm the order address."), "execute_order_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
        self.assertEqual(len(self.backend.writes()), 1)

    def test_relevant_order_address_change_still_invalidates_confirmation(self):
        p = self.reviewed("order_address", {"order_id": "#P0-PENDING", "address": self.address})
        self.backend.records["orders"][0]["shipping_address"]["city"] = "Changed by another channel"
        with self.assertRaisesRegex(ReadError, "facts_changed"):
            self.kit.execute_order_address(p["proposal_id"])
        self.assertEqual(self.backend.writes(), [])

    def test_cancel_whole_order_and_keep_individual_original_refunds(self):
        p = self.reviewed("cancel", {"order_id": "#P0-PENDING", "reason": "ordered by mistake"})
        out, _ = self.host(self.decision(user("Please cancel that entire order."), "execute_cancellation", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
        self.assertEqual(self.backend.records["orders"][0]["status"], "cancelled")
        self.assertEqual(out["receipt"]["payments"][-1], {"transaction_type": "refund", "amount": 50.0, "payment_method_id": "p0_card"})
        self.assertEqual(self.backend.writes()[0]["path"], "/v1/orders/%23P0-PENDING/cancellations")

    def test_rejection_timeout_malformed_receipt_and_no_repeated_write(self):
        for value, expected in ((WriteBackend.response({"error": {"code": "resource_conflict", "message": "secret"}}, 409), "failed"),
                                (TimeoutError("private secret"), "unknown"), (WriteBackend.response({}), "unknown")):
            self.setUp()
            p = self.reviewed()
            self.backend.overrides[("PUT", "/v1/customers/p0_customer_a/default-shipping-address")] = value
            out = self.kit.execute_default_address(p["proposal_id"])
            self.assertEqual(out["state"], expected)
            self.assertNotIn("secret", json.dumps(out))
            self.assertEqual(self.kit.execute_default_address(p["proposal_id"]), out)
            self.assertEqual(len(self.backend.writes()), 1)

    def test_write_batches_and_wrong_executor_are_rejected(self):
        p = self.reviewed()
        self.provider.return_value = calls_reply(call("w", name="execute_default_address", arguments={"proposal_id": p["proposal_id"]}),
                                                 call("r", name="get_customer_summary", arguments={}))
        reply, self.state = self.agent.generate_next_message(user("confirmed"), self.state)
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.writes(), [])
        with self.assertRaisesRegex(ReadError, "wrong_execution_kind"):
            self.kit.execute_cancellation(p["proposal_id"])

    def test_json_resume_restores_display_evidence(self):
        p = self.reviewed()
        restored = self.agent.get_init_state(self.state["history"])
        self.assertEqual(restored["quote_response_turns"], self.state["quote_response_turns"])
        self.state = json.loads(json.dumps(restored))
        out, _ = self.host(self.decision(user("Proceed."), "execute_default_address", {"proposal_id": p["proposal_id"]}))
        self.assertEqual(out["state"], "completed")
