"""Observe stages through real host events, not customer phrase classification."""
import json
import unittest
from copy import deepcopy

import test_task_completion as completion
from support_agent.state import SessionState, receive
from support_agent.workflow import Stage, stage_of, prepared_kinds_with_later_input
from tau2.data_model.message import UserMessage


class WorkflowTests(unittest.TestCase):
    build = completion.TaskCompletionTests.build
    decision = completion.TaskCompletionTests.decision
    result = completion.TaskCompletionTests.result
    prepare = completion.TaskCompletionTests.prepare
    writes = completion.TaskCompletionTests.writes

    def stage(self):
        return stage_of(SessionState.model_validate(self.state))

    def test_quote_question_and_approval_are_distinct_from_execution(self):
        self.build()
        self.assertEqual(self.stage(), Stage.IDENTITY_REQUIRED)
        prepared = self.prepare("exchange")
        self.assertEqual(self.stage(), Stage.WAITING_TOOL_RESULT)
        current = SessionState.model_validate(self.state)
        receive(current, prepared)
        self.assertEqual(stage_of(current), Stage.QUOTE_READY)
        self.assertEqual(prepared_kinds_with_later_input(current), set())
        self.decision(prepared, text="These are the complete exchange choices and settlement. Do you approve submission?")
        self.assertEqual(self.stage(), Stage.WAITING_CUSTOMER)
        self.assertEqual(self.writes(), [])
        # Both phrases produce the same chronological event. The state must not
        # pretend to decide their opposite meanings; those decisions are LLM-owned.
        for text in ("Submit the exact quote now.", "Do not submit; I have a question."):
            copied = SessionState.model_validate(deepcopy(self.state))
            receive(copied, UserMessage(role="user", content=text))
            self.assertEqual(stage_of(copied), Stage.INTERPRETING_CUSTOMER)
            self.assertEqual(prepared_kinds_with_later_input(copied), {"exchange"})
            self.assertEqual(copied.proposals["proposal-1"]["state"], "prepared")
        self.decision(UserMessage(role="user", content="Please explain the return process first; do not submit."),
                      text="Preparation did not submit anything. The policy provides return instructions by email.")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.stage(), Stage.INTERPRETING_CUSTOMER)
        receipt = self.result(self.decision(UserMessage(role="user", content="I approve the exact complete quote; submit it."),
            "execute_exchange", {"proposal_id": "proposal-1"}))
        current = SessionState.model_validate(self.state)
        receive(current, receipt)
        self.assertEqual(stage_of(current), Stage.OUTCOME_AVAILABLE)
        self.assertEqual(json.loads(receipt.content)["state"], "accepted")
        self.assertEqual(len(self.writes()), 1)
        final = "The exchange request was accepted. That does not confirm refund arrival or shipment."
        reply = self.decision(receipt, text=final)
        self.assertEqual(reply.content, final)
        self.assertEqual(self.stage(), Stage.SERVING)
        self.assertIsNone(self.state["halt_reason"])
        restored = self.agent.get_init_state(self.state["history"])
        self.assertEqual(stage_of(SessionState.model_validate(restored)), Stage.SERVING)

    def test_pending_unknown_and_terminal_evidence_take_precedence(self):
        state = SessionState(verified_customer_id="customer", customer_profile_loaded=True,
            submissions={"attempt": {"state": "pending"}})
        self.assertEqual(stage_of(state), Stage.VERIFYING_OUTCOME)
        state.submissions["attempt"]["state"] = "unknown"
        self.assertEqual(stage_of(state), Stage.VERIFYING_OUTCOME)
        # A completed operation leaves the conversation open to other goals.
        state.submissions["attempt"]["state"] = "accepted"
        self.assertEqual(stage_of(state), Stage.SERVING)
        state.handoff = "accepted"
        self.assertEqual(stage_of(state), Stage.HANDOFF)
        state.halt_reason = "gateway_failure"
        self.assertEqual(stage_of(state), Stage.HALTED)
