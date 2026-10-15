"""P6 serial dependencies, partial outcomes, bounded inspection and handoff."""
import json
import unittest
from copy import deepcopy
import test_confirmation as confirmation
from test_agent_protocol import call, calls_reply, text_reply, user
from fixtures.write_backend import WriteBackend
from tools import Tools
from support_agent.domain.identity import ReadError
from support_agent.adapters.conversation_api import TRANSFER_NOTICE
from tau2.hyper.client_api import ClientAPI, ClientAPIContext


class OrchestrationTests(unittest.TestCase):
    setUp = confirmation.ConfirmationTests.setUp
    decision = confirmation.ConfirmationTests.decision
    host = confirmation.ConfirmationTests.host
    prepared = confirmation.ConfirmationTests.prepared
    reviewed = confirmation.ConfirmationTests.reviewed

    def requests(self):
        return [{"kind": "modify_items", "parameters": {"order_id": "#P0-PENDING", "replacements": [{"existing_item_id": "p0_shirt_s_blue", "replacement_item_id": "p0_shirt_m_blue"}], "payment_method_id": "p0_card", "preserve_options": ["color"]}},
                {"kind": "order_address", "parameters": {"order_id": "#P0-PENDING", "address": self.address}}]

    def next_step(self, plan):
        p, _ = self.host(self.decision(user("Prepare next step."), "prepare_plan_step", {"plan_id": plan["plan_id"]}))
        self.host(self.decision(user("Show complete recap."), "review_change", {"proposal_id": p["proposal_id"]}))
        return p

    def test_same_order_dependencies_execute_serially_using_current_facts(self):
        plan, _ = self.host(self.decision(user("Change items and address."), "plan_changes", {"requests": self.requests()}))
        self.assertEqual([s["kind"] for s in plan["steps"]], ["order_address", "modify_items"])
        one = self.next_step(plan)
        self.host(self.decision(user("I approve that address."), "execute_order_address", {"proposal_id": one["proposal_id"]}))
        two = self.next_step(plan)
        self.assertEqual(two["facts"]["snapshot"]["order"]["shipping_address"], self.address)
        self.host(self.decision(user("The item list is complete; proceed."), "execute_item_modification", {"proposal_id": two["proposal_id"]}))
        self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.backend.writes()], ["shipping-address", "item-modifications"])
        self.assertEqual([s["state"] for s in self.kit.get_service_status()["plans"][0]["steps"]], ["completed", "completed"])
        with self.assertRaisesRegex(ReadError, "plan_already_complete"):
            self.kit.prepare_plan_step(plan["plan_id"])

    def test_payment_dependency_precedes_modification_and_cancel_conflicts(self):
        rows = self.requests() + [{"kind": "payment", "parameters": {"order_id": "#P0-PENDING", "payment_method_id": "p0_paypal"}}]
        plan = self.kit.plan_changes(rows)
        self.assertEqual([s["kind"] for s in plan["steps"]], ["order_address", "payment", "modify_items"])
        for conflicting in (rows + [{"kind": "cancel", "parameters": {"order_id": "#P0-PENDING", "reason": "no longer needed"}}], rows + [rows[0]]):
            with self.assertRaises(ReadError):
                self.kit.plan_changes(conflicting)
        self.assertEqual(self.backend.writes(), [])

    def test_partial_success_failed_second_step_does_not_redo_first(self):
        plan = self.kit.plan_changes(self.requests())
        one = self.kit.prepare_plan_step(plan["plan_id"])
        self.kit.execute_order_address(one["proposal_id"])
        two = self.kit.prepare_plan_step(plan["plan_id"])
        self.backend.overrides[("POST", "/v1/orders/%23P0-PENDING/item-modifications")] = WriteBackend.response({"error": {"code": "business_rule_violation", "message": "declined"}}, 422)
        self.assertEqual(self.kit.execute_item_modification(two["proposal_id"])["state"], "failed")
        with self.assertRaisesRegex(ReadError, "previous_plan_step_not_complete"):
            self.kit.prepare_plan_step(plan["plan_id"])
        status = self.kit.get_service_status()
        self.assertEqual([s["state"] for s in status["plans"][0]["steps"]], ["completed", "failed"])
        self.assertEqual(self.backend.records["orders"][0]["shipping_address"], self.address)
        self.assertEqual(len(self.backend.writes()), 2)
        self.kit.abandon_plan(plan["plan_id"])
        self.assertEqual(self.kit.get_service_status()["plans"][0]["steps"][0]["state"], "completed")

    def test_changed_target_abandons_pending_plan_without_rollback(self):
        plan = self.kit.plan_changes(self.requests())
        one = self.kit.prepare_plan_step(plan["plan_id"])
        self.kit.execute_order_address(one["proposal_id"])
        abandoned = self.kit.abandon_plan(plan["plan_id"])
        self.assertEqual([s["state"] for s in abandoned["steps"]], ["completed", "paused"])
        with self.assertRaises(ReadError):
            self.kit.prepare_plan_step(plan["plan_id"])
        self.assertEqual(len(self.backend.writes()), 1)

    def test_unknown_write_observations_never_clear_unknown_or_allow_retry(self):
        p = self.reviewed()
        self.backend.overrides[("PUT", "/v1/customers/p0_customer_a/default-shipping-address")] = TimeoutError("unavailable")
        self.kit.execute_default_address(p["proposal_id"])
        for count in range(1, 4):
            observed = self.kit.inspect_change_result(p["proposal_id"])
            self.assertEqual(observed["state"], "unknown")
            self.assertEqual(observed["inspection_number"], count)
        before = len(self.backend.calls)
        with self.assertRaisesRegex(ReadError, "inspection_limit"):
            self.kit.inspect_change_result(p["proposal_id"])
        self.assertEqual(len(self.backend.calls), before)
        self.kit.execute_default_address(p["proposal_id"])
        self.assertEqual(len(self.backend.writes()), 1)

    def test_transfers_use_trusted_context_exact_notice_and_terminal_automation(self):
        p = self.reviewed()
        reply = self.decision(user("I want to talk to a real person now."), "transfer_to_human", {"summary": "Customer requests assistance with address correction.", "basis": "explicit_request"})
        out, notice = self.host(reply)
        self.assertEqual(out["state"], "accepted")
        self.assertEqual(notice.content, TRANSFER_NOTICE)
        self.assertEqual(self.backend.writes()[0]["path"], "/v1/conversations/confirm/transfers")
        before = len(self.backend.calls)
        for operation in (lambda: self.kit.get_customer_summary(), lambda: self.kit.get_owned_order("#P0-PENDING"), lambda: self.kit.verify_customer(email="alex.p0@example.test"), lambda: self.kit.execute_default_address(p["proposal_id"])):
            with self.assertRaises(ReadError):
                operation()
        self.provider.return_value = calls_reply(call("after", name="get_customer_summary", arguments={}))
        final, self.state = self.agent.generate_next_message(user("One more thing"), self.state)
        self.assertEqual(final.content, TRANSFER_NOTICE)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.kit.transfer_to_human("same", "explicit_request"), out)

    def test_all_transfer_bases_before_identity_and_invalid_basis_zero_requests(self):
        for basis in ("explicit_request", "unsupported_escalation", "policy_gap"):
            backend = WriteBackend()
            kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="before/#")))
            self.assertEqual(kit.transfer_to_human("Relevant known facts only.", basis)["state"], "accepted")
            self.assertEqual(backend.writes()[0]["path"], "/v1/conversations/before%2F%23/transfers")
        for basis in ("anger", "slow", "complex"):
            backend = WriteBackend()
            kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="invalid")))
            with self.assertRaises(ReadError):
                kit.transfer_to_human("summary", basis)
            self.assertEqual(backend.calls, [])

    def test_failed_or_unknown_handoff_never_emits_success_or_resumes_writes(self):
        for response, expected in ((WriteBackend.response({}, 503), "unknown"), (WriteBackend.response({}), "unknown"), (WriteBackend.response({}, 422), "failed")):
            self.setUp()
            p = self.reviewed()
            self.backend.overrides[("POST", "/v1/conversations/confirm/transfers")] = response
            out, text = self.host(self.decision(user("Get me a person."), "transfer_to_human", {"summary": "Requests a person.", "basis": "explicit_request"}))
            self.assertEqual(out["state"], expected)
            self.assertNotEqual(text.content, TRANSFER_NOTICE)
            with self.assertRaises(ReadError):
                self.kit.execute_default_address(p["proposal_id"])
            self.assertEqual(len(self.backend.writes()), 1)

    def test_full_tool_replay_rebuilds_identity_proposals_plans_and_receipts(self):
        trajectory = [("verify_customer", {"email": "alex.p0@example.test"}),
                      ("plan_changes", {"requests": self.requests()}),
                      ("prepare_plan_step", {"plan_id": "plan-1"}),
                      ("review_change", {"proposal_id": "proposal-1"}),
                      ("execute_order_address", {"proposal_id": "proposal-1"}),
                      ("prepare_plan_step", {"plan_id": "plan-1"}),
                      ("review_change", {"proposal_id": "proposal-2"}),
                      ("execute_item_modification", {"proposal_id": "proposal-2"}),
                      ("get_service_status", {})]
        runs = []
        for _ in range(2):
            backend = WriteBackend()
            kit = Tools(ClientAPI(backend, context=ClientAPIContext(conversation_id="replay")))
            outputs = [getattr(kit, name)(**deepcopy(params)) for name, params in trajectory]
            runs.append((outputs, deepcopy(backend.records), backend.calls))
        self.assertEqual(runs[0], runs[1])

    def test_context_reset_discards_all_service_state_and_requires_new_identity(self):
        p = self.reviewed()
        self.kit.client_api._set_context(ClientAPIContext(conversation_id="new-conversation"))
        status = self.kit.get_service_status()
        self.assertEqual(status["changes"], [])
        self.assertEqual(status["plans"], [])
        self.assertFalse(status["human_requested"])
        with self.assertRaises(ReadError):
            self.kit.execute_default_address(p["proposal_id"])

    def test_transfer_cannot_share_a_batch_with_other_actions(self):
        self.provider.return_value = calls_reply(call("transfer", name="transfer_to_human", arguments={"summary": "Requested person", "basis": "explicit_request"}),
                                                 call("read", name="get_customer_summary", arguments={}))
        reply, state = self.agent.generate_next_message(user("Get me a person"), self.state)
        self.assertFalse(reply.tool_calls)
        self.assertEqual(self.backend.writes(), [])
