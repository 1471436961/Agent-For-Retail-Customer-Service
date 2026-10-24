"""Public contracts through the actual tau2 conversion and synthetic host.

Only the provider transport is simulated; semantic comprehension is not scored.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent import create_agent
from tools import Tools
from fixtures.write_backend import WriteBackend
from support_agent.adapters.change_tools import EXECUTE_ACTIONS
from support_agent.domain.identity import ReadError
from support_agent.gateway import AgentLimits
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage, UserMessage
from tau2.hyper.agent_context import activate_agent_context, build_agent_context
from tau2.hyper.client_api import ClientAPI, ClientAPIContext
import test_gateway_wire as wire


class ContractBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.backend = WriteBackend()
        self.kit = Tools(ClientAPI(self.backend, context=ClientAPIContext(conversation_id="contract_boundary")))
        self.agent = self.build()
        self.provider = self.enterContext(patch("tau2.utils.llm_utils.completion"))
        self.enterContext(patch("tau2.utils.llm_utils.get_response_cost", return_value=0))
        self.sequence = 0

    def build(self, models=None):
        context = build_agent_context(domain="retail_plus", tools=list(self.kit.get_tools().values()),
            resource_root=ROOT / "agent", model_configs=models or [{
                "model": "openai/contract-test-model", "constraints": {"max_tokens": 1024}}])
        with activate_agent_context(context):
            return create_agent()

    def decision(self, state, text, name, arguments, narration=None):
        self.sequence += 1
        self.provider.side_effect = None
        self.provider.return_value = wire.completion(content=narration, calls=[{
            "id": f"contract-{self.sequence}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}])
        return self.agent.generate_next_message(UserMessage(role="user", content=text), state)

    def execute_host(self, reply):
        self.assertTrue(reply.tool_calls)
        results, outputs = [], []
        for call in reply.tool_calls:
            output = self.kit.get_tools()[call.name](**call.arguments)
            outputs.append(output)
            results.append(ToolMessage(role="tool", id=call.id, content=json.dumps(output)))
        return MultiToolMessage(role="tool", tool_messages=results), outputs

    def consume_host(self, reply, state):
        result, outputs = self.execute_host(reply)
        self.provider.return_value = wire.completion(content="The tool result is available.")
        response, state = self.agent.generate_next_message(result, state)
        return outputs, response, state

    def verified_state(self):
        reply, state = self.decision(self.agent.get_init_state(), "My account email is alex.p0@example.test.",
                                     "verify_customer", {"email": "alex.p0@example.test"})
        _, _, state = self.consume_host(reply, state)
        return state

    def reviewed_cancellation(self):
        state = self.verified_state()
        reply, state = self.decision(state, "Cancel the entire pending order: no longer needed.",
            "prepare_change", {"kind": "cancel", "parameters": {
                "order_id": "#P0-PENDING", "reason": "no longer needed"}})
        outputs, _, state = self.consume_host(reply, state)
        proposal = outputs[0]
        reply, state = self.decision(state, "Show the complete cancellation proposal.",
            "review_change", {"proposal_id": proposal["proposal_id"]})
        _, recap, state = self.consume_host(reply, state)
        self.assertIn("Please confirm", recap.content)
        self.assertEqual(self.backend.writes(), [])
        return proposal, state

    def test_allowed_duplicate_model_configs_are_disambiguated_by_choices(self):
        agent = self.build(models=[
            {"model": "openai/contract-test-model", "constraints": {
                "max_tokens": 111, "reasoning_effort": {"one_of": ["low", "medium"]}}},
            {"model": "openai/contract-test-model", "constraints": {
                "max_tokens": 222, "reasoning_effort": {"one_of": ["low", "high"]}}},
        ])
        self.assertEqual(agent.gateway.options, {"reasoning_effort": "medium"})
        self.provider.return_value = wire.completion(content="Please identify your account.")
        _, state = agent.generate_next_message(UserMessage(role="user", content="Hello"), agent.get_init_state())
        self.assertIsNone(state["halt_reason"])
        self.assertEqual(self.provider.call_args.kwargs["max_tokens"], 111)
        self.assertEqual(self.provider.call_args.kwargs["reasoning_effort"], "medium")
        self.assertNotIn("max_tokens", agent.gateway.options)
        self.provider.assert_called_once()

    def test_unusable_first_model_does_not_hide_another_allowed_configuration(self):
        agent = self.build(models=[
            {"model": "openai/ambiguous-model", "constraints": {"max_tokens": 11}},
            {"model": "openai/ambiguous-model", "constraints": {"max_tokens": 22}},
            {"model": "openai/contract-test-model", "constraints": {"max_tokens": 33}},
        ])
        self.assertEqual(agent.gateway.model, "openai/contract-test-model")
        self.assertEqual(agent.gateway.options, {})
        self.provider.return_value = wire.completion(content="Please identify your account.")
        agent.generate_next_message(UserMessage(role="user", content="Hello"), agent.get_init_state())
        self.assertEqual(self.provider.call_args.kwargs["max_tokens"], 33)
        self.provider.assert_called_once()

    def test_provider_narration_is_not_displayed_before_tool_execution(self):
        reply, state = self.decision(self.agent.get_init_state(), "Help identify my account.",
            "verify_customer", {"email": "alex.p0@example.test"}, narration="The account lookup is already complete.")
        self.assertIsNone(reply.content)
        self.assertEqual(reply.tool_calls[0].name, "verify_customer")
        self.assertIsNone(state["halt_reason"])
        self.assertIsNone(state["history"][-1].get("content"))
        self.assertEqual(self.backend.calls, [])

    def test_narrated_invalid_call_is_still_rejected_before_host(self):
        reply, state = self.decision(self.agent.get_init_state(), "Please help.",
            "verify_customer", {"email": 42}, narration="I will verify this account.")
        self.assertFalse(reply.tool_calls)
        self.assertEqual(state["halt_reason"], "invalid_model_reply")
        self.assertEqual(self.backend.calls, [])

    def test_historical_tool_narration_does_not_count_as_a_displayed_change_review(self):
        proposal, state = self.reviewed_cancellation()
        history = state["history"][:-1]
        narration = state["history"][-1]["content"]
        for event in history:
            if event.get("role") == "assistant" and event.get("content") and not event.get("tool_calls"):
                event["content"] = "Preparatory discussion; no change recap displayed."
        history.extend([AssistantMessage(role="assistant", content=narration, tool_calls=[
            ToolCall(id="historical-catalog", name="list_products", arguments={})]).model_dump(mode="json"),
            ToolMessage(role="tool", id="historical-catalog", content='{"products":[]}').model_dump(mode="json")])
        restored = self.agent.get_init_state(history)
        self.assertEqual(restored["reviewed"], {})
        self.assertEqual(restored["review_to_display"]["proposal_id"], proposal["proposal_id"])
        calls = self.provider.call_count
        self.provider.return_value = wire.completion(content="Let me explain the proposed cancellation.")
        reply, restored = self.agent.generate_next_message(
            UserMessage(role="user", content="Please continue."), restored)
        self.assertTrue(reply.content.endswith(narration[narration.index("Prepared change"):]))
        self.assertEqual(self.provider.call_count, calls + 1)
        self.assertIn(proposal["proposal_id"], restored["reviewed"])
        self.assertEqual(self.backend.writes(), [])

    def test_transfer_metadata_matches_the_mutating_openapi_operation(self):
        spec = json.loads((ROOT / "materials/client_api/openapi.yaml").read_text(encoding="utf-8"))
        operation = spec["paths"]["/v1/conversations/{conversation_id}/transfers"]["post"]
        tool = self.kit.get_tools()["transfer_to_human"]
        self.assertEqual(tool.info["tool_type"], "generic")
        self.assertEqual(tool.info["mutates_state"], operation["x-api-mutates-state"])
        mutators = {name for name, tool in self.kit.get_tools().items() if tool.info["mutates_state"]}
        self.assertEqual(mutators, {*EXECUTE_ACTIONS, "submit_change", "transfer_to_human"})

    def test_bad_nested_plan_targets_fail_before_any_operation(self):
        self.kit.verify_customer(email="alex.p0@example.test")
        before = len(self.backend.calls)
        for target in (None, "", [], {}, 42, True):
            with self.subTest(target=target), self.assertRaises(ReadError):
                self.kit.plan_changes([{"kind": "cancel", "parameters": {
                    "order_id": target, "reason": "no longer needed"}}])
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.backend.writes(), [])

    def test_nonstring_cancellation_reason_is_a_controlled_failure(self):
        self.kit.verify_customer(email="alex.p0@example.test")
        for reason in (None, [], {}, 42, True):
            with self.subTest(reason=reason), self.assertRaises(ReadError) as caught:
                self.kit.prepare_change("cancel", {"order_id": "#P0-PENDING", "reason": reason})
            self.assertEqual(caught.exception.code, "unsupported_cancellation_reason")
        self.assertEqual(self.backend.writes(), [])

    def test_actual_write_receipt_is_retained_when_model_service_fails(self):
        proposal, state = self.reviewed_cancellation()
        reply, state = self.decision(state, "I approve this exact whole-order cancellation.",
            "execute_cancellation", {"proposal_id": proposal["proposal_id"]})
        result, outputs = self.execute_host(reply)
        self.assertEqual(outputs[0]["state"], "completed")
        self.provider.side_effect = RuntimeError("Offline provider unavailable")
        response, state = self.agent.generate_next_message(result, state)
        self.assertEqual(state["halt_reason"], "gateway_failure")
        self.assertIn('"state": "completed"', response.content)
        self.assertIn("original_charge_refunds", response.content)
        self.assertIn("3–6 working days", response.content)
        self.assertEqual(state["receipt_facts_to_display"], [])
        self.assertEqual(state["history"][-1]["content"], response.content)
        calls = self.provider.call_count
        self.agent.generate_next_message(UserMessage(role="user", content="Try again."), state)
        self.assertEqual(self.provider.call_count, calls)
        self.assertEqual(len(self.backend.writes()), 1)

    def test_actual_write_receipt_is_retained_at_model_budget_boundary(self):
        proposal, state = self.reviewed_cancellation()
        reply, state = self.decision(state, "I approve this exact whole-order cancellation.",
            "execute_cancellation", {"proposal_id": proposal["proposal_id"]})
        result, _ = self.execute_host(reply)
        self.agent.gateway.limits = AgentLimits(max_model_calls=state["model_calls"])
        calls = self.provider.call_count
        response, state = self.agent.generate_next_message(result, state)
        self.assertEqual(state["halt_reason"], "model_call_limit")
        self.assertIn('"state": "completed"', response.content)
        self.assertEqual(self.provider.call_count, calls)
        self.assertEqual(len(self.backend.writes()), 1)


if __name__ == "__main__":
    unittest.main()
